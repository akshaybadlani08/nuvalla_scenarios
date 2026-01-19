# ledgerworks_demo_server.py
"""
LedgerWorks Demo Server (Account #2)
- Real HTTP endpoints for robust fintech demos
- Receipts + idempotency so you can show retries without double-processing
- Minimal business logic; your interceptor (Nuvalla) enforces policy decisions

Run:
  pip install fastapi uvicorn
  python ledgerworks_demo_server.py

Swagger:
  http://localhost:9006/docs
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="LedgerWorks Demo APIs", version="1.0")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ----------------------------
# In-memory state
# ----------------------------
STATE: Dict[str, Any] = {
    "audit": [],
    "vendors": {},          # vendor_id -> vendor
    "purchase_orders": {},  # po_id -> po
    "receipts": {},         # grn_id -> grn
    "invoices": {},         # invoice_id -> invoice
    "approvals": {},        # approval_id -> approval record
    "payments": {},         # payment_id -> payment
    "refunds": {},          # refund_id -> refund
    "reports": {},          # report_id -> report
}

# Idempotency:
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


async def maybe_return_idempotent(request: Request, idem: Optional[str]) -> Optional[Tuple[int, Dict[str, Any]]]:
    if not idem:
        return None
    k = (request.method, str(request.url.path), idem)
    return IDEMPOTENCY.get(k)


def store_idempotent(request: Request, idem: Optional[str], status_code: int, payload: Dict[str, Any]) -> None:
    if not idem:
        return
    k = (request.method, str(request.url.path), idem)
    IDEMPOTENCY[k] = (status_code, payload)


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
    vendor_id = p.get("vendor_id") or new_id("vnd")
    vendor = {
        "vendor_id": vendor_id,
        "name": p.get("name"),
        "ap_email": p.get("ap_email"),
        "bank_last4_verified": p.get("bank_last4_verified"),
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


# Optional: record a bank-change request (useful for demo narratives)
@app.post("/api/v1/vendors/bank-change/request")
async def vendor_bank_change_request(
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
    vendor_id = p.get("vendor_id")
    if vendor_id not in STATE["vendors"]:
        raise HTTPException(status_code=404, detail="vendor_id not found")

    req_id = new_id("bankchg")
    change_req = {
        "bank_change_request_id": req_id,
        "vendor_id": vendor_id,
        "requested_bank_last4": p.get("requested_bank_last4"),
        "source_email": p.get("source_email"),
        "status": "requested",
        "requested_at_ms": now_ms(),
    }

    # Store request on vendor record for inspection (not enforcing anything here)
    STATE["vendors"][vendor_id].setdefault("bank_change_requests", []).append(change_req)

    resp = receipt(
        domain="vendors",
        operation="bank-change.request",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=change_req,
    )
    store_idempotent(request, idempotency_key, 202, resp)
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
    po_id = p.get("po_id") or new_id("po")
    po = {
        "po_id": po_id,
        "vendor_id": p.get("vendor_id"),
        "items": p.get("items", []),
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
    grn_id = p.get("grn_id") or new_id("grn")
    grn = {
        "grn_id": grn_id,
        "po_id": p.get("po_id"),
        "items_received": p.get("items_received", []),
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
# Accounts payable: invoice + approve + payment
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
    invoice_id = p.get("invoice_id") or new_id("inv")
    invoice = {
        "invoice_id": invoice_id,
        "vendor_id": p.get("vendor_id"),
        "invoice_number": p.get("invoice_number"),
        "po_id": p.get("po_id"),
        "grn_id": p.get("grn_id"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "currency": p.get("currency", "USD"),
        "due_date": p.get("due_date", "2026-02-15"),
        "status": "submitted",
        "flags": p.get("flags", []),
        "created_at_ms": now_ms(),
    }
    STATE["invoices"][invoice_id] = invoice

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
    if not invoice_id or invoice_id not in STATE["invoices"]:
        raise HTTPException(status_code=404, detail="invoice_id not found")

    approval_id = new_id("appr")
    decision = p.get("decision", "approved")
    approval = {
        "approval_id": approval_id,
        "invoice_id": invoice_id,
        "approver": p.get("approver"),
        "decision": decision,  # approved / rejected
        "reason": p.get("reason"),
        "decided_at_ms": now_ms(),
    }
    STATE["approvals"][approval_id] = approval
    STATE["invoices"][invoice_id]["status"] = "approved" if decision == "approved" else "rejected"

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
    payment_id = new_id("pay")
    payment = {
        "payment_id": payment_id,
        "invoice_id": p.get("invoice_id"),
        "vendor_id": p.get("vendor_id"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "method": p.get("method", "ach"),
        "destination_bank_last4": p.get("destination_bank_last4"),
        "status": "queued",
        "queued_at_ms": now_ms(),
        "flags": p.get("flags", []),
    }
    STATE["payments"][payment_id] = payment

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
async def refunds_create(
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
    refund_id = new_id("rfnd")
    refund = {
        "refund_id": refund_id,
        "customer_id": p.get("customer_id"),
        "original_payment_ref": p.get("original_payment_ref"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "destination": p.get("destination", {}),
        "reason": p.get("reason", "customer_request"),
        "status": "processing",
        "flags": p.get("flags", []),
        "created_at_ms": now_ms(),
    }
    STATE["refunds"][refund_id] = refund

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
# Reports / "Excel analysis"
# ----------------------------
@app.post("/api/v1/reports/excel/generate")
async def reports_excel_generate(
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
    report_id = new_id("rpt")
    report = {
        "report_id": report_id,
        "report_type": p.get("report_type", "ap_aging"),
        "filters": p.get("filters", {}),
        "format": "xlsx",
        "status": "generated",
        "generated_at_ms": now_ms(),
        "preview_rows": p.get("preview_rows", []),
    }
    STATE["reports"][report_id] = report

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
