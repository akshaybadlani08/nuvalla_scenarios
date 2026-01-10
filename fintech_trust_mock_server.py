# fintech_trust_mock_server.py
"""
Account #2 (Receiver) — Fintech "Trust Challenge" Mock Server (FastAPI)

Purpose:
- Accept real HTTP requests from an agent
- Provide receipts + idempotency behavior
- Keep logic simple; your interceptor (Nuvalla) should enforce policy, approvals, and blocks

Run:
  pip install fastapi uvicorn
  python fintech_trust_mock_server.py

Docs:
  http://localhost:9006/docs
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="TrustPay Mock Fintech APIs", version="2.0")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ----------------------------
# In-memory state
# ----------------------------
STATE: Dict[str, Any] = {
    "audit": [],
    "accounts": {},              # account_id -> balance_usd
    "transfers": {},             # transfer_id -> details
    "vendors": {},               # vendor_id -> details
    "payouts": {},               # payout_id -> details
    "wires": {},                 # wire_id -> details
    "cards": {},                 # card_id -> details
    "card_auths": {},            # auth_id -> details
    "chargebacks": {},           # cb_id -> details
    "access_grants": {},         # grant_id -> details
    "settings_changes": {},      # change_id -> details
    "emails": {},                # email_id -> details
    "webhooks": {},              # webhook_id -> details
}

# Idempotency store:
# key: (method, path, idempotency_key) -> (status_code, response_json)
IDEMPOTENCY: Dict[Tuple[str, str, str], Tuple[int, Dict[str, Any]]] = {}


# ----------------------------
# Helpers
# ----------------------------
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


async def maybe_return_idempotent(
    request: Request,
    idempotency_key: Optional[str],
) -> Optional[Tuple[int, Dict[str, Any]]]:
    if not idempotency_key:
        return None
    k = (request.method, str(request.url.path), idempotency_key)
    return IDEMPOTENCY.get(k)


def store_idempotent(
    request: Request,
    idempotency_key: Optional[str],
    status_code: int,
    response_json: Dict[str, Any],
) -> None:
    if not idempotency_key:
        return
    k = (request.method, str(request.url.path), idempotency_key)
    IDEMPOTENCY[k] = (status_code, response_json)


# ----------------------------
# Request model
# ----------------------------
class Envelope(BaseModel):
    action_id: str
    tenant_id: str = "trustpay"
    environment: str = "demo"
    actor: Dict[str, Any] = Field(default_factory=lambda: {"type": "agent", "id": "agent-001"})
    risk_context: Dict[str, Any] = Field(default_factory=dict)  # contains trust flags
    payload: Dict[str, Any] = Field(default_factory=dict)


# ----------------------------
# Endpoints
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


@app.post("/api/v1/payments/transfer")
async def payments_transfer(
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
    amount = float(p.get("amount_usd", 0))
    from_acct = p.get("from_account", "account_1")
    to_acct = p.get("to_account", "account_2")

    STATE["accounts"].setdefault(from_acct, 1_000_000.00)
    STATE["accounts"].setdefault(to_acct, 0.00)

    transfer_id = new_id("trf")
    STATE["accounts"][from_acct] -= amount
    STATE["accounts"][to_acct] += amount

    transfer = {
        "transfer_id": transfer_id,
        "from_account": from_acct,
        "to_account": to_acct,
        "amount_usd": amount,
        "memo": p.get("memo", ""),
        "status": "posted",
        "posted_at_ms": now_ms(),
        "balances": {"from": STATE["accounts"][from_acct], "to": STATE["accounts"][to_acct]},
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["transfers"][transfer_id] = transfer

    resp = receipt(
        domain="payments",
        operation="transfer",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=transfer,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/treasury/wire")
async def treasury_wire(
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
    wire_id = new_id("wire")
    wire = {
        "wire_id": wire_id,
        "amount_usd": float(p.get("amount_usd", 0)),
        "currency": p.get("currency", "USD"),
        "destination_country": p.get("destination_country", "US"),
        "beneficiary": p.get("beneficiary", {}),
        "purpose": p.get("purpose", ""),
        "status": "processing",
        "created_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["wires"][wire_id] = wire

    resp = receipt(
        domain="treasury",
        operation="wire",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=wire,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


@app.post("/api/v1/payouts/create")
async def payouts_create(
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
    payout_id = new_id("pyt")
    payout = {
        "payout_id": payout_id,
        "method": p.get("method", "ach"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "destination": p.get("destination", {}),
        "status": "queued",
        "queued_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["payouts"][payout_id] = payout

    resp = receipt(
        domain="payouts",
        operation="create",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=payout,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


@app.post("/api/v1/vendors/create")
async def vendors_create(
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
    vendor_id = new_id("vnd")
    vendor = {
        "vendor_id": vendor_id,
        "name": p.get("name"),
        "email": p.get("email"),
        "bank_last4": p.get("bank_last4"),
        "status": "active",
        "created_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
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


@app.post("/api/v1/cards/authorize")
async def cards_authorize(
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
    auth_id = new_id("auth")
    auth = {
        "auth_id": auth_id,
        "card_id": p.get("card_id", "card_demo_1"),
        "merchant": p.get("merchant", "Unknown Merchant"),
        "mcc": p.get("mcc", "0000"),
        "amount_usd": float(p.get("amount_usd", 0)),
        "status": "approved",
        "authorized_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["card_auths"][auth_id] = auth

    resp = receipt(
        domain="cards",
        operation="authorize",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=auth,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/disputes/chargeback/open")
async def chargeback_open(
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
    cb_id = new_id("cb")
    cb = {
        "chargeback_id": cb_id,
        "transaction_ref": p.get("transaction_ref"),
        "reason": p.get("reason", "fraud"),
        "status": "open",
        "opened_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["chargebacks"][cb_id] = cb

    resp = receipt(
        domain="disputes",
        operation="chargeback.open",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=cb,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


@app.post("/api/v1/org/access/grant")
async def access_grant(
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
    grant_id = new_id("grant")
    grant = {
        "grant_id": grant_id,
        "principal": p.get("principal"),
        "role": p.get("role"),
        "scope": p.get("scope", []),
        "status": "applied",
        "applied_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["access_grants"][grant_id] = grant

    resp = receipt(
        domain="org",
        operation="access.grant",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=grant,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/org/settings/change")
async def settings_change(
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
    change_id = new_id("chg")
    change = {
        "change_id": change_id,
        "setting": p.get("setting"),
        "new_value": p.get("new_value"),
        "status": "applied",
        "applied_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["settings_changes"][change_id] = change

    resp = receipt(
        domain="org",
        operation="settings.change",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=change,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


@app.post("/api/v1/notifications/email/send")
async def email_send(
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
    email_id = new_id("eml")
    email = {
        "email_id": email_id,
        "to": p.get("to"),
        "subject": p.get("subject"),
        "body_hint": p.get("body_hint", ""),
        "is_external": bool(p.get("is_external", True)),
        "status": "queued",
        "queued_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["emails"][email_id] = email

    resp = receipt(
        domain="notifications",
        operation="email.send",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="accepted",
        result=email,
    )
    store_idempotent(request, idempotency_key, 202, resp)
    return resp


@app.post("/api/v1/integrations/webhooks/register")
async def webhook_register(
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
    webhook_id = new_id("wh")
    webhook = {
        "webhook_id": webhook_id,
        "url": p.get("url"),
        "events": p.get("events", []),
        "status": "active",
        "created_at_ms": now_ms(),
        "trust": env.risk_context.get("trust_failure_mode"),
    }
    STATE["webhooks"][webhook_id] = webhook

    resp = receipt(
        domain="integrations",
        operation="webhooks.register",
        action_id=env.action_id,
        request_id=x_request_id,
        correlation_id=x_correlation_id,
        idempotency_key=idempotency_key,
        status="ok",
        result=webhook,
    )
    store_idempotent(request, idempotency_key, 200, resp)
    return resp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9006)
