"""Leak scanner: checks the gate's work instead of trusting it.

The scanner builds its deny-list from the RAW data, not from the gate's output,
and it does not rely on the inventory alone. A built-in floor of protected
columns applies even if someone loosens the inventory, so one edit to the YAML
cannot defeat both the gate and the scan.

What it checks, in every file under each target (and every table in the DuckDB
warehouse), case-insensitively:
  - raw values of person-level columns: names, emails, phones, notes, and the
    original contact and rep IDs, plus common name variants ("last, first",
    "first.last") and email local parts
  - email and phone number patterns
  - for the llm target only: raw account names and domains (confidential
    business data), and any column not allowed to the llm destination

A file it cannot read is a finding, not a skip.

Exit 0: clean. Exit 1: findings, each listed.

Usage: python privacy/scan.py [targets...]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
import sys
from pathlib import Path

import yaml

EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b|\b\d{3}[.-]\d{4}\b")
MIN_LEN = 6  # shorter values (bare first names) cause too many false positives

# always protected, regardless of what the inventory says
PROTECTED_PERSON = {"first_name", "last_name", "email", "phone", "next_step_notes",
                    "contact_id", "primary_contact_id", "rep_id", "owner_rep_id"}
PROTECTED_FROM_LLM = {"account_name", "domain", "title", "contact_id", "primary_contact_id"}
PERSON_CLASSES = {"direct_identifier", "free_text", "pseudonymous_key"}
TEXT_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl", ".txt", ".md", ".yml", ".yaml", ""}


def raw_rows(raw: Path, table: str):
    path = raw / f"{table}.csv"
    if path.exists():
        with path.open(newline="") as f:
            yield from csv.DictReader(f)


def deny_lists(raw: Path, inv: dict) -> tuple[set[str], set[str]]:
    """(values banned everywhere, values banned from the llm target)."""
    person: set[str] = set()
    business: set[str] = set()
    for table, spec in inv["tables"].items():
        person_cols = {c for c, s in spec.items() if s["class"] in PERSON_CLASSES} | (PROTECTED_PERSON & set(spec))
        biz_cols = ({c for c, s in spec.items() if s["class"] == "confidential_business"}
                    | ({"account_name", "domain"} & set(spec)))
        for row in raw_rows(raw, table):
            for c in person_cols:
                person.add(norm((row.get(c) or "").strip()))
            for c in biz_cols:
                business.add(norm((row.get(c) or "").strip()))
            f, l = (row.get("first_name") or "").lower(), (row.get("last_name") or "").lower()
            if f and l:
                person |= {f"{f} {l}", f"{l}, {f}", f"{l} {f}", f"{f}.{l}", f"{f}_{l}", f"{f}{l}",
                           f"{f}-{l}", f"{f[0]}. {l}", f"{f[0]}.{l}", f"{f[0]} {l}"}
            email = (row.get("email") or "").lower()
            if "@" in email:
                person.add(email.split("@")[0])
    return ({v for v in person if len(v) >= MIN_LEN},
            {v for v in business if len(v) >= MIN_LEN})


def find(text: str, deny: set[str]) -> list[str]:
    # bucket by prefix so this is one pass over the text instead of one per value
    by_prefix: dict[str, list[str]] = {}
    for v in deny:
        by_prefix.setdefault(v[:MIN_LEN], []).append(v)
    out = []
    for i in range(len(text) - MIN_LEN + 1):
        for v in by_prefix.get(text[i:i + MIN_LEN], ()):
            if text.startswith(v, i):
                out.append(v)
    return out


SPACES = re.compile(r"[ \t\u00a0]+")


def norm(text: str) -> str:
    """Lowercase and collapse whitespace."""
    return SPACES.sub(" ", text.lower())


def texts(path: Path):
    """Yield (label, normalized text) for a file. Raises ValueError if it cannot be read."""
    if path.suffix == ".duckdb":
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
        try:
            tables = con.sql("select table_schema, table_name from information_schema.tables "
                             "where table_type = 'BASE TABLE'").fetchall()
            for schema, name in tables:
                rel = con.sql(f'select * from "{schema}"."{name}"')
                body = "\n".join(",".join("" if v is None else str(v) for v in r) for r in rel.fetchall())
                yield f"{path}:{schema}.{name}", norm(",".join(rel.columns) + "\n" + body)
        finally:
            con.close()
    elif path.suffix == ".gz":
        yield str(path), norm(gzip.decompress(path.read_bytes()).decode("utf-8", "strict"))
    elif path.suffix in TEXT_SUFFIXES:
        yield str(path), norm(path.read_bytes().decode("utf-8", "strict"))
    else:
        raise ValueError(f"unscannable file type {path.suffix!r}")


def scan(raw: Path, targets: list[Path], inventory: Path) -> list[str]:
    inv = yaml.safe_load(inventory.read_text())
    person, business = deny_lists(raw, inv)
    hits: list[str] = []
    for root in targets:
        if not root.exists():
            continue
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        is_llm = root.name == "llm"
        for path in files:
            try:
                for label, text in texts(path):
                    hits += [f"{label}: email pattern {m.group(0)!r}" for m in EMAIL.finditer(text)]
                    hits += [f"{label}: phone pattern {m.group(0)!r}" for m in PHONE.finditer(text)]
                    hits += [f"{label}: raw personal value {v[:40]!r}" for v in find(text, person)]
                    if is_llm:
                        hits += [f"{label}: confidential business value {v[:40]!r}"
                                 for v in find(text, business)]
            except (ValueError, UnicodeDecodeError, OSError) as e:
                hits.append(f"{path}: could not scan ({e})")
        if is_llm:
            for path in sorted(root.glob("*.csv")):
                spec = inv["tables"].get(path.stem, {})
                with path.open(newline="") as f:
                    header = next(csv.reader(f), [])
                for c in header:
                    if c in PROTECTED_FROM_LLM or "llm" not in spec.get(c, {}).get("destinations", []):
                        hits.append(f"{path}: column {c!r} may not go to the llm destination")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--inventory", default="config/data_inventory.yml")
    ap.add_argument("targets", nargs="*", default=["data/clean", "data/llm", "data/warehouse.duckdb"])
    a = ap.parse_args()
    hits = scan(Path(a.raw), [Path(t) for t in a.targets], Path(a.inventory))
    if hits:
        print(f"LEAK SCAN FAILED: {len(hits)} finding(s)", file=sys.stderr)
        for h in hits[:50]:
            print("  " + h, file=sys.stderr)
        return 1
    print(f"leak scan ok: {', '.join(a.targets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
