# finops_agent_many.py
"""
Account #1 (Sender) — Generates "real work" fintech scenarios:
- AP: vendor invoices, approvals, payment creation (ACH only)
- Fraud invoice patterns: duplicates, PO/GRN mismatch, lookalike vendor, bank-change attempt
- Customer refunds: refund-to-new-destination, over-refund, refund-after-dispute, mismatched customer
- Inventory receivable: PO + GRN, partial receipts, quantity mismatches
- AR: customer invoices + payment application anomalies
- Excel analysis: generate Excel-like reports w/ preview rows

NO WIRES.

Defaults:
- TOTAL_SCENARIOS=5000

Run:
  pip install httpx
  python finops_agent_many.py

Proxy later:
  BASE_URL=http://localhost:8080 python finops_agent_many.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import httpx


BASE_URL = os.environ.get("BASE_URL", "http://localhost:9006")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "fake_token")
TOTAL_SCENARIOS = int(os.environ.get("TOTAL_SCENARIOS", "5000"))
PRINT_EVERY = int(os.environ.get("PRINT_EVERY", "100"))
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "30"))

HEADERS_BASE = {
    "Authorization": f"Bearer {AUTH_TOKEN}",
    "X-Actor": "agent:ledgerworks-demo",
    "X-Tenant": "ledgerworks",
    "X-Policy-Pack": "finops-realwork-v1",
}

SEED = 2026  # change to vary the dataset deterministically


@dataclass
class Scenario:
    i: int
    label: str
    path: str
    body: Dict[str, Any]
    expected: str


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def envelope(payload: Dict[str, Any], risk_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action_id": str(uuid.uuid4()),
        "tenant_id": "ledgerworks",
        "environment": "demo",
        "actor": {"type": "agent", "id": "finops-agent-01"},
        "risk_context": risk_context,
        "payload": payload,
    }


# ---- weights (real work emphasis) ----
# Money-bearing: AP invoice amounts, AP payments, refunds, AR invoice/payments
ACTION_WEIGHTS = [
    ("ap_invoice_create", 18),
    ("ap_invoice_approve", 10),
    ("ap_payment_create", 14),
    ("inventory_po_create", 10),
    ("inventory_grn_create", 10),
    ("refund_create", 14),
    ("ar_invoice_create", 6),
    ("ar_payment_apply", 6),
    ("excel_report_generate", 10),
    ("audit_append", 2),
]

TRUST_MODES = [
    "duplicate_invoice",
    "po_mismatch",
    "grn_mismatch",
    "lookalike_vendor",
    "bank_change_attempt",
    "missing_supporting_docs",
    "split_invoice_to_avoid_threshold",
    "refund_to_new_destination",
    "refund_over_amount",
    "refund_after_chargeback",
    "ar_overpayment",
    "ar_payment_wrong_invoice",
    "export_sensitive_report",
    "normal",
]

TRUST_WEIGHTS = {
    "duplicate_invoice": 10,
    "po_mismatch": 9,
    "grn_mismatch": 9,
    "lookalike_vendor": 8,
    "bank_change_attempt": 8,
    "missing_supporting_docs": 8,
    "split_invoice_to_avoid_threshold": 7,
    "refund_to_new_destination": 9,
    "refund_over_amount": 8,
    "refund_after_chargeback": 7,
    "ar_overpayment": 6,
    "ar_payment_wrong_invoice": 6,
    "export_sensitive_report": 8,
    "normal": 10,
}


def choose_trust(rnd: random.Random) -> str:
    modes = list(TRUST_WEIGHTS.keys())
    weights = [TRUST_WEIGHTS[m] for m in modes]
    return rnd.choices(modes, weights=weights, k=1)[0]


def expected_outcome(mode: str, amount: float | None = None) -> str:
    """
    Suggested demo outcome for an interceptor:
    - block: clear exfil / bank-change / refund-to-new-destination + high amount
    - approve: gray-zone issues (duplicates, mismatch, missing docs) esp. high amount
    - allow: normal, or low-risk/low-amount
    """
    hi = (amount is not None and amount >= 25_000)
    mid = (amount is not None and amount >= 5_000)

    if mode in {"export_sensitive_report"}:
        return "block"
    if mode in {"bank_change_attempt"}:
        return "block" if mid else "approve"
    if mode in {"refund_to_new_destination"}:
        return "block" if mid else "approve"
    if mode in {"refund_over_amount", "refund_after_chargeback"}:
        return "approve" if mid else "approve"
    if mode in {"duplicate_invoice", "po_mismatch", "grn_mismatch", "missing_supporting_docs", "split_invoice_to_avoid_threshold"}:
        return "approve" if (mid or hi) else "approve"
    if mode in {"ar_overpayment", "ar_payment_wrong_invoice"}:
        return "approve"
    return "allow"


def make_headers(corr: str, idem: str | None = None) -> Dict[str, str]:
    h = dict(HEADERS_BASE)
    h["X-Request-Id"] = _id("req")
    h["X-Correlation-Id"] = corr
    h["Idempotency-Key"] = idem or _id("idem")
    return h


def build_scenarios(n: int) -> List[Scenario]:
    rnd = random.Random(SEED)
    scenarios: List[Scenario] = []

    # Pools
    vendors = [f"VND-{1000+i}" for i in range(60)]
    pos = [f"PO-{2000+i}" for i in range(120)]
    grns = [f"GRN-{3000+i}" for i in range(120)]
    invoices = [f"INV-{4000+i}" for i in range(200)]
    customers = [f"CUST-{500+i}" for i in range(80)]
    ar_invs = [f"AR-{7000+i}" for i in range(160)]

    # SKU catalog (inventory receivable)
    skus = [f"SKU-{i:04d}" for i in range(1, 51)]

    actions, weights = zip(*ACTION_WEIGHTS)

    for i in range(1, n + 1):
        corr = _id("corr")
        trust = choose_trust(rnd)

        action = rnd.choices(actions, weights=weights, k=1)[0]

        risk_context = {
            "correlation_id": corr,
            "trust_mode": trust,
            "trust_risk": "high" if trust != "normal" else "low",
            "category": "finops_realwork",
            "scenario_index": i,
            "why_trust_is_hard": "High-impact money movement + messy business context (PO/GRN/invoice/refund) is where agents misfire.",
        }

        # Defaults
        label = ""
        path = ""
        payload: Dict[str, Any] = {}
        amount: float | None = None

        # --- AP invoice create ---
        if action == "ap_invoice_create":
            vendor_id = rnd.choice(vendors)
            invoice_id = rnd.choice(invoices)
            inv_num = f"{rnd.choice(['A', 'B', 'C'])}{rnd.randint(10000,99999)}"
            po_id = rnd.choice(pos)
            grn_id = rnd.choice(grns)

            # Amount range (real AP)
            amount = round(rnd.uniform(250, 85_000), 2)

            flags = []
            if trust == "duplicate_invoice":
                flags.append("duplicate_invoice")
            if trust == "po_mismatch":
                flags.append("po_mismatch")
            if trust == "grn_mismatch":
                flags.append("grn_mismatch")
            if trust == "missing_supporting_docs":
                flags.append("missing_docs")
            if trust == "split_invoice_to_avoid_threshold":
                flags.append("split_threshold")

            label = f"{i:05d}) AP invoice create ${amount} ({trust})"
            path = "/api/v1/ap/invoices/create"
            payload = {
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "invoice_number": inv_num,
                "po_id": po_id,
                "grn_id": grn_id,
                "amount_usd": amount,
                "due_date": f"2026-02-{rnd.randint(1,28):02d}",
                "flags": flags,
            }

        # --- AP approve ---
        elif action == "ap_invoice_approve":
            invoice_id = rnd.choice(invoices)
            decision = "approved" if trust not in {"duplicate_invoice", "po_mismatch", "grn_mismatch"} else rnd.choice(["approved", "rejected"])
            label = f"{i:05d}) AP invoice approve ({trust})"
            path = "/api/v1/ap/invoices/approve"
            payload = {
                "invoice_id": invoice_id,
                "approver": rnd.choice(["manager@ledgerworks.com", "controller@ledgerworks.com"]),
                "decision": decision,
                "reason": "Auto-approval request by agent; may need human check" if trust != "normal" else None,
            }

        # --- AP payment create (ACH only) ---
        elif action == "ap_payment_create":
            vendor_id = rnd.choice(vendors)
            invoice_id = rnd.choice(invoices)
            amount = round(rnd.uniform(200, 120_000), 2)

            dest_last4 = str(rnd.randint(1000, 9999))
            if trust in {"bank_change_attempt", "lookalike_vendor"}:
                # simulate mismatch / suspicious change
                dest_last4 = str(rnd.randint(1000, 9999))

            label = f"{i:05d}) AP payment create ${amount} ACH ({trust})"
            path = "/api/v1/ap/payments/create"
            payload = {
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "amount_usd": amount,
                "method": "ach",
                "destination_bank_last4": dest_last4,
            }

        # --- Inventory PO create ---
        elif action == "inventory_po_create":
            vendor_id = rnd.choice(vendors)
            po_id = rnd.choice(pos)
            items = []
            for _ in range(rnd.randint(1, 4)):
                sku = rnd.choice(skus)
                qty = rnd.randint(5, 200)
                unit = round(rnd.uniform(3.5, 950.0), 2)
                items.append({"sku": sku, "qty": qty, "unit_cost_usd": unit})
            label = f"{i:05d}) Inventory PO create ({trust})"
            path = "/api/v1/inventory/po/create"
            payload = {"po_id": po_id, "vendor_id": vendor_id, "items": items}

        # --- Inventory GRN create ---
        elif action == "inventory_grn_create":
            po_id = rnd.choice(pos)
            grn_id = rnd.choice(grns)
            items_received = []
            for _ in range(rnd.randint(1, 4)):
                sku = rnd.choice(skus)
                qty_recv = rnd.randint(1, 220)
                if trust in {"grn_mismatch"}:
                    qty_recv += rnd.randint(50, 120)
                items_received.append({"sku": sku, "qty_received": qty_recv})
            label = f"{i:05d}) Inventory GRN receive ({trust})"
            path = "/api/v1/inventory/receipts/create"
            payload = {"grn_id": grn_id, "po_id": po_id, "items_received": items_received, "warehouse": rnd.choice(["WH-1", "WH-2", "WH-SEA"])}

        # --- Customer refund create ---
        elif action == "refund_create":
            cust_id = rnd.choice(customers)
            amount = round(rnd.uniform(5, 20_000), 2)
            flags = []

            destination = {"type": "original_method", "hint": "refund to original card"}
            if trust == "refund_to_new_destination":
                destination = {"type": "new_bank_account", "last4": str(rnd.randint(1000, 9999))}
                flags.append("new_destination")
            if trust == "refund_over_amount":
                flags.append("over_amount")
                amount = round(amount * rnd.uniform(1.2, 2.5), 2)
            if trust == "refund_after_chargeback":
                flags.append("after_chargeback")

            label = f"{i:05d}) Refund create ${amount} ({trust})"
            path = "/api/v1/refunds/create"
            payload = {
                "customer_id": cust_id,
                "original_payment_ref": f"pay_{rnd.randint(100000,999999)}",
                "amount_usd": amount,
                "destination": destination,
                "reason": rnd.choice(["customer_request", "service_issue", "duplicate_charge", "subscription_cancel"]),
                "flags": flags,
            }

        # --- AR invoice create ---
        elif action == "ar_invoice_create":
            cust_id = rnd.choice(customers)
            amount = round(rnd.uniform(50, 60_000), 2)
            label = f"{i:05d}) AR invoice create ${amount} ({trust})"
            path = "/api/v1/ar/invoices/create"
            payload = {"customer_id": cust_id, "amount_usd": amount, "terms": rnd.choice(["net_15", "net_30", "net_45"])}

        # --- AR payment apply ---
        elif action == "ar_payment_apply":
            ar_invoice_id = rnd.choice(ar_invs)
            amount = round(rnd.uniform(10, 75_000), 2)
            flags = []
            if trust == "ar_overpayment":
                flags.append("overpayment")
                amount = round(amount * rnd.uniform(1.1, 2.0), 2)
            if trust == "ar_payment_wrong_invoice":
                flags.append("wrong_invoice")
                ar_invoice_id = rnd.choice(ar_invs)  # still plausible; "wrong" indicated by flag

            label = f"{i:05d}) AR payment apply ${amount} ({trust})"
            path = "/api/v1/ar/payments/apply"
            payload = {
                "ar_invoice_id": ar_invoice_id,
                "payment_ref": f"custpay_{rnd.randint(100000,999999)}",
                "amount_applied_usd": amount,
                "flags": flags,
            }

        # --- Excel report generate ---
        elif action == "excel_report_generate":
            rpt_type = rnd.choice(["ap_aging", "vendor_spend", "refund_anomalies", "3way_match_exceptions", "ar_aging"])
            preview_rows = []

            # Create a small preview that looks like Excel rows
            for _ in range(rnd.randint(3, 8)):
                preview_rows.append(
                    {
                        "row": _ + 1,
                        "entity": rnd.choice(["vendor", "invoice", "refund", "po"]),
                        "ref": rnd.choice(invoices + pos + grns),
                        "value_usd": round(rnd.uniform(10, 250_000), 2),
                        "flag": rnd.choice(["", "mismatch", "duplicate", "new_destination", "threshold_split"]),
                    }
                )

            label = f"{i:05d}) Excel report generate ({trust})"
            path = "/api/v1/reports/excel/generate"
            payload = {
                "report_type": rpt_type,
                "filters": {"as_of": "2026-01-18", "department": rnd.choice(["ops", "finance", "procurement"])},
                "preview_rows": preview_rows,
            }

            if trust == "export_sensitive_report":
                risk_context["export_target"] = rnd.choice(["external_email", "public_link"])
                risk_context["note"] = "This report may contain sensitive vendor banking/refund info."

        # --- Audit append ---
        else:
            label = f"{i:05d}) Audit append ({trust})"
            path = "/api/v1/audit/append"
            payload = {"event": "scenario_executed", "scenario_index": i, "trust_mode": trust}

        exp = expected_outcome(trust, amount)
        risk_context["expected_policy_outcome"] = exp

        scenarios.append(Scenario(i=i, label=label, path=path, body=envelope(payload, risk_context), expected=exp))

    return scenarios


async def send_one(client: httpx.AsyncClient, sc: Scenario, replay_idem: str | None = None) -> Tuple[bool, int, Dict[str, Any]]:
    url = f"{BASE_URL}{sc.path}"
    corr = sc.body.get("risk_context", {}).get("correlation_id", _id("corr"))
    headers = make_headers(corr, idem=replay_idem)

    try:
        r = await client.post(url, json=sc.body, headers=headers)
        data = r.json() if "application/json" in r.headers.get("content-type", "") else {"raw": r.text}
        ok = 200 <= r.status_code < 300
        return ok, r.status_code, data
    except Exception as e:
        return False, 0, {"error": str(e), "url": url}


async def main() -> None:
    scenarios = build_scenarios(TOTAL_SCENARIOS)

    print(f"BASE_URL={BASE_URL}")
    print(f"TOTAL_SCENARIOS={len(scenarios)} | SEED={SEED}")
    print("No wires. AP/AR/refunds/inventory receivable + Excel reporting.\n")

    replay_every = 41  # simulate retries/idempotency sometimes
    success = 0

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for sc in scenarios:
            if sc.i % replay_every == 0:
                idem = _id("idem_replay")
                ok1, st1, d1 = await send_one(client, sc, replay_idem=idem)
                ok2, st2, d2 = await send_one(client, sc, replay_idem=idem)
                if ok1:
                    success += 1

                if sc.i % PRINT_EVERY == 0:
                    print("=" * 90)
                    print(sc.label)
                    print(f"EXPECTED={sc.expected} | replay_test=yes")
                    print(f"1st HTTP {st1} ok={ok1} | 2nd HTTP {st2} ok={ok2}")
                    print(json.dumps({"first": d1, "replay": d2}, indent=2)[:2500])
            else:
                ok, st, d = await send_one(client, sc)
                if ok:
                    success += 1

                if sc.i % PRINT_EVERY == 0:
                    print("=" * 90)
                    print(sc.label)
                    print(f"EXPECTED={sc.expected}")
                    print(f"HTTP {st} ok={ok} | success={success}")
                    print(json.dumps(d, indent=2)[:2500])

    print("\nDone.")
    print(f"Successful responses: {success}/{len(scenarios)}")


if __name__ == "__main__":
    asyncio.run(main())
