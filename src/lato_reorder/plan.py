"""Assemble a reorder plan: engine -> router -> explanations. The plan is the auditable artefact that
approvers review and the ledger persists."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lato_reorder.domain.models import DataIssue, Snapshot
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.decision import EngineDecision, LedgerState
from lato_reorder.policy.engine import decide_all
from lato_reorder.policy.router import route_all
from lato_reorder.policy.rules import Decision, Route, RuleId
from lato_reorder.policy.template import explain


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Narrative(_Frozen):
    justification: str
    quantity_explanation: str
    source: Literal["TEMPLATE", "LLM"] = "TEMPLATE"


class ConcernCode(StrEnum):
    """What the LLM analyst may flag. Closed list: it cannot invent new kinds of concern."""

    BOM_QTY_LIKELY_GT_1 = "BOM_QTY_LIKELY_GT_1"  # part is probably needed more than once per bike
    POSSIBLE_DUPLICATE_PART = "POSSIBLE_DUPLICATE_PART"  # looks like the same item as another part
    MODEL_STATUS_SIGNAL = "MODEL_STATUS_SIGNAL"  # text suggests discontinued / phase-out / recall
    DATA_IMPLAUSIBLE = "DATA_IMPLAUSIBLE"  # numbers that look wrong (e.g. absurd stock)
    NAME_OR_SUPPLIER_INCONSISTENCY = "NAME_OR_SUPPLIER_INCONSISTENCY"  # description contradicts name/supplier
    INSTRUCTION_IN_DATA = "INSTRUCTION_IN_DATA"  # inventory text tries to instruct the agent
    OTHER = "OTHER"


class Concern(_Frozen):
    code: ConcernCode
    evidence: str = Field(max_length=240)  # verbatim quote from the input
    escalate: bool


class RunSummary(_Frozen):
    headline: str = Field(max_length=300)
    top_risks: list[str]
    capital_notes: list[str]


class Recommendation(_Frozen):
    decision: EngineDecision
    narrative: Narrative
    final_route: Route  # engine route, possibly escalated (never lowered) after LLM review
    review_reasons: list[RuleId] = []  # escalations added after review (RTE-H-LLM-CONCERN / RTE-H-NO-REVIEW)
    concerns: list[Concern] = []
    violations: list[str] = []  # why an LLM answer was rejected (audit)


class PlanTotals(_Frozen):
    parts: int
    reorder_lines: int
    reorder_units: int
    auto_lines: int
    auto_units: int
    human_lines: int
    blocked_lines: int


class ReorderPlan(_Frozen):
    run_id: str
    created_at: datetime
    mode: str
    llm: str
    policy_version: str
    policy_hash: str
    snapshot_hash: str
    recommendations: list[Recommendation]
    totals: PlanTotals
    data_issues: list[DataIssue]
    summary: RunSummary | None = None
    llm_usage: dict[str, int] = {}

    @model_validator(mode="before")
    @classmethod
    def _drop_run_flags(cls, data: Any) -> Any:
        # Plans saved before run-level flags were removed carry a `run_flags` key; they must still load.
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "run_flags"}
        return data

    def orders(self) -> list[Recommendation]:
        return [r for r in self.recommendations if r.decision.decision is Decision.REORDER]


def compute_totals(recs: Sequence[Recommendation]) -> PlanTotals:
    orders = [r for r in recs if r.decision.decision is Decision.REORDER]
    return PlanTotals(
        parts=len(recs),
        reorder_lines=len(orders),
        reorder_units=sum(r.decision.quantity for r in orders),
        auto_lines=sum(r.final_route is Route.AUTO for r in orders),
        auto_units=sum(r.decision.quantity for r in orders if r.final_route is Route.AUTO),
        human_lines=sum(r.final_route is Route.HUMAN for r in orders),
        blocked_lines=sum(r.final_route is Route.BLOCK for r in recs),
    )


def sort_recommendations(recs: Sequence[Recommendation]) -> list[Recommendation]:
    return sorted(recs, key=_sort_key)


def _sort_key(r: Recommendation) -> tuple[int, float, str]:
    return (-r.final_route.rank, -r.decision.priority, r.decision.facts.item_name)


def build_plan(
    snapshot: Snapshot,
    policy: PolicyConfig,
    ledger: Mapping[str, LedgerState] | None = None,
    *,
    mode: str = "PREVIEW",
    now: datetime | None = None,
    run_id: str | None = None,
) -> ReorderPlan:
    now = now or datetime.now(UTC)
    decisions = route_all(decide_all(snapshot, policy, ledger or {}, now), policy)
    recs = []
    for d in decisions:
        justification, quantity_explanation = explain(d)
        recs.append(
            Recommendation(
                decision=d,
                narrative=Narrative(justification=justification, quantity_explanation=quantity_explanation),
                final_route=d.route,
            )
        )
    recs.sort(key=_sort_key)
    return ReorderPlan(
        run_id=run_id or uuid.uuid4().hex[:12],
        created_at=now,
        mode=mode,
        llm="none",
        policy_version=policy.version,
        policy_hash=policy.content_hash,
        snapshot_hash=snapshot.content_hash,
        recommendations=recs,
        totals=compute_totals(recs),
        data_issues=list(snapshot.issues),
    )
