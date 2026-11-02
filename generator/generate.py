"""Synthetic B2B SaaS company generator.

Produces three years of CRM, billing and marketing data for a fictional
company, written as CSVs to data/raw/. The output is deliberately messy in
the ways real CRM data is messy (duplicate accounts, missing close dates,
skipped stages, missing amounts) and deliberately contains fake personal
data, so the privacy gate downstream has something real to protect.

All personal data is invented. Email domains use the reserved .example TLD
(RFC 2606) and phone numbers use the 555-0100..0199 fictional range.

Usage:
    python generator/generate.py --seed 42 --out data/raw
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

# --- model parameters --------------------------------------------------------

START = date(2023, 11, 1)
MONTHS = 36

STAGES = ["Discovery", "Qualification", "Solution", "Proposal", "Negotiation"]

SEGMENTS = {
    # new-business opps created per month, win rate, median ARR, median cycle days,
    # annual churn probability at renewal, monthly expansion probability
    "SMB": dict(nb_per_month=38, win=0.28, arr=12_000, cycle=35, churn=0.15, expand=0.010, share=0.55),
    "MidMarket": dict(nb_per_month=16, win=0.22, arr=45_000, cycle=70, churn=0.10, expand=0.015, share=0.32),
    "Enterprise": dict(nb_per_month=5, win=0.18, arr=160_000, cycle=140, churn=0.06, expand=0.020, share=0.13),
}

INSTALLED_BASE = 300          # customers already live when the window opens
INITIAL_PROSPECTS = 1_800
NEW_PROSPECTS_PER_MONTH = 25

# Mess rates
P_DUPLICATE_ACCOUNT = 0.02
P_SKIP_STAGE = 0.04
P_MISSING_CLOSE_DATE = 0.03
P_MISSING_AMOUNT = 0.015
P_CONTRACTION_AT_RENEWAL = 0.10

INDUSTRIES = ["Software", "Financial Services", "Healthcare", "Retail", "Manufacturing",
              "Logistics", "Media", "Education", "Professional Services", "Energy"]
CHANNELS = {"Paid Search": 0.22, "Organic": 0.18, "Webinar": 0.14, "Event": 0.10,
            "Outbound": 0.20, "Referral": 0.08, "Partner": 0.08}
TITLES = ["VP Revenue Operations", "Director of Sales", "CFO", "Head of Marketing",
          "Sales Operations Manager", "Chief Revenue Officer", "IT Director",
          "Data Analyst", "Controller", "Account Manager", "COO", "Head of Data"]
STATES = ["NY", "NJ", "CA", "TX", "IL", "MA", "WA", "GA", "CO", "FL", "PA", "NC"]

FIRST = ["Avery", "Jordan", "Priya", "Marcus", "Elena", "Tomas", "Grace", "Hiro", "Amara",
         "Liam", "Sofia", "Noah", "Mei", "Diego", "Hannah", "Omar", "Chloe", "Ravi", "Isla",
         "Mateo", "Zoe", "Kwame", "Nora", "Ethan", "Leila", "Owen", "Yara", "Caleb", "Ines",
         "Felix", "Maya", "Jonah", "Anya", "Theo", "Rosa", "Samir", "Ivy", "Lucas", "Nadia", "Eli"]
LAST = ["Okafor", "Lindqvist", "Ramirez", "Chen", "Novak", "Haddad", "Brennan", "Sato",
        "Mensah", "Kowalski", "Duarte", "Fischer", "Iyer", "Moreau", "Kaplan", "Abara",
        "Whitfield", "Rossi", "Nakamura", "Petrov", "Gallagher", "Osei", "Varga", "Lund",
        "Castillo", "Byrne", "Zhou", "Adeyemi", "Holm", "Quinn", "Serrano", "Tanaka",
        "Murphy", "Dubois", "Ahmed", "Keller", "Silva", "Novotny", "Park", "Reyes"]
WORD_A = ["North", "Bright", "Iron", "Cedar", "Blue", "Summit", "Clear", "Harbor", "Silver",
          "Quarry", "Atlas", "Ember", "Granite", "Juniper", "Meridian", "Orchard", "Pioneer",
          "Redwood", "Signal", "Tidal", "Vantage", "Willow", "Keystone", "Lumen"]
WORD_B = ["loop", "field", "works", "stack", "ledger", "point", "line", "forge", "path",
          "grid", "bridge", "yard", "mark", "wave", "craft", "scale", "vault", "spring"]
SUFFIX = ["Inc", "LLC", "Corp", "Group", "Co", "Labs", "Systems", "Partners"]


def month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


END = month_add(START, MONTHS)          # exclusive
AS_OF = END - timedelta(days=1)         # last day of the window


@dataclass
class World:
    rng: np.random.Generator
    reps: list = field(default_factory=list)
    accounts: list = field(default_factory=list)
    contacts: list = field(default_factory=list)
    touches: list = field(default_factory=list)
    opps: list = field(default_factory=list)
    history: list = field(default_factory=list)
    events: list = field(default_factory=list)
    spend: list = field(default_factory=list)
    subs: dict = field(default_factory=dict)   # subscription_id -> state
    by_account_contacts: dict = field(default_factory=dict)
    last_touch_channel: dict = field(default_factory=dict)
    used_names: set = field(default_factory=set)
    counters: dict = field(default_factory=lambda: {"acct": 0, "con": 0, "opp": 0, "sub": 0, "evt": 0, "tch": 0})

    def next_id(self, kind: str, prefix: str) -> str:
        self.counters[kind] += 1
        return f"{prefix}{self.counters[kind]:06d}"


# --- entity builders ---------------------------------------------------------

def person(w: World) -> tuple[str, str]:
    return str(w.rng.choice(FIRST)), str(w.rng.choice(LAST))


def phone(w: World) -> str:
    return f"({w.rng.integers(201, 990)}) 555-01{w.rng.integers(0, 100):02d}"


WORD_C = ["", "Analytics", "Health", "Freight", "Media", "Energy", "Capital", "Retail",
          "Software", "Labs", "Logistics", "Learning"]


def company_name(w: World) -> tuple[str, str]:
    """Unique company name and a reserved .example domain."""
    if "_pool" not in w.counters:
        pool = [(a, b, c) for a in WORD_A for b in WORD_B for c in WORD_C]
        order = w.rng.permutation(len(pool))
        w.counters["_pool"] = [pool[i] for i in order]
        w.counters["_next"] = 0
    a, b, c = w.counters["_pool"][w.counters["_next"]]
    w.counters["_next"] += 1
    base = f"{a}{b}"
    display = f"{base} {c}".strip()
    slug = f"{base}{c}".lower()
    return f"{display} {w.rng.choice(SUFFIX)}", f"{slug}.example"


def make_reps(w: World) -> None:
    plan = [("SMB", "East", 3), ("SMB", "West", 2), ("MidMarket", "East", 2),
            ("MidMarket", "West", 2), ("Enterprise", "East", 2), ("Enterprise", "West", 1)]
    quota = {"SMB": 600_000, "MidMarket": 900_000, "Enterprise": 1_400_000}
    n = 0
    for seg, region, count in plan:
        for _ in range(count):
            n += 1
            f, l = person(w)
            w.reps.append(dict(
                rep_id=f"REP{n:03d}", first_name=f, last_name=l,
                email=f"{f.lower()}.{l.lower()}@sellerco.example",
                segment=seg, region=region,
                hire_date=(START - timedelta(days=int(w.rng.integers(60, 900)))).isoformat(),
                annual_quota=quota[seg],
            ))


def reps_for(w: World, seg: str) -> list[dict]:
    return [r for r in w.reps if r["segment"] == seg]


def make_account(w: World, created: date, seg: str | None = None) -> dict:
    if seg is None:
        seg = str(w.rng.choice(list(SEGMENTS), p=[s["share"] for s in SEGMENTS.values()]))
    name, domain = company_name(w)
    emp = {"SMB": (10, 200), "MidMarket": (200, 2000), "Enterprise": (2000, 40000)}[seg]
    owner = w.rng.choice(reps_for(w, seg))
    acct = dict(
        account_id=w.next_id("acct", "ACC"), account_name=name, domain=domain,
        industry=str(w.rng.choice(INDUSTRIES)), segment=seg,
        employee_count=int(w.rng.integers(*emp)), billing_country="US",
        billing_state=str(w.rng.choice(STATES)), created_at=created.isoformat(),
        owner_rep_id=owner["rep_id"],
    )
    w.accounts.append(acct)
    for _ in range(int(w.rng.integers(1, 5))):
        make_contact(w, acct, created)
    # Mess: a duplicate record of the same company, as when two reps both create it.
    if w.rng.random() < P_DUPLICATE_ACCOUNT:
        variant = str(w.rng.choice([name.upper(), name.replace(" Inc", " Incorporated"),
                                    name.split(" ")[0], name + " (old)"]))
        dup_created = created + timedelta(days=int(w.rng.integers(5, 200)))
        dup = dict(acct, account_id=w.next_id("acct", "ACC"), account_name=variant,
                   created_at=dup_created.isoformat(), _dup=True)
        w.accounts.append(dup)
        make_contact(w, dup, dup_created)
    return acct


def make_contact(w: World, acct: dict, created: date) -> dict:
    f, l = person(w)
    c = dict(
        contact_id=w.next_id("con", "CON"), account_id=acct["account_id"],
        first_name=f, last_name=l,
        email=f"{f.lower()}.{l.lower()}@{acct['domain']}",
        phone=phone(w), title=str(w.rng.choice(TITLES)), created_at=created.isoformat(),
    )
    w.contacts.append(c)
    w.by_account_contacts.setdefault(acct["account_id"], []).append(c)
    return c


def contacts_of(w: World, account_id: str) -> list[dict]:
    return w.by_account_contacts.get(account_id, [])


def add_touches(w: World, acct: dict, before: date, n: int) -> None:
    cons = contacts_of(w, acct["account_id"])
    if not cons:
        return
    names, probs = list(CHANNELS), list(CHANNELS.values())
    for _ in range(n):
        c = cons[int(w.rng.integers(0, len(cons)))]
        earliest = date.fromisoformat(c["created_at"])
        when = max(before - timedelta(days=int(w.rng.integers(1, 120))), earliest)
        ch = str(w.rng.choice(names, p=probs))
        w.touches.append(dict(
            touch_id=w.next_id("tch", "TCH"), contact_id=c["contact_id"],
            account_id=acct["account_id"], channel=ch,
            campaign=f"{ch} {when.year}-Q{(when.month - 1) // 3 + 1}",
            touched_at=when.isoformat(),
        ))
        w.last_touch_channel[acct["account_id"]] = ch


# --- opportunities -----------------------------------------------------------

def lognormal(w: World, median: float, sigma: float = 0.55) -> float:
    return float(median * np.exp(w.rng.normal(0, sigma)))


def make_opp(w: World, acct: dict, created: date, opp_type: str, amount: float,
             win_p: float, cycle_days: float, close_by: date | None = None) -> dict:
    seg = acct["segment"]
    rep = next(r for r in w.reps if r["rep_id"] == acct["owner_rep_id"])
    cons = contacts_of(w, acct["account_id"])
    primary = cons[0] if cons else None
    won = w.rng.random() < win_p
    cycle = max(7, int(lognormal(w, cycle_days, 0.45)))
    close = created + timedelta(days=cycle)
    if close_by is not None:
        close = min(close, close_by)
        cycle = max(1, (close - created).days)
    closed = close <= AS_OF

    # Which stages the deal touched. Losses exit early; some deals skip a stage.
    if won:
        reached = len(STAGES)
    else:
        reached = int(w.rng.integers(1, len(STAGES) + 1))
    path = STAGES[:reached]
    if len(path) > 2 and w.rng.random() < P_SKIP_STAGE:
        path = [s for i, s in enumerate(path) if i != int(w.rng.integers(1, len(path) - 1))]

    opp_id = w.next_id("opp", "OPP")
    span = (min(close, AS_OF) - created).days if closed else (AS_OF - created).days
    offsets = sorted(int(x) for x in w.rng.uniform(0, max(span, 1), size=len(path) - 1))
    offsets = [0] + offsets
    if not closed:
        # An open deal has only progressed partway along its path.
        progress = max(1, int(len(path) * min(1.0, (AS_OF - created).days / cycle)))
        path, offsets = path[:progress], offsets[:progress]
    for stage, off in zip(path, offsets):
        w.history.append(dict(opportunity_id=opp_id, stage=stage,
                              entered_at=(created + timedelta(days=off)).isoformat()))
    if closed:
        final = "Closed Won" if won else "Closed Lost"
        w.history.append(dict(opportunity_id=opp_id, stage=final, entered_at=close.isoformat()))
        stage_now = final
    else:
        stage_now = path[-1]

    source = w.last_touch_channel.get(acct["account_id"], "Inbound")

    contact_name = f"{primary['first_name']} {primary['last_name']}" if primary else "the buyer"
    note = str(w.rng.choice([
        f"Call with {contact_name}, wants pricing by Friday.",
        f"{contact_name} looping in procurement; reach at {primary['phone'] if primary else 'n/a'}.",
        f"Sent deck to {primary['email'] if primary else 'n/a'}. Budget approved for next quarter.",
        "Champion left the company, re-qualify.",
        "Security review in progress.",
        "",
    ]))

    opp = dict(
        opportunity_id=opp_id, account_id=acct["account_id"], owner_rep_id=rep["rep_id"],
        primary_contact_id=primary["contact_id"] if primary else "",
        opportunity_type=opp_type, lead_source=source, created_at=created.isoformat(),
        close_date=close.isoformat() if closed else (created + timedelta(days=cycle)).isoformat(),
        stage=stage_now, is_closed=closed, is_won=bool(closed and won),
        amount_arr=round(amount, 2), next_step_notes=note,
    )
    # Mess
    if closed and w.rng.random() < P_MISSING_CLOSE_DATE:
        opp["close_date"] = ""
    if w.rng.random() < P_MISSING_AMOUNT:
        opp["amount_arr"] = ""
    w.opps.append(opp)
    opp["_close"] = close
    opp["_amount"] = amount
    return opp


# --- subscriptions -----------------------------------------------------------

def add_event(w: World, sub_id: str, account_id: str, month: date, kind: str,
              delta: float, opp_id: str = "") -> None:
    w.events.append(dict(
        event_id=w.next_id("evt", "EVT"), subscription_id=sub_id, account_id=account_id,
        event_month=month.isoformat(), event_type=kind, arr_delta=round(delta, 2),
        opportunity_id=opp_id,
    ))


def start_subscription(w: World, acct: dict, month: date, arr: float, opp_id: str = "") -> None:
    sub_id = w.next_id("sub", "SUB")
    w.subs[sub_id] = dict(account_id=acct["account_id"], seg=acct["segment"], arr=arr,
                          start=month, active=True, acct=acct)
    add_event(w, sub_id, acct["account_id"], month, "new", arr, opp_id)


def active_account_ids(w: World) -> set:
    return {s["account_id"] for s in w.subs.values() if s["active"]}


# --- simulation --------------------------------------------------------------

def simulate(seed: int) -> World:
    w = World(rng=np.random.default_rng(seed))
    make_reps(w)

    # Installed base: customers who signed before the window opened.
    for _ in range(INSTALLED_BASE):
        seg = str(w.rng.choice(list(SEGMENTS), p=[s["share"] for s in SEGMENTS.values()]))
        started = month_add(START, -int(w.rng.integers(1, 25)))
        acct = make_account(w, started - timedelta(days=int(w.rng.integers(30, 200))), seg)
        start_subscription(w, acct, started, lognormal(w, SEGMENTS[seg]["arr"]))

    for _ in range(INITIAL_PROSPECTS):
        make_account(w, START - timedelta(days=int(w.rng.integers(1, 700))))

    pending = []   # (month_to_book, kind, acct, sub_id, amount, opp_id)
    busy_until: dict[str, date] = {}   # account -> close date of its open new-business deal
    customers: set[str] = set()        # won, booked or pending; never re-sold as new business
    churned: set[str] = set()          # churned customers do not come back in this simulation
    for m in range(MONTHS):
        month = month_add(START, m)

        # Book events whose opportunity closed in an earlier month.
        for item in [p for p in pending if p[0] == month]:
            _, kind, acct, sub_id, amount, opp_id = item
            if kind == "new":
                start_subscription(w, acct, month, amount, opp_id)
            elif kind == "expansion" and w.subs[sub_id]["active"]:
                w.subs[sub_id]["arr"] += amount
                add_event(w, sub_id, acct["account_id"], month, "expansion", amount, opp_id)
        pending = [p for p in pending if p[0] != month]

        for _ in range(NEW_PROSPECTS_PER_MONTH):
            make_account(w, month + timedelta(days=int(w.rng.integers(0, 28))))

        customers |= active_account_ids(w)
        for seg, p in SEGMENTS.items():
            # Eligible: a prospect that exists, is not a duplicate record, has never been
            # a customer, and has no new-business deal still open as of this month.
            pool = [a for a in w.accounts if a["segment"] == seg
                    and not a.get("_dup")
                    and a["account_id"] not in customers
                    and a["account_id"] not in churned
                    and busy_until.get(a["account_id"], START) < month
                    and date.fromisoformat(a["created_at"]) < month_add(month, 1)]
            n = min(len(pool), int(w.rng.poisson(p["nb_per_month"])))
            for idx in w.rng.choice(len(pool), size=n, replace=False):
                acct = pool[int(idx)]
                created = max(month + timedelta(days=int(w.rng.integers(0, 28))),
                              date.fromisoformat(acct["created_at"]))
                add_touches(w, acct, created, int(w.rng.integers(1, 7)))
                amount = lognormal(w, p["arr"])
                opp = make_opp(w, acct, created, "New Business", amount, p["win"], p["cycle"])
                busy_until[acct["account_id"]] = opp["_close"]
                if opp["is_won"]:
                    customers.add(acct["account_id"])
                    pending.append((month_add(month_start(opp["_close"]), 1), "new", acct, None,
                                    opp["_amount"], opp["opportunity_id"]))

        # Existing customers: expansion, renewals, contraction, churn.
        for sub_id, s in list(w.subs.items()):
            if not s["active"]:
                continue
            acct, p = s["acct"], SEGMENTS[s["seg"]]
            if w.rng.random() < p["expand"]:
                created = month + timedelta(days=int(w.rng.integers(0, 28)))
                amount = s["arr"] * float(w.rng.uniform(0.10, 0.40))
                opp = make_opp(w, acct, created, "Expansion", amount, 0.55, p["cycle"] * 0.6)
                if opp["is_won"]:
                    pending.append((month_add(month_start(opp["_close"]), 1), "expansion", acct,
                                    sub_id, opp["_amount"], opp["opportunity_id"]))

            months_live = (month.year - s["start"].year) * 12 + month.month - s["start"].month
            if months_live > 0 and months_live % 12 == 0:
                renewal_created = month - timedelta(days=90)
                churns = w.rng.random() < p["churn"]
                opp = make_opp(w, acct, renewal_created, "Renewal", s["arr"],
                               0.0 if churns else 1.0, 60, close_by=month - timedelta(days=1))
                if churns:
                    churned.add(acct["account_id"])
                    add_event(w, sub_id, acct["account_id"], month, "churn", -s["arr"],
                              opp["opportunity_id"])
                    s["active"] = False
                elif w.rng.random() < P_CONTRACTION_AT_RENEWAL:
                    cut = s["arr"] * float(w.rng.uniform(0.10, 0.30))
                    s["arr"] -= cut
                    add_event(w, sub_id, acct["account_id"], month, "contraction", -cut,
                              opp["opportunity_id"])

        # Sales and marketing spend: aggregate company costs, no personal data.
        sales = sum(r["annual_quota"] for r in w.reps) * 0.22 / 12 * float(w.rng.uniform(0.92, 1.08))
        mktg = 95_000 * float(w.rng.uniform(0.85, 1.20)) * (1 + 0.01 * m)
        w.spend.append(dict(month=month.isoformat(), sales_spend=round(sales, 2),
                            marketing_spend=round(mktg, 2)))
    return w


# --- output ------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict]) -> None:
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/raw")
    args = ap.parse_args()
    w = simulate(args.seed)
    out = Path(args.out)
    tables = {
        "reps": w.reps, "accounts": w.accounts, "contacts": w.contacts,
        "marketing_touches": w.touches, "opportunities": w.opps,
        "opportunity_stage_history": w.history, "subscription_events": w.events,
        "sm_spend": w.spend,
    }
    for name, rows in tables.items():
        write_csv(out / f"{name}.csv", rows)
    meta = {"seed": args.seed, "start": START.isoformat(), "as_of": AS_OF.isoformat(),
            "row_counts": {k: len(v) for k, v in tables.items()}}
    (out / "_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
