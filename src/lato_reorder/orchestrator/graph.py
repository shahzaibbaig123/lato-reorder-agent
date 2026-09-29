"""The reorder workflow as a LangGraph StateGraph.

START -> fetch_snapshot -> reconcile_ledger -> decide -> narrate -> persist_plan -> submit_auto
      -> human_review [interrupt] -> submit_approved -> report -> END

Design rules that make this safe on the money path:
  * The ledger is the system of record for intents, approvals and POs. The checkpointer only records
    where the workflow is (thread_id = run_id); graph state holds the snapshot and plan JSON.
  * A resumed node re-runs from its start. `human_review` therefore does nothing but read the ledger
    before calling interrupt(), so replaying it is harmless.
  * SAP is only called from the submit_* nodes, never from a node that interrupts, and every call is
    guarded by the ledger's APPROVED -> SUBMITTING compare-and-set. A replayed submit node cannot place a
    second PO. No retry policy is attached to submit nodes.
"""

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from lato_reorder.domain.ingest import build_snapshot
from lato_reorder.domain.models import Snapshot
from lato_reorder.execution.submitter import SubmitRefused, SubmitReport, Submitter, submit_lock
from lato_reorder.ledger.repo import Ledger
from lato_reorder.ledger.states import IntentStatus
from lato_reorder.mcp_gateway.client import McpClient
from lato_reorder.mcp_gateway.inventory import fetch_raw
from lato_reorder.plan import ReorderPlan, build_plan
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.report.render import save_plan
from lato_reorder.settings import Mode

Narrator = Callable[[ReorderPlan, Snapshot], Awaitable[ReorderPlan]]


class ReorderState(TypedDict, total=False):
    run_id: str
    mode: str
    snapshot: dict[str, Any]
    reconcile: dict[str, Any]
    plan: dict[str, Any]
    persisted: dict[str, Any]
    auto_submit: dict[str, Any]
    review: dict[str, Any]
    submit: dict[str, Any]
    files: list[str]
    status: str


@dataclass
class Deps:
    policy: PolicyConfig
    ledger: Ledger
    client_factory: Callable[[], McpClient]
    clock: Callable[[], datetime]
    mode: Mode
    plans_dir: Path
    lock_path: Path
    submit_enabled: bool = False
    execute: bool = False
    narrator: Narrator | None = None


def _report_dict(report: SubmitReport) -> dict[str, Any]:
    return {
        "write_calls": report.write_calls,
        "breaker_tripped": report.breaker_tripped,
        "results": [asdict(r) for r in report.results],
    }


def build_graph(deps: Deps) -> StateGraph[ReorderState]:
    submitter = Submitter(
        deps.ledger,
        deps.policy,
        deps.client_factory,
        mode=deps.mode,
        submit_enabled=deps.submit_enabled,
        lock_path=deps.lock_path,
        clock=deps.clock,
    )

    async def submit(intent_ids: list[str]) -> dict[str, Any]:
        if not intent_ids:
            return {"skipped": "nothing approved"}
        try:
            return _report_dict(await submitter.submit(intent_ids, execute=deps.execute))
        except SubmitRefused as exc:
            return {"refused": str(exc)}

    async def fetch_snapshot(state: ReorderState) -> ReorderState:
        async with deps.client_factory() as client:
            products, parts = await fetch_raw(client)  # includes the tool-contract check
        snapshot = build_snapshot(products, parts, fetched_at=deps.clock())
        return {"snapshot": snapshot.model_dump(mode="json")}

    async def reconcile_ledger(state: ReorderState) -> ReorderState:
        now = deps.clock()
        snapshot = Snapshot.model_validate(state["snapshot"])
        try:
            # Holding the submit lock proves no submitter is mid-call, so a SUBMITTING intent is a crash.
            with submit_lock(deps.lock_path):
                swept = deps.ledger.startup_sweep(now)
        except SubmitRefused:
            swept = {**deps.ledger.startup_sweep(now, recover_in_flight=False), "submitter_running": []}
        received = deps.ledger.apply_receipts(snapshot, now)
        return {"reconcile": {**swept, "received": received}}

    async def decide(state: ReorderState) -> ReorderState:
        now = deps.clock()
        snapshot = Snapshot.model_validate(state["snapshot"])
        view = deps.ledger.ledger_view(now)
        plan = build_plan(snapshot, deps.policy, view, mode=deps.mode.value, now=now, run_id=state["run_id"])
        return {"plan": plan.model_dump(mode="json")}

    async def narrate(state: ReorderState) -> ReorderState:
        if deps.narrator is None:
            return {}
        plan = ReorderPlan.model_validate(state["plan"])
        snapshot = Snapshot.model_validate(state["snapshot"])
        return {"plan": (await deps.narrator(plan, snapshot)).model_dump(mode="json")}

    async def persist_plan(state: ReorderState) -> ReorderState:
        plan = ReorderPlan.model_validate(state["plan"])
        return {"persisted": asdict(deps.ledger.persist_plan(plan, deps.clock()))}

    async def submit_auto(state: ReorderState) -> ReorderState:
        auto = [
            i.intent_id
            for i in deps.ledger.intents([IntentStatus.APPROVED], run_id=state["run_id"])
            if i.route == "AUTO"
        ]
        return {"auto_submit": await submit(auto)}

    async def human_review(state: ReorderState) -> ReorderState:
        # Read-only before interrupt(): this node re-runs from the top when resumed.
        pending = [i.intent_id for i in deps.ledger.intents([IntentStatus.PENDING_APPROVAL])]
        if not pending:
            return {"review": {"skipped": "nothing awaiting approval"}}
        decision = interrupt({"run_id": state["run_id"], "pending": pending})
        return {"review": decision if isinstance(decision, dict) else {"resumed": decision}}

    async def submit_approved(state: ReorderState) -> ReorderState:
        approved = [i.intent_id for i in deps.ledger.intents([IntentStatus.APPROVED])]
        return {"submit": await submit(approved)}

    async def report(state: ReorderState) -> ReorderState:
        plan = ReorderPlan.model_validate(state["plan"])
        json_path, md_path = save_plan(plan, deps.plans_dir)
        return {"files": [str(json_path), str(md_path)], "status": "DONE"}

    graph = StateGraph(ReorderState)
    steps = [
        ("fetch_snapshot", fetch_snapshot),
        ("reconcile_ledger", reconcile_ledger),
        ("decide", decide),
        ("narrate", narrate),
        ("persist_plan", persist_plan),
        ("submit_auto", submit_auto),
        ("human_review", human_review),
        ("submit_approved", submit_approved),
        ("report", report),
    ]
    for name, fn in steps:
        graph.add_node(name, fn)
    graph.add_edge(START, steps[0][0])
    for (a, _), (b, _) in pairwise(steps):
        graph.add_edge(a, b)
    graph.add_edge(steps[-1][0], END)
    return graph
