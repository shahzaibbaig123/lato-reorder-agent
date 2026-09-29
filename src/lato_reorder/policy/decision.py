"""Authoritative, structured output of the deterministic engine (one per part)."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

from lato_reorder.policy.maths import Tier
from lato_reorder.policy.rules import Decision, Flag, Route, RuleId


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LedgerState(_Frozen):
    """What the PO ledger knows about one part. Supplied by the ledger (M5); empty means nothing on order."""

    on_order: NonNegativeInt = 0  # qty in APPROVED / SUBMITTING / SUBMITTED / MISMATCH / UNKNOWN
    has_open_po: bool = False  # an APPROVED / SUBMITTING / SUBMITTED intent exists
    inflight_unresolved: bool = False  # an UNKNOWN or SUBMITTED_MISMATCH intent exists
    rejected_recently: bool = False
    rejection_reason: str | None = None


class PartFacts(_Frozen):
    part_key: str
    item_name: str
    supplier: str | None
    description: str = ""
    on_hand: int | None
    on_order: int = 0
    inventory_position: int | None
    used_by: list[str] = []
    fan_out: int = 0
    catalogue_size: int = 0
    share: float = 0.0
    units_per_bike: int = 1
    units_source: Literal["DEFAULT", "OVERRIDE"] = "DEFAULT"
    fg_by_model: dict[str, int | None] = {}
    exposed_models: list[str] = []
    data_issues: list[str] = []


class DerivationStep(_Frozen):
    rule_id: RuleId | None
    expression: str  # human-readable, e.g. "ROP = ceil((1 + 1.0) x 45) = 90"
    value: float


class Calc(_Frozen):
    k: float
    cycle_demand: float
    safety_cycles: float
    reorder_point: int
    order_up_to: int
    cover_before: float  # cycles of assumed demand covered by inventory position
    cover_after: float | None
    raw_quantity: int
    lot: int
    steps: list[DerivationStep]


class EngineDecision(_Frozen):
    facts: PartFacts
    calc: Calc | None  # None for orphans and data-blocked parts
    tier: Tier | None
    decision: Decision
    quantity: NonNegativeInt
    primary_rule: RuleId
    rules_applied: list[RuleId]
    flags: list[Flag]
    priority: float = Field(ge=0)
    route: Route = Route.NONE
    route_reasons: list[RuleId] = []
    policy_version: str
    decision_hash: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "EngineDecision":
        if (self.quantity > 0) != (self.decision is Decision.REORDER):
            raise ValueError("quantity > 0 exactly when decision is REORDER")
        if self.decision is Decision.BLOCKED and self.route not in (Route.BLOCK, Route.NONE):
            raise ValueError("a BLOCKED decision can only route to BLOCK")
        if self.decision is Decision.NO_ORDER and self.route is not Route.NONE:
            raise ValueError("NO_ORDER routes to NONE")
        return self

    def with_hash(self, policy_hash: str) -> "EngineDecision":
        """Stable identity of what was decided; approvals bind to it."""
        payload = {
            "part": self.facts.part_key,
            "item": self.facts.item_name,
            "decision": self.decision,
            "quantity": self.quantity,
            "tier": self.tier,
            "route": self.route,
            "ip": self.facts.inventory_position,
            "policy": policy_hash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return self.model_copy(update={"decision_hash": digest[:16]})
