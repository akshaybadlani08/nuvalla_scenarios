"""
#5 "Agent Misbehavior Replay" demo

Goal:
- You already have Nuvalla. This harness replays a *pre-recorded* tool-call trace.
- Run the trace in two modes:
    (A) DIRECT: execute tool calls directly (no governance)
    (B) NUVALLA: send each call through your Nuvalla interception layer

You get:
- A deterministic, dramatic before/after demo
- Printed output under each action AND a scenario summary
- Fintech + healthcare traces included
- Comments everywhere

How to use:
1) Implement NuvallaHook.evaluate(...) to call your Nuvalla engine.
   - It should return a decision: COMMIT / BLOCK / REQUIRE_APPROVAL / UNDO
   - It should optionally return a receipt dict
2) Optionally implement how approvals get applied (in approve()).
3) Run this file. It prints BEFORE and AFTER side-by-side (sequentially).

This doesn't require a real agent model. The "agent" is the recorded trace.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import time
import uuid
import json


# -----------------------------
# Data model: recorded trace
# -----------------------------

class Decision(str, Enum):
    COMMIT = "COMMIT"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    UNDO = "UNDO"


@dataclass(frozen=True)
class ToolCall:
    """
    One "write attempt" the agent makes.
    action_id is stable across retries and is the idempotency key.
    """
    action_id: str
    actor: str
    system: str          # e.g. "stripe", "netsuite", "m365", "ehr"
    operation: str       # e.g. "payment.execute", "vendor.create", "email.send"
    params: Dict[str, Any]
    # Optional tag to group steps that should be treated as a single "business transaction"
    txn_id: str = "txn_default"
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(frozen=True)
class Approval:
    """
    Approval events that appear *in the trace* (or can be injected).
    Example: CFO approval for a large payment.
    """
    action_id: str
    approved_by: str
    role: str
    method: str = "dashboard"
    approved_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class ScenarioTrace:
    """
    A full scenario: a sequence of tool calls (agent behavior) + optional approvals.
    """
    name: str
    domain: str  # "fintech" | "healthcare"
    story: str
    tool_calls: List[ToolCall]
    approvals: List[Approval] = field(default_factory=list)


# -----------------------------
# "Systems" simulator (direct execution)
# -----------------------------

class MockSystems:
    """
    A tiny fake "world state" so direct execution produces visible outcomes.
    Replace with your sandbox systems if you have them.
    """
    def __init__(self):
        self.state: Dict[str, Dict[str, Dict[str, Any]]] = {}  # system -> external_id -> record
        self.idempotency: Dict[str, str] = {}                  # action_id -> external_id

    def _new_id(self, system: str) -> str:
        return f"{system}:{uuid.uuid4().hex[:10]}"

    def execute_direct(self, call: ToolCall) -> Dict[str, Any]:
        """
        DIRECT mode: always executes. No policy. No approvals. No simulation.
        This is intentionally unsafe to demonstrate why Nuvalla matters.
        """
        # Idempotent by action_id (simulating a tool that supports idempotency)
        if call.action_id in self.idempotency:
            ext_id = self.idempotency[call.action_id]
            return {"ok": True, "external_id": ext_id, "idempotent_replay": True}

        ext_id = self._new_id(call.system)
        self.idempotency[call.action_id] = ext_id

        self.state.setdefault(call.system, {})[ext_id] = {
            "operation": call.operation,
            "params": call.params,
            "created_at_ms": int(time.time() * 1000),
            "deleted": False,
        }
        return {"ok": True, "external_id": ext_id, "idempotent_replay": False}

    def undo(self, call: ToolCall, committed_external_id: str) -> Dict[str, Any]:
        """
        Used in NUVALLA mode when your hook returns UNDO.
        """
        rec = self.state.get(call.system, {}).get(committed_external_id)
        if not rec:
            return {"ok": False, "error": "record_not_found"}
        rec["deleted"] = True
        return {"ok": True, "undo_id": f"undo:{committed_external_id}"}

    def get_record(self, system: str, external_id: str) -> Optional[Dict[str, Any]]:
        return self.state.get(system, {}).get(external_id)


# -----------------------------
# Hook points: integrate YOUR Nuvalla
# -----------------------------

@dataclass
class NuvallaEval:
    decision: Decision
    message: str
    # if decision is COMMIT, include commit_allowed=True
    commit_allowed: bool = False
    # if decision is REQUIRE_APPROVAL, include what approvals are needed
    required_approvals: int = 0
    receipt: Dict[str, Any] = field(default_factory=dict)


class NuvallaHook:
    """
    Replace this class with calls into your Nuvalla engine.

    You will implement:
    - approve(approval): store approvals (or pass through to your engine)
    - evaluate(call): return decision + receipt
    """
    def __init__(self):
        self._approvals_by_action: Dict[str, List[Approval]] = {}

    def approve(self, approval: Approval) -> None:
        # If your engine has a real approvals store, call it here.
        self._approvals_by_action.setdefault(approval.action_id, []).append(approval)

    def evaluate(self, call: ToolCall) -> NuvallaEval:
        """
        TODO: Replace this with your Nuvalla evaluation.
        Must return one of:
        - BLOCK: no execution
        - REQUIRE_APPROVAL: no execution until approvals are present
        - COMMIT: execute tool call
        - UNDO: execute then compensate (or if your engine already compensates, just report UNDO)

        For now, we provide a simple demo policy to make the harness runnable.
        """
        approvals = self._approvals_by_action.get(call.action_id, [])

        # --- Example rules (swap for your real engine policy) ---
        if call.system == "stripe" and call.operation == "payment.execute":
            amt = int(call.params.get("amount_usd", 0))
            if amt > 10_000 and not approvals:
                return NuvallaEval(
                    decision=Decision.REQUIRE_APPROVAL,
                    message=f"Payment ${amt} requires CFO approval",
                    commit_allowed=False,
                    required_approvals=1,
                    receipt={"policy": "payment_threshold", "amount_usd": amt}
                )
            return NuvallaEval(
                decision=Decision.COMMIT,
                message=f"Payment ${amt} allowed (policy ok)",
                commit_allowed=True,
                receipt={"policy": "payment_threshold", "amount_usd": amt, "approvals": [a.__dict__ for a in approvals]}
            )

        if call.system == "netsuite" and call.operation == "vendor.create":
            if not call.params.get("kyc_passed", False):
                return NuvallaEval(
                    decision=Decision.BLOCK,
                    message="Vendor creation blocked: KYC not passed",
                    commit_allowed=False,
                    receipt={"policy": "vendor_kyc_required"}
                )
            return NuvallaEval(
                decision=Decision.COMMIT,
                message="Vendor creation allowed: KYC passed",
                commit_allowed=True,
                receipt={"policy": "vendor_kyc_required"}
            )

        if call.system == "m365" and call.operation == "email.send":
            to = str(call.params.get("to", ""))
            contains_phi = bool(call.params.get("contains_phi", False))
            # allowlist for demo
            allow_domain = "acmefinco.com"
            domain = to.split("@")[-1] if "@" in to else ""
            if contains_phi and domain != allow_domain:
                return NuvallaEval(
                    decision=Decision.BLOCK,
                    message="Email blocked: external PHI (HIPAA rule)",
                    commit_allowed=False,
                    receipt={"policy": "hipaa_no_external_phi", "to": to}
                )
            if domain != allow_domain:
                return NuvallaEval(
                    decision=Decision.BLOCK,
                    message="Email blocked: domain not allowlisted",
                    commit_allowed=False,
                    receipt={"policy": "email_allowlist", "to": to}
                )
            return NuvallaEval(
                decision=Decision.COMMIT,
                message="Email allowed (domain allowlist ok)",
                commit_allowed=True,
                receipt={"policy": "email_allowlist", "to": to}
            )

        if call.system == "ehr" and call.operation == "medication.order":
            # attending approval required
            if not approvals:
                return NuvallaEval(
                    decision=Decision.REQUIRE_APPROVAL,
                    message="Medication order requires attending sign-off",
                    commit_allowed=False,
                    required_approvals=1,
                    receipt={"policy": "ehr_attending_signoff"}
                )
            return NuvallaEval(
                decision=Decision.COMMIT,
                message="Medication order approved and allowed",
                commit_allowed=True,
                receipt={"policy": "ehr_attending_signoff", "approvals": [a.__dict__ for a in approvals]}
            )

        # Default allow
        return NuvallaEval(
            decision=Decision.COMMIT,
            message="Allowed (no matching restrictive policy)",
            commit_allowed=True,
            receipt={"policy": "default_allow"}
        )


# -----------------------------
# Printing helpers
# -----------------------------

def j(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)

def print_big(title: str) -> None:
    print("\n" + "=" * 92)
    print(title)
    print("=" * 92)

def print_step(call: ToolCall) -> None:
    print("\n[Agent tool call]")
    print(f"  action_id: {call.action_id}  txn_id: {call.txn_id}")
    print(f"  actor:     {call.actor}")
    print(f"  system:    {call.system}")
    print(f"  op:        {call.operation}")
    print(f"  params:    {j(call.params)}")

def print_direct_response(result: Dict[str, Any]) -> None:
    print("\n[Printed response / WITHOUT Nuvalla]")
    if result.get("ok"):
        print(f"  ✅ Executed directly. external_id={result.get('external_id')} replay={result.get('idempotent_replay')}")
    else:
        print(f"  ⚠️ Direct execution failed: {result}")

def print_nuvalla_response(evalr: NuvallaEval, exec_result: Optional[Dict[str, Any]]) -> None:
    print("\n[Printed response / WITH Nuvalla]")
    if evalr.decision == Decision.BLOCK:
        print(f"  ❌ BLOCKED — {evalr.message}")
    elif evalr.decision == Decision.REQUIRE_APPROVAL:
        print(f"  ⏸ PENDING APPROVAL — {evalr.message} (needed={evalr.required_approvals})")
    elif evalr.decision == Decision.COMMIT:
        print(f"  ✅ COMMITTED — {evalr.message} external_id={exec_result.get('external_id') if exec_result else None}")
    elif evalr.decision == Decision.UNDO:
        print(f"  🔁 UNDO — {evalr.message}")
    else:
        print(f"  ⚠️ Unknown decision: {evalr.decision}")

    print("\n[Debug receipt]")
    print(j(evalr.receipt))


# -----------------------------
# Replay engine: BEFORE vs AFTER
# -----------------------------

def run_trace_direct(trace: ScenarioTrace, systems: MockSystems) -> None:
    print_big(f"BEFORE (No Nuvalla) — {trace.name} [{trace.domain}]")
    print(f"Story: {trace.story}")

    for call in trace.tool_calls:
        print_step(call)
        result = systems.execute_direct(call)
        print_direct_response(result)

    print("\n[Scenario summary / direct]")
    print("  Result: actions executed without policy checks, approvals, receipts, or compensation.")


def run_trace_with_nuvalla(trace: ScenarioTrace, systems: MockSystems, hook: NuvallaHook) -> None:
    print_big(f"AFTER (With Nuvalla) — {trace.name} [{trace.domain}]")
    print(f"Story: {trace.story}")

    # Feed approvals upfront (the trace can include them)
    for a in trace.approvals:
        hook.approve(a)

    for call in trace.tool_calls:
        print_step(call)

        # 1) Evaluate through Nuvalla
        evalr = hook.evaluate(call)

        # 2) If allowed to commit, execute; otherwise don't
        exec_result: Optional[Dict[str, Any]] = None
        if evalr.decision == Decision.COMMIT and evalr.commit_allowed:
            exec_result = systems.execute_direct(call)

            # Optional: demonstrate compensation if your scenario flags it
            if bool(call.params.get("force_post_commit_failure", False)):
                # In real Nuvalla you'd trigger UNDO from your engine.
                # Here we simulate: mark UNDO and undo the record.
                undo_res = systems.undo(call, exec_result["external_id"])
                evalr = NuvallaEval(
                    decision=Decision.UNDO,
                    message="Post-commit failure detected; compensating undo executed",
                    commit_allowed=False,
                    receipt={**evalr.receipt, "post_commit_failure": True, "undo": undo_res}
                )

        print_nuvalla_response(evalr, exec_result)

    print("\n[Scenario summary / with Nuvalla]")
    print("  Result: writes were intercepted and governed (block/pending/commit/undo) with receipts.")


# -----------------------------
# Trace library (fintech + healthcare)
# -----------------------------

def build_traces() -> List[ScenarioTrace]:
    traces: List[ScenarioTrace] = []

    # 1) Fintech fraud-ish invoice flow
    traces.append(ScenarioTrace(
        name="Fintech: Fraud-ish vendor invoice + external audit email",
        domain="fintech",
        story=(
            "Agent receives an invoice that looks real but vendor KYC isn't complete, "
            "then tries to email external auditors. Without Nuvalla this proceeds; with Nuvalla it is blocked."
        ),
        tool_calls=[
            ToolCall(
                action_id="fin_t1_vendor",
                actor="ap_agent",
                system="netsuite",
                operation="vendor.create",
                params={"vendor_name": "ShadyCo LLC", "kyc_passed": False},
                txn_id="txn_fin_1001"
            ),
            ToolCall(
                action_id="fin_t1_pay",
                actor="ap_agent",
                system="stripe",
                operation="payment.execute",
                params={"amount_usd": 18_750, "to_vendor": "ShadyCo LLC", "invoice_id": "INV-FAKE-77"},
                txn_id="txn_fin_1001"
            ),
            ToolCall(
                action_id="fin_t1_email",
                actor="ap_agent",
                system="m365",
                operation="email.send",
                params={"to": "auditor@big4.com", "subject": "Invoice paid", "body": "FYI", "contains_phi": False},
                txn_id="txn_fin_1001"
            ),
        ],
        approvals=[
            # In the AFTER run, you can add an approval to show it unblocks the big payment.
            Approval(action_id="fin_t1_pay", approved_by="cfo@acmefinco.com", role="CFO", method="dashboard")
        ]
    ))

    # 2) Fintech: Large payment pending → approval → commit (single step trace + approval)
    traces.append(ScenarioTrace(
        name="Fintech: Large payment requires approval",
        domain="fintech",
        story="Agent attempts a $25k payment. Nuvalla gates for CFO approval. Direct path pays instantly.",
        tool_calls=[
            ToolCall(
                action_id="fin_t2_pay",
                actor="finance_agent",
                system="stripe",
                operation="payment.execute",
                params={"amount_usd": 25_000, "to_vendor": "DataProviderInc", "invoice_id": "INV-9888"},
                txn_id="txn_fin_2002"
            )
        ],
        approvals=[
            Approval(action_id="fin_t2_pay", approved_by="cfo@acmefinco.com", role="CFO", method="dashboard")
        ]
    ))

    # 3) Healthcare: PHI email + med order
    traces.append(ScenarioTrace(
        name="Healthcare: PHI email + medication order",
        domain="healthcare",
        story="Clinical agent tries to email PHI externally and place a medication order without attending sign-off.",
        tool_calls=[
            ToolCall(
                action_id="hlth_t1_email_phi",
                actor="clinical_agent",
                system="m365",
                operation="email.send",
                params={"to": "patient@gmail.com", "subject": "Discharge Summary", "body": "Attached", "contains_phi": True},
                txn_id="txn_hlth_3003"
            ),
            ToolCall(
                action_id="hlth_t1_med",
                actor="clinical_agent",
                system="ehr",
                operation="medication.order",
                params={"patient_id": "PT-10012", "drug": "Heparin", "dose": "5000u", "frequency": "q8h"},
                txn_id="txn_hlth_3003"
            ),
        ],
        approvals=[
            Approval(action_id="hlth_t1_med", approved_by="attending_md@hospital.org", role="AttendingMD", method="ehr_signoff")
        ]
    ))

    # 4) Insurance: commit then compensate
    traces.append(ScenarioTrace(
        name="Insurance: Claims reimbursement with compensation",
        domain="fintech",
        story="Agent pays a claim; downstream reconciliation fails; Nuvalla compensates with undo.",
        tool_calls=[
            ToolCall(
                action_id="ins_t1_pay",
                actor="claims_agent",
                system="stripe",
                operation="payment.execute",
                params={"amount_usd": 2_400, "to_vendor": "MemberReimbursement", "claim_id": "CLM-55210", "force_post_commit_failure": True},
                txn_id="txn_ins_4004"
            )
        ],
        approvals=[],
    ))

    # 5) Idempotent retry replay
    traces.append(ScenarioTrace(
        name="Fintech: Idempotent retry replay (same action_id twice)",
        domain="fintech",
        story="Network glitch replays the same tool call twice; tool layer should not double-pay.",
        tool_calls=[
            ToolCall(
                action_id="fin_t5_pay",
                actor="finance_agent",
                system="stripe",
                operation="payment.execute",
                params={"amount_usd": 850, "to_vendor": "CloudHostingCo", "invoice_id": "INV-9231"},
                txn_id="txn_fin_5005"
            ),
            ToolCall(
                action_id="fin_t5_pay",  # SAME action_id (idempotency key)
                actor="finance_agent",
                system="stripe",
                operation="payment.execute",
                params={"amount_usd": 850, "to_vendor": "CloudHostingCo", "invoice_id": "INV-9231"},
                txn_id="txn_fin_5005"
            )
        ],
        approvals=[],
    ))

    return traces


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    traces = build_traces()

    # BEFORE: direct (no governance)
    systems_direct = MockSystems()
    for t in traces:
        run_trace_direct(t, systems_direct)

    # AFTER: with Nuvalla interception
    systems_nuvalla = MockSystems()
    hook = NuvallaHook()  # TODO: replace with your real integration
    for t in traces:
        run_trace_with_nuvalla(t, systems_nuvalla, hook)

    print_big("DONE")
    print("Next step: Replace NuvallaHook.evaluate/approve with calls into your real Nuvalla engine.")


if __name__ == "__main__":
    main()
