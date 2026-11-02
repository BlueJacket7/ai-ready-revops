"""Privacy gate: the only path from raw data to anything downstream.

Reads data/raw/*.csv and config/data_inventory.yml, and writes:
  data/clean/<table>.csv      columns allowed to the warehouse (dbt)
  data/llm/<table>.csv        columns allowed to any language-model tool
  data/clean/_manifest.json   what was dropped, hashed and generalized, with file hashes

Fails closed. The gate stops, and changes nothing on disk, if:
  - a raw table or column is not in the inventory, or an inventory column is missing
  - a class, handling, rule or destination is unknown
  - a column asks for a destination its class does not allow
  - a row has more or fewer fields than its header
  - no PRIVACY_KEY is set and --dev was not passed
  - two different values hash to the same token
  - anything else goes wrong mid-run

Outputs are swapped in only after the run succeeds; on an error the previous output is restored.

Usage:
  PRIVACY_KEY=... python privacy/gate.py
  python privacy/gate.py --dev    # random one-time key; tokens are not stable across runs
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

HANDLINGS = {"drop", "pseudonymize", "generalize", "keep"}
DESTINATIONS = {"warehouse", "llm"}


class GateError(Exception):
    pass


# --- transformations ---------------------------------------------------------

def pseudonymize(value: str, key: str, namespace: str) -> str:
    """Keyed HMAC-SHA256 token. Deterministic per (key, namespace) so joins still work."""
    if value == "":
        return ""
    digest = hmac.new(key.encode(), f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"p_{digest[:16]}"


def seniority(title: str) -> str:
    t = title.lower()
    if t.startswith("chief") or t in {"cfo", "coo", "ceo", "cto", "cio"}:
        return "C-level"
    if t.startswith("vp") or "vice president" in t or t.startswith("head of"):
        return "VP or Head"
    if "director" in t or t == "controller":
        return "Director"
    if "manager" in t:
        return "Manager"
    return "Individual contributor"


def year_month(value: str) -> str:
    return value[:7] if value else ""


RULES = {"seniority": seniority, "year_month": year_month}


# --- inventory validation ----------------------------------------------------

def load_inventory(path: Path) -> dict:
    inv = yaml.safe_load(path.read_text())
    classes = inv.get("classes", {})
    for cls, spec in classes.items():
        if spec.get("handling") not in HANDLINGS:
            raise GateError(f"class {cls!r}: unknown handling {spec.get('handling')!r}")
        bad = set(spec.get("allowed_destinations", [])) - DESTINATIONS
        if bad:
            raise GateError(f"class {cls!r}: unknown destinations {sorted(bad)}")
    for table, cols in inv["tables"].items():
        for col, spec in cols.items():
            where = f"{table}.{col}"
            cls = spec.get("class")
            if cls not in classes:
                raise GateError(f"{where}: unknown class {cls!r}")
            handling = classes[cls]["handling"]
            dests = set(spec.get("destinations", []))
            unknown = dests - DESTINATIONS
            if unknown:
                raise GateError(f"{where}: unknown destinations {sorted(unknown)}")
            # the class's allowed_destinations is a hard ceiling
            over = dests - set(classes[cls].get("allowed_destinations", []))
            if over:
                raise GateError(f"{where}: class {cls!r} does not allow destinations {sorted(over)}")
            if handling == "generalize" and spec.get("rule") not in RULES:
                raise GateError(f"{where}: generalize needs a known rule, got {spec.get('rule')!r}")
            if handling == "pseudonymize" and not spec.get("namespace"):
                raise GateError(f"{where}: pseudonymize needs a namespace")
    return inv


# --- io ----------------------------------------------------------------------

def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="") as f:
        r = csv.DictReader(f)
        cols = list(r.fieldnames or [])
        rows = []
        for n, row in enumerate(r, start=2):
            # DictReader puts surplus fields under None and fills missing ones with None.
            if None in row or any(v is None for v in row.values()):
                raise GateError(f"{path.name} line {n}: field count does not match the header")
            rows.append(row)
    return cols, rows


def write_csv(path: Path, cols: list[str], rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- gate --------------------------------------------------------------------

def run(raw: Path, out_clean: Path, out_llm: Path, inventory: Path, key: str | None, dev: bool) -> dict:
    if not key:
        if not dev:
            raise GateError("PRIVACY_KEY is not set. Set it, or pass --dev for a local run.")
        key, key_mode = secrets.token_hex(32), "dev-ephemeral"
    else:
        key_mode = "env"

    inv = load_inventory(inventory)
    raw_tables = sorted(p.stem for p in raw.glob("*.csv"))
    unknown = [t for t in raw_tables if t not in inv["tables"]]
    if unknown:
        raise GateError(f"raw tables not in the inventory: {unknown}")
    missing_tables = [t for t in inv["tables"] if t not in raw_tables]
    if missing_tables:
        raise GateError(f"inventory tables missing from raw data: {missing_tables}")

    loaded = {}
    for table in raw_tables:
        cols, rows = read_csv(raw / f"{table}.csv")
        spec = inv["tables"][table]
        extra = [c for c in cols if c not in spec]
        if extra:
            raise GateError(f"{table}: columns not in the inventory: {extra}")
        absent = [c for c in spec if c not in cols]
        if absent:
            raise GateError(f"{table}: inventory columns missing from raw data: {absent}")
        loaded[table] = (cols, rows)

    # build next to the final dirs so the renames stay on one filesystem; keep the
    # old output aside until both swaps succeed, restore it otherwise
    finals = (out_clean, out_llm)
    builds, asides = [], []
    for final in finals:
        final.parent.mkdir(parents=True, exist_ok=True)
        builds.append(Path(tempfile.mkdtemp(prefix=f".{final.name}-build-", dir=final.parent)))
        asides.append(final.parent / f".{final.name}-previous")
    swapped: list[int] = []
    try:
        manifest = transform(inv, loaded, key, key_mode, inventory, builds[0], builds[1])
        for i, final in enumerate(finals):
            if asides[i].exists():
                shutil.rmtree(asides[i])
            if final.exists():
                final.rename(asides[i])
            builds[i].rename(final)
            swapped.append(i)
    except BaseException:
        for i in reversed(swapped):
            shutil.rmtree(finals[i], ignore_errors=True)
        for i, final in enumerate(finals):
            if asides[i].exists() and not final.exists():
                asides[i].rename(final)
        raise
    finally:
        for b in builds:
            shutil.rmtree(b, ignore_errors=True)
        for i, a in enumerate(asides):
            if a.exists() and finals[i].exists():
                shutil.rmtree(a, ignore_errors=True)
    return manifest


def transform(inv: dict, loaded: dict, key: str, key_mode: str, inventory: Path,
              out_clean: Path, out_llm: Path) -> dict:
    manifest = {
        "gate_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "key_mode": key_mode,
        "policy_version": inv.get("policy_version"),
        "inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
        "tables": {},
    }
    tokens: dict[tuple[str, str], str] = {}   # (namespace, token) -> original, for collision checks
    for table, (cols, rows) in loaded.items():
        spec = inv["tables"][table]
        handling = {c: inv["classes"][spec[c]["class"]]["handling"] for c in cols}
        out_rows = []
        for row in rows:
            new = {}
            for c in cols:
                h, s = handling[c], spec[c]
                if h == "drop":
                    continue
                if h == "pseudonymize":
                    tok = pseudonymize(row[c], key, s["namespace"])
                    if tok:
                        seen = tokens.setdefault((s["namespace"], tok), row[c])
                        if seen != row[c]:
                            raise GateError(f"token collision in namespace {s['namespace']!r}")
                    new[c] = tok
                elif h == "generalize":
                    new[c] = RULES[s["rule"]](row[c])
                else:
                    new[c] = row[c]
            out_rows.append(new)
        wh_cols = [c for c in cols if "warehouse" in spec[c].get("destinations", [])]
        llm_cols = [c for c in cols if "llm" in spec[c].get("destinations", [])]
        entry = {
            "rows": len(rows),
            "dropped": [c for c in cols if handling[c] == "drop"],
            "pseudonymized": [c for c in cols if handling[c] == "pseudonymize"],
            "generalized": [c for c in cols if handling[c] == "generalize"],
            "warehouse_columns": wh_cols,
            "llm_columns": llm_cols,
        }
        if wh_cols:
            entry["warehouse_sha256"] = write_csv(out_clean / f"{table}.csv", wh_cols, out_rows)
        if llm_cols:
            entry["llm_sha256"] = write_csv(out_llm / f"{table}.csv", llm_cols, out_rows)
        manifest["tables"][table] = entry
    out_clean.mkdir(parents=True, exist_ok=True)
    (out_clean / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--clean", default="data/clean")
    ap.add_argument("--llm", default="data/llm")
    ap.add_argument("--inventory", default="config/data_inventory.yml")
    ap.add_argument("--dev", action="store_true", help="use a random one-time key")
    a = ap.parse_args()
    try:
        m = run(Path(a.raw), Path(a.clean), Path(a.llm), Path(a.inventory),
                os.environ.get("PRIVACY_KEY"), a.dev)
    except GateError as e:
        print(f"GATE REFUSED: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - any failure is a refusal, never a partial write
        print(f"GATE FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    t = m["tables"].values()
    print(f"gate ok: {len(m['tables'])} tables, {sum(len(x['dropped']) for x in t)} columns dropped, "
          f"{sum(len(x['pseudonymized']) for x in t)} pseudonymized, key={m['key_mode']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
