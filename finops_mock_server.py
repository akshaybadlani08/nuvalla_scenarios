# finops_mock_server.py
"""
Account #2 (Receiver) — FinOps Mock Server (FastAPI)
Company theme: "LedgerWorks" (finops + AP/AR + inventory receivable + refunds + reporting)

- Real HTTP endpoints (proxy/interceptor-friendly)
- Idempotency support (replays return same response)
- Receipts and audit logging
- Minimal business logic; leave enforcement to your interceptor

Run:
  pip install fastapi uvicorn
  python finops_mock_server.py

Docs:
  http://localhost:9006/docs
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="LedgerWorks FinOps Mock APIs", version="1.0")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ----------------------------
# In-memory state
# ----------------------------
STATE: Dict[str, Any] = {
    "audit": [],
    "vendors": {},             # vendor_id -> vendor
    "purchase_orders": {},      # po_id -> PO
    "receipts": {},             # grn_id -> goods receipt
    "invoices": {},             # inv_id -> invoice
    "invoice_approvals": {},    # appr_id -> approval record
    "payments": {},             # pay_id -> payment
    "refunds": {},              # rfnd_id -> refund
    "customers": {},            # cust_id -> customer
    "ar_invoices": {},          # ar_id -> receivable invoice
    "ar_payments": {},          # arp_id -> payment applied
    "reports": {},              # rpt_id -> report meta
}

# key: (method, path, idempotency_key) -> (status_code, response_json)
IDEMPOTENCY: Dict[Tuple[str, str, str], Tuple[int, Dict[str, Any]]] = {}


def require_auth(authorization: Optional[str]) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing/invalid Authorization header")


def receipt(
    *,
    domain: str,
    operation: str,
    action_id: str,
    request_id: Optional[str],
    correlation_id: Optional[str],
    idempotency_key: Optional[str],
    status: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "receipt_id": new_id("rcpt"),
        "received_at_ms": now_ms(),
        "domain": domain,
        "operation": operation,
        "action_id": action_id,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "status": status,  # ok/accepted/processing
        "result": result,
    }


async def maybe_return_idempotent(request: Request, idempotency_key: Optional[str]) -> Optional[Tuple[int, Dict[str, Any]]]:
    if not idempotency_key:
        return None
    k = (request.method, str(request.url.path), idempotency_key)
    return IDEMPOTENCY.get(k)


def store_idempotent(request: Request, idempotency_key: Optional[str], status_code: int, response_json: Dict[str, Any]) -> None:
    if not idempotency_key:
        return
    k = (request.method, str(request.url.path), idempotency_key)
    IDEMPOTENCY[k] = (status_code, response_json)


class Envelope(BaseModel):
    action_id: str
    tenant_id: str = "ledgerworks"
    environment: str = "demo"
    actor: Dict[str, Any] = Field(default_factory=lambda: {"type": "agent", "id": "agent-001"})
    risk_context: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)


# ----------------------------
# Audit
# ----------------------------
@app.post("/api/v1/audit/append")
async def audit_append(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    event = {
        "event_id": new_id("evt"),
        "at_ms": now_ms(),
        "action_id": env.action_id,
        "correlation_id": x_correlation_id,
        "risk_context": env.risk_context,
        "payload": env.payload,
    }
    STATE["audit"].append(event)

    resp = receipt(
        domain="audit",
        operation="append",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result={"event_id": event["event_id"], "audit_size": len(STATE["audit"])},
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


# ----------------------------
# Vendors
# ----------------------------
@app.post("/api/v1/vendors/create")
async def vendor_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    vendor_id = p.get("vendor_id", new_id("vnd"))
    vendor = {
        "vendor_id": vendor_id,
        "name": p.get("name"),
        "ap_email": p.get("ap_email"),
        "bank_last4": p.get("bank_last4"),
        "status": "active",
        "created_at_ms": now_ms(),
    }
    STATE["vendors"][vendor_id] = vendor

    resp = receipt(
        domain="vendors",
        operation="create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=vendor,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


# ----------------------------
# Inventory receivable: PO + GRN
# ----------------------------
@app.post("/api/v1/inventory/po/create")
async def po_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    po_id = p.get("po_id", new_id("po"))
    po = {
        "po_id": po_id,
        "vendor_id": p.get("vendor_id"),
        "items": p.get("items", []),  # [{"sku","qty","unit_cost_usd"}]
        "status": "open",
        "created_at_ms": now_ms(),
    }
    STATE["purchase_orders"][po_id] = po

    resp = receipt(
        domain="inventory",
        operation="po.create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=po,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/inventory/receipts/create")
async def grn_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    grn_id = p.get("grn_id", new_id("grn"))
    grn = {
        "grn_id": grn_id,
        "po_id": p.get("po_id"),
        "items_received": p.get("items_received", []),  # [{"sku","qty_received"}]
        "warehouse": p.get("warehouse", "WH-1"),
        "status": "received",
        "received_at_ms": now_ms(),
    }
    STATE["receipts"][grn_id] = grn

    resp = receipt(
        domain="inventory",
        operation="receipts.create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=grn,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


# ----------------------------
# Accounts payable: invoices + approval + payments
# ----------------------------
@app.post("/api/v1/ap/invoices/create")
async def ap_invoice_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    inv_id = p.get("invoice_id", new_id("inv"))
    invoice = {
        "invoice_id": inv_id,
        "vendor_id": p.get("vendor_id"),
        "invoice_number": p.get("invoice_number"),
        "po_id": p.get("po_id"),
        "grn_id": p.get("grn_id"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "due_date": p.get("due_date", "2026-02-15"),
        "status": "submitted",
        "created_at_ms": now_ms(),
        "flags": p.get("flags", []),  # e.g. ["duplicate_invoice", "po_mismatch"]
    }
    STATE["invoices"][inv_id] = invoice

    resp = receipt(
        domain="ap",
        operation="invoices.create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=invoice,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


@app.post("/api/v1/ap/invoices/approve")
async def ap_invoice_approve(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    invoice_id = p.get("invoice_id")
    if invoice_id not in STATE["invoices"]:
        raise HTTPException(status_code=404, detail="invoice_id not found")

    appr_id = new_id("appr")
    approval = {
        "approval_id": appr_id,
        "invoice_id": invoice_id,
        "approver": p.get("approver", "manager@ledgerworks.com"),
        "decision": p.get("decision", "approved"),  # approved/rejected
        "reason": p.get("reason"),
        "decided_at_ms": now_ms(),
    }
    STATE["invoice_approvals"][appr_id] = approval
    # minimal status update
    STATE["invoices"][invoice_id]["status"] = "approved" if approval["decision"] == "approved" else "rejected"

    resp = receipt(
        domain="ap",
        operation="invoices.approve",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=approval,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/ap/payments/create")
async def ap_payment_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    pay_id = new_id("pay")
    payment = {
        "payment_id": pay_id,
        "invoice_id": p.get("invoice_id"),
        "vendor_id": p.get("vendor_id"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "method": p.get("method", "ach"),
        "destination_bank_last4": p.get("destination_bank_last4"),
        "status": "queued",
        "queued_at_ms": now_ms(),
    }
    STATE["payments"][pay_id] = payment

    resp = receipt(
        domain="ap",
        operation="payments.create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=payment,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


# ----------------------------
# Customer refunds
# ----------------------------
@app.post("/api/v1/refunds/create")
async def refund_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    rfnd_id = new_id("rfnd")
    refund = {
        "refund_id": rfnd_id,
        "customer_id": p.get("customer_id"),
        "original_payment_ref": p.get("original_payment_ref"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "destination": p.get("destination", {}),
        "reason": p.get("reason", "customer_request"),
        "status": "processing",
        "created_at_ms": now_ms(),
        "flags": p.get("flags", []),
    }
    STATE["refunds"][rfnd_id] = refund

    resp = receipt(
        domain="refunds",
        operation="create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=refund,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


# ----------------------------
# Accounts receivable
# ----------------------------
@app.post("/api/v1/ar/invoices/create")
async def ar_invoice_create(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    ar_id = new_id("ar")
    ar = {
        "ar_invoice_id": ar_id,
        "customer_id": p.get("customer_id"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "terms": p.get("terms", "net_30"),
        "status": "open",
        "created_at_ms": now_ms(),
    }
    STATE["ar_invoices"][ar_id] = ar

    resp = receipt(
        domain="ar",
        operation="invoices.create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=ar,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/ar/payments/apply")
async def ar_payment_apply(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    arp_id = new_id("arp")
    applied = {
        "ar_payment_id": arp_id,
        "ar_invoice_id": p.get("ar_invoice_id"),
        "payment_ref": p.get("payment_ref"),
        "amount_applied_usd": float(p.get("amount_applied_usd", 0)),
        "status": "applied",
        "applied_at_ms": now_ms(),
        "flags": p.get("flags", []),
    }
    STATE["ar_payments"][arp_id] = applied

    resp = receipt(
        domain="ar",
        operation="payments.apply",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=applied,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


# ----------------------------
# Excel analysis (reporting)
# ----------------------------
@app.post("/api/v1/reports/excel/generate")
async def report_generate(
    request: Request,
    env: Envelope,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
    x_correlation_id: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    hit = await maybe_return_idempotent(request, idempotency_key)
    if hit:
        return hit[1]

    p = env.payload
    rpt_id = new_id("rpt")
    report = {
        "report_id": rpt_id,
        "report_type": p.get("report_type", "ap_aging"),
        "filters": p.get("filters", {}),
        "format": "xlsx",
        "status": "generated",
        "generated_at_ms": now_ms(),
        # Keep it simple: return a small preview (rows) so demo shows "excel-like" content
        "preview_rows": p.get("preview_rows", []),
    }
    STATE["reports"][rpt_id] = report

    resp = receipt(
        domain="reports",
        operation="excel.generate",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=report,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9006)
