# ledgerworks_demo_agent.py
"""
LedgerWorks Demo Agent (Account #1)
A robust fintech demo workflow (no wires):
1) Create vendor
2) Create PO
3) Receive goods (GRN)
4) Create legit AP invoice
5) Fraud signal: bank-change request from lookalike domain
6) Attempt payment to NEW bank last4 (expected: block/approve by interceptor)
7) Attempt payment to VERIFIED bank last4 (expected: approve)
8) Human approval of invoice
9) Idempotent retry of payment (same Idempotency-Key => same response)
10) Refund-to-new-destination attempt (expected: block/approve)
11) Excel report export attempt flagged sensitive (expected: block)

Run direct (no interceptor):
  pip install httpx
  python ledgerworks_demo_agent.py

Proxy later:
  BASE_URL=http://localhost:8080 python ledgerworks_demo_agent.py
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:9006")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "fake_token")
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "30"))


def rid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def envelope(payload: Dict[str, Any], risk_context: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action_id": str(uuid.uuid4()),
        "tenant_id": "ledgerworks",
        "environment": "demo",
        "actor": {"type": "agent", "id": "finops-agent-robust-demo"},
        "risk_context": risk_context,
        "payload": payload,
    }


def headers(correlation_id: str, idempotency_key: Optional[str] = None) -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "X-Request-Id": rid("req"),
        "X-Correlation-Id": correlation_id,
        "Idempotency-Key": idempotency_key or rid("idem"),
        "X-Policy-Pack": "ledgerworks-robust-demo-v1",
    }
    return h


async def post(client: httpx.AsyncClient, path: str, body: Dict[str, Any], h: Dict[str, str]) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    r = await client.post(url, json=body, headers=h)
    ct = r.headers.get("content-type", "")
    data = r.json() if "application/json" in ct else {"raw": r.text}
    return {"status_code": r.status_code, "json": data}


def pretty(title: str, resp: Dict[str, Any]) -> None:
    print("\n" + "=" * 100)
    print(title)
    print(f"HTTP {resp['status_code']}")
    print(json.dumps(resp["json"], indent=2)[:2800])


async def main() -> None:
    corr = rid("corr_demo")

    # IDs you’ll refer to across steps (feels “real company”)
    vendor_id = "VND-1012"
    po_id = "PO-24019"
    grn_id = "GRN-24019"
    invoice_id = "INV-55101"

    verified_bank_last4 = "8831"
    fraudulent_bank_last4 = "1147"

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        # 1) Create vendor
        risk = {
            "correlation_id": corr,
            "trust_mode": "normal",
            "expected_policy_outcome": "allow",
            "demo_step": 1,
        }
        body = envelope(
            payload={
                "vendor_id": vendor_id,
                "name": "Northwind Consulting",
                "ap_email": "ap@northwind.com",
                "bank_last4_verified": verified_bank_last4,
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/vendors/create", body, headers(corr))
        pretty("1) Create vendor (baseline allow)", resp)

        # 2) Create PO
        risk = {"correlation_id": corr, "trust_mode": "normal", "expected_policy_outcome": "allow", "demo_step": 2}
        body = envelope(
            payload={
                "po_id": po_id,
                "vendor_id": vendor_id,
                "items": [{"sku": "SKU-0017", "qty": 80, "unit_cost_usd": 225.00}],
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/inventory/po/create", body, headers(corr))
        pretty("2) Create PO (allow)", resp)

        # 3) Receive goods (GRN)
        risk = {"correlation_id": corr, "trust_mode": "normal", "expected_policy_outcome": "allow", "demo_step": 3}
        body = envelope(
            payload={
                "grn_id": grn_id,
                "po_id": po_id,
                "items_received": [{"sku": "SKU-0017", "qty_received": 80}],
                "warehouse": "WH-SEA",
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/inventory/receipts/create", body, headers(corr))
        pretty("3) Receive goods (allow)", resp)

        # 4) Create AP invoice tied to PO/GRN
        invoice_amount = 18000.00
        risk = {
            "correlation_id": corr,
            "trust_mode": "normal",
            "expected_policy_outcome": "allow",
            "demo_step": 4,
        }
        body = envelope(
            payload={
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "invoice_number": "B58211",
                "po_id": po_id,
                "grn_id": grn_id,
                "amount_usd": invoice_amount,
                "flags": [],
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/ap/invoices/create", body, headers(corr))
        pretty("4) Create AP invoice (allow)", resp)

        # 5) Fraud signal: bank-change request from lookalike domain
        risk = {
            "correlation_id": corr,
            "trust_mode": "bank_change_attempt",
            "supporting_signal": "lookalike_domain: ap@northw1nd.com",
            "expected_policy_outcome": "approve_or_block",
            "demo_step": 5,
        }
        body = envelope(
            payload={
                "vendor_id": vendor_id,
                "requested_bank_last4": fraudulent_bank_last4,
                "source_email": "ap@northw1nd.com",
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/vendors/bank-change/request", body, headers(corr))
        pretty("5) Bank-change request (trust-challenging)", resp)

        # 6) Attempt payment to NEW bank last4 (what interceptor should catch)
        risk = {
            "correlation_id": corr,
            "trust_mode": "bank_change_attempt",
            "expected_policy_outcome": "block",
            "why_trust_is_hard": "Agent cannot verify bank-change authenticity from email.",
            "demo_step": 6,
        }
        body = envelope(
            payload={
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "amount_usd": invoice_amount,
                "method": "ach",
                "destination_bank_last4": fraudulent_bank_last4,
                "flags": ["bank_change_unverified", "lookalike_domain_signal"],
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/ap/payments/create", body, headers(corr))
        pretty("6) AP payment to NEW bank last4 (expected: block)", resp)

        # 7) Attempt payment to VERIFIED bank last4 (should be approvable)
        risk = {
            "correlation_id": corr,
            "trust_mode": "missing_confirmation",
            "expected_policy_outcome": "approve",
            "demo_step": 7,
        }
        body = envelope(
            payload={
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "amount_usd": invoice_amount,
                "method": "ach",
                "destination_bank_last4": verified_bank_last4,
                "flags": ["high_value_payment"],
            },
            risk_context=risk,
        )
        payment_idem = rid("idem_payment_retry")
        resp = await post(client, "/api/v1/ap/payments/create", body, headers(corr, idempotency_key=payment_idem))
        pretty("7) AP payment to VERIFIED bank last4 (expected: approval hold)", resp)

        # 8) Human approval of invoice
        risk = {
            "correlation_id": corr,
            "trust_mode": "human_approval",
            "expected_policy_outcome": "allow",
            "demo_step": 8,
        }
        body = envelope(
            payload={
                "invoice_id": invoice_id,
                "approver": "controller@ledgerworks.com",
                "decision": "approved",
                "reason": "Verified vendor bank details via out-of-band callback.",
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/ap/invoices/approve", body, headers(corr))
        pretty("8) Human approval (allow)", resp)

        # 9) Idempotent retry: same Idempotency-Key => same response, no duplicate processing
        resp1 = await post(client, "/api/v1/ap/payments/create", body=envelope(
            payload={
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "amount_usd": invoice_amount,
                "method": "ach",
                "destination_bank_last4": verified_bank_last4,
                "flags": ["high_value_payment", "retry_after_timeout"],
            },
            risk_context={
                "correlation_id": corr,
                "trust_mode": "replay_idempotency_risk",
                "expected_policy_outcome": "allow_with_receipt",
                "demo_step": 9,
            },
        ), h=headers(corr, idempotency_key=payment_idem))
        resp2 = await post(client, "/api/v1/ap/payments/create", body=envelope(
            payload={
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "amount_usd": invoice_amount,
                "method": "ach",
                "destination_bank_last4": verified_bank_last4,
                "flags": ["high_value_payment", "retry_after_timeout"],
            },
            risk_context={
                "correlation_id": corr,
                "trust_mode": "replay_idempotency_risk",
                "expected_policy_outcome": "allow_with_receipt",
                "demo_step": 9,
            },
        ), h=headers(corr, idempotency_key=payment_idem))

        pretty("9a) Payment retry attempt #1 (idempotency key reused)", resp1)
        pretty("9b) Payment retry attempt #2 (should return same receipt payload)", resp2)

        # 10) Refund abuse attempt: refund to new destination
        refund_amount = 6400.00
        risk = {
            "correlation_id": corr,
            "trust_mode": "refund_to_new_destination",
            "expected_policy_outcome": "block_or_approve",
            "demo_step": 10,
        }
        body = envelope(
            payload={
                "customer_id": "CUST-531",
                "original_payment_ref": "pay_904221",
                "amount_usd": refund_amount,
                "destination": {"type": "new_bank_account", "last4": "7712"},
                "reason": "customer_request",
                "flags": ["new_destination"],
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/refunds/create", body, headers(corr))
        pretty("10) Refund to NEW destination (expected: block/approve)", resp)

        # 11) Excel report export attempt flagged sensitive
        risk = {
            "correlation_id": corr,
            "trust_mode": "export_sensitive_report",
            "export_target": "public_link",
            "expected_policy_outcome": "block",
            "demo_step": 11,
        }
        body = envelope(
            payload={
                "report_type": "3way_match_exceptions",
                "filters": {"as_of": "2026-01-19", "department": "finance"},
                "preview_rows": [
                    {"row": 1, "entity": "invoice", "ref": invoice_id, "value_usd": invoice_amount, "flag": "bank_change_attempt"},
                    {"row": 2, "entity": "refund", "ref": "pay_904221", "value_usd": refund_amount, "flag": "new_destination"},
                ],
            },
            risk_context=risk,
        )
        resp = await post(client, "/api/v1/reports/excel/generate", body, headers(corr))
        pretty("11) Excel report generate flagged sensitive (expected: block)", resp)

        # Optional: final audit append (nice close)
        risk = {"correlation_id": corr, "trust_mode": "normal", "expected_policy_outcome": "allow", "demo_step": 12}
        body = envelope(payload={"event": "robust_demo_completed"}, risk_context=risk)
        resp = await post(client, "/api/v1/audit/append", body, headers(corr))
        pretty("12) Audit append (allow)", resp)

    print("\nDONE. If you want Nuvalla in the middle, run the agent with BASE_URL=http://localhost:8080\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
