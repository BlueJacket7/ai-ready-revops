"""Tests for the privacy gate and leak scanner."""

from __future__ import annotations

import csv
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "privacy"))

import gate  # noqa: E402
import scan  # noqa: E402

INVENTORY = ROOT / "config" / "data_inventory.yml"
KEY = "test-key-1"


@pytest.fixture(scope="session")
def raw(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("raw")
    subprocess.run([sys.executable, str(ROOT / "generator" / "generate.py"), "--seed", "7",
                    "--out", str(out)], check=True, capture_output=True)
    return out


def run_gate(raw: Path, tmp: Path, key: str | None = KEY, dev: bool = False,
             inventory: Path = INVENTORY) -> tuple[Path, Path]:
    clean, llm = tmp / "out" / "clean", tmp / "out" / "llm"
    gate.run(raw, clean, llm, inventory, key, dev)
    return clean, llm


def rows(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def edited_inventory(tmp: Path, change) -> Path:
    inv = yaml.safe_load(INVENTORY.read_text())
    change(inv)
    path = tmp / "inventory.yml"
    path.write_text(yaml.safe_dump(inv))
    return path


def copy_raw(raw: Path, tmp: Path) -> Path:
    bad = tmp / "raw"
    shutil.copytree(raw, bad)
    return bad



def test_output_carries_no_personal_data(raw, tmp_path):
    clean, llm = run_gate(raw, tmp_path)
    assert scan.scan(raw, [clean, llm], INVENTORY) == []


def test_free_text_never_leaves_the_gate(raw, tmp_path):
    clean, llm = run_gate(raw, tmp_path)
    for d in (clean, llm):
        assert "next_step_notes" not in (d / "opportunities.csv").read_text().splitlines()[0]


def test_llm_export_is_minimized(raw, tmp_path):
    _, llm = run_gate(raw, tmp_path)
    assert not (llm / "contacts.csv").exists(), "no contact-level rows go to the llm"
    assert not (llm / "reps.csv").exists(), "no rep table goes to the llm"
    header = next(csv.reader((llm / "accounts.csv").open()))
    assert "account_name" not in header and "domain" not in header
    owners = {r["owner_rep_id"] for r in rows(llm / "opportunities.csv")}
    assert all(o.startswith("p_") for o in owners), "reps appear only as tokens"


def test_generalization_rule(raw, tmp_path):
    clean, _ = run_gate(raw, tmp_path)
    assert {r["title"] for r in rows(clean / "contacts.csv")} <= {
        "C-level", "VP or Head", "Director", "Manager", "Individual contributor"}


def test_pseudonyms_join_across_tables(raw, tmp_path):
    clean, _ = run_gate(raw, tmp_path)
    contacts = {r["contact_id"] for r in rows(clean / "contacts.csv")}
    assert {r["primary_contact_id"] for r in rows(clean / "opportunities.csv")} - {""} <= contacts
    assert {r["contact_id"] for r in rows(clean / "marketing_touches.csv")} <= contacts
    reps = {r["rep_id"] for r in rows(clean / "reps.csv")}
    assert {r["owner_rep_id"] for r in rows(clean / "opportunities.csv")} <= reps


def test_pseudonyms_depend_on_the_key():
    a = gate.pseudonymize("CON000001", "key-a", "contact")
    assert a == gate.pseudonymize("CON000001", "key-a", "contact")
    assert a != gate.pseudonymize("CON000001", "key-b", "contact")
    assert a != gate.pseudonymize("CON000001", "key-a", "rep")
    assert "CON000001" not in a


def test_dev_mode_key_is_random_per_run(raw, tmp_path):
    clean_a, _ = run_gate(raw, tmp_path / "a", key=None, dev=True)
    clean_b, _ = run_gate(raw, tmp_path / "b", key=None, dev=True)
    assert rows(clean_a / "contacts.csv")[0]["contact_id"] != rows(clean_b / "contacts.csv")[0]["contact_id"]
    assert '"key_mode": "dev-ephemeral"' in (clean_a / "_manifest.json").read_text()


def test_missing_key_is_refused(raw, tmp_path):
    with pytest.raises(gate.GateError, match="PRIVACY_KEY"):
        run_gate(raw, tmp_path, key=None)
    assert not (tmp_path / "out" / "clean").exists()


def test_unlisted_column_fails_closed(raw, tmp_path):
    bad = copy_raw(raw, tmp_path)
    data = rows(bad / "contacts.csv")
    for r in data:
        r["date_of_birth"] = "1990-01-01"
    with (bad / "contacts.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0]))
        w.writeheader()
        w.writerows(data)
    with pytest.raises(gate.GateError, match="not in the inventory"):
        run_gate(bad, tmp_path)
    assert not (tmp_path / "out" / "clean").exists()


def test_unlisted_table_fails_closed(raw, tmp_path):
    bad = copy_raw(raw, tmp_path)
    (bad / "support_tickets.csv").write_text("ticket_id,body\nT1,hello\n")
    with pytest.raises(gate.GateError, match="raw tables not in the inventory"):
        run_gate(bad, tmp_path)
    assert not (tmp_path / "out" / "clean").exists()


def test_unknown_handling_is_refused(raw, tmp_path):
    # a typo here used to fall through to 'keep'
    inv = edited_inventory(tmp_path, lambda i: i["classes"]["pseudonymous_key"].update(handling="pseudonymise"))
    with pytest.raises(gate.GateError, match="unknown handling"):
        run_gate(raw, tmp_path, inventory=inv)


def test_class_ceiling_blocks_confidential_data_to_llm(raw, tmp_path):
    inv = edited_inventory(tmp_path, lambda i: i["tables"]["accounts"]["account_name"].update(
        destinations=["warehouse", "llm"]))
    with pytest.raises(gate.GateError, match="does not allow destinations"):
        run_gate(raw, tmp_path, inventory=inv)


def test_malformed_row_fails_without_partial_output(raw, tmp_path):
    clean, _ = run_gate(raw, tmp_path)
    before = (clean / "accounts.csv").read_bytes()
    for extra in ("\nCON999999,ACC000001\n", "\nREP999,Jo,Smith,j@x.example,SMB,East,2024-01-01,1,surplus\n"):
        bad = copy_raw(raw, tmp_path / f"bad{len(extra)}")
        target = bad / ("contacts.csv" if extra.startswith("\nCON") else "reps.csv")
        target.write_text(target.read_text().rstrip("\n") + extra)
        with pytest.raises(gate.GateError, match="field count"):
            gate.run(bad, clean, clean.parent / "llm", INVENTORY, KEY, False)
        assert (clean / "accounts.csv").read_bytes() == before


def test_scanner_catches_a_planted_name_in_any_case(raw, tmp_path):
    clean, llm = run_gate(raw, tmp_path)
    c = rows(raw / "contacts.csv")[0]
    for variant in (f"{c['first_name']} {c['last_name']}".upper(),
                    f"{c['last_name']}, {c['first_name']}".lower(),
                    c["email"].split("@")[0]):
        f = clean / "accounts.csv"
        original = f.read_text()
        f.write_text(original + f"\nplanted,{variant}\n")
        assert any("raw personal value" in h for h in scan.scan(raw, [clean, llm], INVENTORY)), variant
        f.write_text(original)


def test_scanner_catches_patterns_and_unreadable_files(raw, tmp_path):
    clean, llm = run_gate(raw, tmp_path)
    (llm / "notes.jsonl").write_text('{"x": "reach me at someone@else.example or 555-0147"}')
    (clean / "dump.parquet").write_bytes(b"PAR1\x00\x01")
    hits = scan.scan(raw, [clean, llm], INVENTORY)
    assert any("email pattern" in h for h in hits)
    assert any("phone pattern" in h for h in hits)
    assert any("could not scan" in h for h in hits)


def test_scanner_floor_holds_if_the_inventory_is_loosened(raw, tmp_path):
    _, llm = run_gate(raw, tmp_path)
    name = rows(raw / "accounts.csv")[0]["account_name"]
    (llm / "accounts.csv").write_text((llm / "accounts.csv").read_text() + f"\n{name}\n")
    loosened = edited_inventory(tmp_path, lambda i: i["tables"]["accounts"]["account_name"].update(
        **{"class": "business_attribute", "destinations": ["warehouse", "llm"]}))
    assert any("confidential business value" in h for h in scan.scan(raw, [llm], loosened))


def test_scanner_reads_the_warehouse(raw, tmp_path):
    import duckdb
    db = tmp_path / "w.duckdb"
    c = rows(raw / "contacts.csv")[0]
    con = duckdb.connect(str(db))
    con.execute("create table t as select ? as who", [f"{c['first_name']} {c['last_name']}"])
    con.close()
    assert any("raw personal value" in h for h in scan.scan(raw, [db], INVENTORY))


def test_dbt_never_reads_raw_data():
    sources = yaml.safe_load((ROOT / "dbt" / "models" / "staging" / "sources.yml").read_text())
    for src in sources["sources"]:
        assert "data/clean/" in src["meta"]["external_location"]
    for path in (ROOT / "dbt").rglob("*"):
        if path.is_file() and path.suffix in (".sql", ".yml") and "target" not in path.parts:
            assert "data/raw" not in path.read_text(), f"{path} references raw data"


def test_failed_swap_restores_previous_output(raw, tmp_path, monkeypatch):
    clean, llm = run_gate(raw, tmp_path)
    before = {p.name: p.read_bytes() for d in (clean, llm) for p in d.iterdir()}
    real_rename = Path.rename

    def flaky(self, target):
        if Path(target) == llm and ".llm-build-" in self.name:
            raise OSError("boom")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        gate.run(raw, clean, llm, INVENTORY, "another-key", False)
    monkeypatch.setattr(Path, "rename", real_rename)
    after = {p.name: p.read_bytes() for d in (clean, llm) for p in d.iterdir()}
    assert after == before
    assert not [p for p in clean.parent.iterdir() if p.name.startswith(".")], "no temp dirs left"


def test_scanner_catches_spacing_hyphen_and_initial_forms(raw, tmp_path):
    clean, llm = run_gate(raw, tmp_path)
    c = rows(raw / "contacts.csv")[0]
    f, l = c["first_name"], c["last_name"]
    for variant in (f"{f}  {l}", f"{f}-{l}", f"{f[0]}. {l}"):
        target = clean / "accounts.csv"
        original = target.read_text()
        target.write_text(original + f"\nplanted,{variant}\n")
        assert any("raw personal value" in h for h in scan.scan(raw, [clean, llm], INVENTORY)), variant
        target.write_text(original)
