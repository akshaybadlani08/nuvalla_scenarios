"""
Scenario #5: "Replay the same recorded tool calls" — FINTECH ONLY

What you asked for:
- A bunch of scenarios that contain BOTH ends:
    (1) agent ACTIONS (tool-call trace)
    (2) APPROVAL events (who/role/method/time)
- You already have Nuvalla. You will "put Nuvalla in between" by wiring 2 functions:
    - nuvalla_submit_action(action_payload) -> receipt/decision
    - nuvalla_submit_approval(approval_payload) -> ack
- A printed response underneath each scenario AND each step (demo-friendly)
- Includes comments everywhere

How this harness works:
- Each scenario is a deterministic replay trace.
- For each step:
    - DIRECT mode: executes tool call immediately (unsafe baseline)
    - NUVALLA mode: calls YOUR Nuvalla first. If it returns COMMIT, we execute. If BLOCK/PENDING, we do not.
- Approvals are optionally injected between attempts.

You only need to edit the "NuvallaAdapter" class to match your engine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple
import time
import uuid
import json


# -----------------------------
# Trace model (inputs)
# -----------------------------

class Decision(str, Enum):
    COMMIT = "COMMIT"
    BLOCK = "BLOCK"
    PENDING = "PENDING_APPROVAL"
    UNDO = "UNDO"


@dataclass(frozen=True)
class ToolCall:
    """
    One agent-initiated write attempt.
    Use action_id as idempotency key.
    """
    action_id: str
    actor: str
    system: str                 # "stripe", "netsuite", "m365", "jira" etc.
    operation: str              # "payment.execute", "payout.execute", "vendor.create", "email.send"
    params: Dict[str, Any]
    txn_id: str                 # groups steps into a single business flow
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(frozen=True)
class Approval:
    """
    A human approval event. Some actions require 1 approval; some require 2 (maker-checker).
    """
    action_id: str
    approved_by: str
    role: str                   # "CFO", "RiskOfficer", "TreasuryManager", etc.
    method: str = "dashboard"
    approved_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass
class ScenarioTrace:
    name: str
    story: str
    tool_calls: List[ToolCall]

    # approvals that happen "in the world"
    approvals_timeline: List[Approval] = field(default_factory=list)

    # optional: approvals can arrive after some step index
    # if you want to model approvals arriving later, include it in approvals_timeline with a "deliver_after_step" field
    # But to keep types simple, we model delivery with a helper dict below.


# -----------------------------
# "World" simulator (fake Stripe/NetSuite/M365 state)
# -----------------------------

class MockSystems:
    """
    Deterministic fake external systems to show visible side effects.
    Replace with your real sandbox connectors if needed.
    """
    def __init__(self):
        self.state: Dict[str, Dict[str, Dict[str, Any]]] = {}   # system -> external_id -> record
        self.action_to_external: Dict[str, str] = {}            # action_id -> external_id (idempotency)

    def _new_id(self, system: str) -> str:
        return f"{system}:{uuid.uuid4().hex[:10]}"

    def execute(self, call: ToolCall) -> Dict[str, Any]:
        # Idempotency: same action_id => same external_id, no duplicate side effects
        if call.action_id in self.action_to_external:
            ext = self.action_to_external[call.action_id]
            return {"ok": True, "external_id": ext, "idempotent_replay": True}

        ext = self._new_id(call.system)
        self.action_to_external[call.action_id] = ext
        self.state.setdefault(call.system, {})[ext] = {
            "operation": call.operation,
            "params": call.params,
            "created_at_ms": int(time.time() * 1000),
            "deleted": False,
        }
        return {"ok": True, "external_id": ext, "idempotent_replay": False}

    def undo(self, call: ToolCall, external_id: str) -> Dict[str, Any]:
        rec = self.state.get(call.system, {}).get(external_id)
        if not rec:
            return {"ok": False, "error": "record_not_found"}
        rec["deleted"] = True
        return {"ok": True, "undo_id": f"undo:{external_id}"}


# -----------------------------
# You plug Nuvalla in here
# -----------------------------

@dataclass
class NuvallaReceipt:
    """
    Minimal normalized receipt for printing.
    Map your real receipt into this.
    """
    decision: Decision
    status: str                         # "success" | "blocked" | "pending_approval" | "failed"
    message: str                        # short UI line
    receipt: Dict[str, Any] = field(default_factory=dict)  # raw receipt details (optional)


class NuvallaAdapter:
    """
    EDIT THIS: connect to your Nuvalla engine.

    You need two calls:
    - submit_approval(Approval) -> None
    - intercept(call: ToolCall) -> NuvallaReceipt

    The harness doesn't care how you implement it; it just needs a Decision.
    """

    def __init__(self, nuvalla_engine: Any):
        self.nuvalla = nuvalla_engine

    def submit_approval(self, approval: Approval) -> None:
        # TODO: replace with your approval ingestion API
        #
        # Example:
        # self.nuvalla.add_approval(
        #   action_id=approval.action_id,
        #   approved_by=approval.approved_by,
        #   role=approval.role,
        #   method=approval.method,
        #   approved_at_ms=approval.approved_at_ms,
        # )
        pass

    def intercept(self, call: ToolCall) -> NuvallaReceipt:
        # TODO: replace with your action interception API
        #
        # Example:
        # receipt = self.nuvalla.execute_action(
        #   action_id=call.action_id,
        #   actor=call.actor,
        #   system=call.system,
        #   operation=call.operation,
        #   params=call.params,
        #   txn_id=call.txn_id,
        # )
        #
        # Map receipt -> Decision / status / message

        raise NotImplementedError("Wire NuvallaAdapter.intercept() to your real engine.")


# -----------------------------
# Printing helpers (your "printed response underneath each")
# -----------------------------

def j(x: Any) -> str:
    return json.dumps(x, indent=2, sort_keys=True, default=str)

def print_scenario_header(trace: ScenarioTrace) -> None:
    print("\n" + "=" * 100)
    print(f"SCENARIO: {trace.name}")
    print("-" * 100)
    print(f"Story: {trace.story}")

def print_call(call: ToolCall) -> None:
    print("\n[Agent → ToolCall]")
    print(f"  txn_id:    {call.txn_id}")
    print(f"  action_id: {call.action_id}")
    print(f"  actor:     {call.actor}")
    print(f"  system:    {call.system}")
    print(f"  operation: {call.operation}")
    print(f"  params:    {j(call.params)}")

def print_approval(approval: Approval) -> None:
    print("\n[Human → Approval]")
    print(f"  action_id:    {approval.action_id}")
    print(f"  approved_by:  {approval.approved_by}")
    print(f"  role:         {approval.role}")
    print(f"  method:       {approval.method}")
    print(f"  approved_at:  {approval.approved_at_ms}")

def print_direct_result(exec_result: Dict[str, Any]) -> None:
    print("\n[Printed response / BEFORE (Direct)]")
    if exec_result.get("ok"):
        print(f"  ✅ Executed immediately. external_id={exec_result['external_id']} replay={exec_result['idempotent_replay']}")
    else:
        print(f"  ⚠️ Direct execution failed: {exec_result}")

def print_nuvalla_result(receipt: NuvallaReceipt, commit_exec: Optional[Dict[str, Any]] = None) -> None:
    print("\n[Printed response / AFTER (With Nuvalla)]")
    if receipt.status == "blocked":
        print(f"  ❌ BLOCKED — {receipt.message}")
    elif receipt.status == "pending_approval":
        print(f"  ⏸ PENDING — {receipt.message}")
    elif receipt.decision == Decision.UNDO and receipt.status == "success":
        print(f"  🔁 COMPENSATED (UNDO) — {receipt.message}")
    elif receipt.decision == Decision.COMMIT and receipt.status == "success":
        ext = commit_exec.get("external_id") if commit_exec else None
        print(f"  ✅ COMMITTED — {receipt.message} external_id={ext}")
    else:
        print(f"  ⚠️ FAILED — {receipt.message}")

    # Optional debug/audit blob
    if receipt.receipt:
        print("\n[Audit receipt]")
        print(j(receipt.receipt))


# -----------------------------
# Replay runner
# -----------------------------

def run_fintech_replay(
    traces: Sequence[ScenarioTrace],
    adapter: NuvallaAdapter,
    show_direct_baseline: bool = True,
) -> None:
    """
    For each scenario:
    - Optionally run direct baseline (unsafe)
    - Run with Nuvalla interception
    - Deliver approvals at scheduled times
    """
    for trace in traces:
        print_scenario_header(trace)

        # DIRECT baseline (optional)
        if show_direct_baseline:
            print("\n" + "-" * 100)
            print("BASELINE: BEFORE (no Nuvalla)")
            systems_direct = MockSystems()

            for call in trace.tool_calls:
                print_call(call)
                res = systems_direct.execute(call)
                print_direct_result(res)

            print("\n[Scenario baseline summary]")
            print("  Result: agent writes executed without policy checks, approvals, receipts, or compensation.")

        # WITH Nuvalla
        print("\n" + "-" * 100)
        print("AFTER: WITH Nuvalla interception")
        systems_after = MockSystems()

        # Deliver approvals according to a schedule:
        # We encode delivery timing in approval param "deliver_after_step" if present.
        # For simplicity, we store it inside receipt dict; approvals themselves remain clean.
        approvals_schedule: List[Tuple[int, Approval]] = []
        for a in trace.approvals_timeline:
            deliver_after = int(getattr(a, "deliver_after_step", 0)) if hasattr(a, "deliver_after_step") else 0
            # If you prefer, you can encode deliver_after_step in approval.method like "dashboard@step2"
            approvals_schedule.append((deliver_after, a))

        # index approvals by step number
        approvals_by_step: Dict[int, List[Approval]] = {}
        for step_idx, appr in approvals_schedule:
            approvals_by_step.setdefault(step_idx, []).append(appr)

        for idx, call in enumerate(trace.tool_calls, start=1):
            # Deliver any approvals that should arrive *before* this step runs.
            for a in approvals_by_step.get(idx - 1, []):
                print_approval(a)
                adapter.submit_approval(a)

            print_call(call)

            # 1) Intercept with your Nuvalla
            receipt = adapter.intercept(call)

            # 2) Only commit to the external system if Nuvalla says COMMIT
            commit_res = None
            if receipt.decision == Decision.COMMIT and receipt.status == "success":
                commit_res = systems_after.execute(call)

                # Optional: show compensation demo if your Nuvalla returns UNDO, or if you flag it in params.
                # If your engine already performs undo logic internally, you can remove this block.
                if bool(call.params.get("force_post_commit_failure", False)):
                    undo_res = systems_after.undo(call, commit_res["external_id"])
                    # Your real engine would return UNDO. Here we just print extra info if you want.
                    # print("\n# Comment: Post-commit failure simulated; undo executed:", undo_res)

            print_nuvalla_result(receipt, commit_exec=commit_res)

        # Deliver approvals after last step (if any)
        for a in approvals_by_step.get(len(trace.tool_calls), []):
            print_approval(a)
            adapter.submit_approval(a)

        print("\n[Scenario AFTER summary]")
        print("  Result: writes were governed (blocked/pending/committed/compensated) with receipts.")


# -----------------------------
# FINTECH scenario pack (ALL fintech) — actions + approvals
# -----------------------------

def fintech_scenarios_pack() -> List[ScenarioTrace]:
