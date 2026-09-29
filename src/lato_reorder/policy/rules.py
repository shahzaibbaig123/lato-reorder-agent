"""The policy's vocabulary: decisions, routes, rule IDs and flags.

Every decision records the rule IDs that produced it, so the reasoning is auditable and the LLM can
only cite rules that exist (these enums are also its output schema).
"""

import re
from enum import StrEnum


class Decision(StrEnum):
    REORDER = "REORDER"
    NO_ORDER = "NO_ORDER"
    BLOCKED = "BLOCKED"


class Route(StrEnum):
    NONE = "NONE"  # nothing to approve
    AUTO = "AUTO"  # approved by policy
    HUMAN = "HUMAN"  # a person must approve
    BLOCK = "BLOCK"  # cannot be submitted until fixed

    @property
    def rank(self) -> int:
        return _ROUTE_RANK[self]


_ROUTE_RANK = {Route.NONE: 0, Route.AUTO: 1, Route.HUMAN: 2, Route.BLOCK: 3}


def strictest(*routes: Route) -> Route:
    return max(routes, key=lambda r: r.rank)


class RuleId(StrEnum):
    # Tier
    TIER_1 = "TIER-1"
    TIER_2 = "TIER-2"
    TIER_3 = "TIER-3"
    TIER_0 = "TIER-0"
    # Trigger / hold (the primary rule of a decision)
    TRIG_STOCKOUT = "TRIG-STOCKOUT"
    TRIG_ROP = "TRIG-ROP"
    HOLD_ABOVE_ROP = "HOLD-ABOVE-ROP"
    HOLD_OPEN_PO = "HOLD-OPEN-PO"
    HOLD_OPEN_PO_INSUFFICIENT = "HOLD-OPEN-PO-INSUFFICIENT"
    HOLD_ORPHAN = "HOLD-ORPHAN"
    # Quantity
    QTY_OUT = "QTY-OUT"
    QTY_LOT = "QTY-LOT"
    # Routing
    RTE_AUTO = "RTE-AUTO"
    RTE_H_TIER = "RTE-H-TIER"
    RTE_H_STOCKOUT = "RTE-H-STOCKOUT"
    RTE_H_SIZE = "RTE-H-SIZE"
    RTE_H_BUDGET = "RTE-H-BUDGET"
    RTE_H_FLAG = "RTE-H-FLAG"
    RTE_H_LLM_CONCERN = "RTE-H-LLM-CONCERN"
    RTE_H_NO_REVIEW = "RTE-H-NO-REVIEW"
    RTE_B_DATA = "RTE-B-DATA"
    RTE_B_NAME = "RTE-B-NAME"
    RTE_B_INFLIGHT = "RTE-B-INFLIGHT"
    RTE_B_SIZE = "RTE-B-SIZE"


RULE_DESCRIPTIONS: dict[RuleId, str] = {
    RuleId.TIER_1: "CRITICAL: used by at least the configured share of bike models",
    RuleId.TIER_2: "SHARED: used by 2 or more models, below the critical share",
    RuleId.TIER_3: "SINGLE: used by exactly one model",
    RuleId.TIER_0: "ORPHAN: used by no model; never ordered",
    RuleId.TRIG_STOCKOUT: "Reorder: nothing on hand and inventory position at or below the reorder point",
    RuleId.TRIG_ROP: "Reorder: inventory position at or below the reorder point",
    RuleId.HOLD_ABOVE_ROP: "No order: inventory position above the reorder point",
    RuleId.HOLD_OPEN_PO: "No order: an open PO already lifts inventory position above the reorder point",
    RuleId.HOLD_OPEN_PO_INSUFFICIENT: "No order: below the reorder point but a PO is already open; "
    "flagged, never a second PO",
    RuleId.HOLD_ORPHAN: "No order: no bike model uses this part",
    RuleId.QTY_OUT: "Quantity = order-up-to level minus inventory position",
    RuleId.QTY_LOT: "Quantity rounded up to whole lots",
    RuleId.RTE_AUTO: "Auto-approved: non-critical, in stock, within size caps and run budget, no flags",
    RuleId.RTE_H_TIER: "Human approval: part is CRITICAL",
    RuleId.RTE_H_STOCKOUT: "Human approval: part is out of stock",
    RuleId.RTE_H_SIZE: "Human approval: quantity above the auto-approve limit",
    RuleId.RTE_H_BUDGET: "Human approval: this run's auto-approve budget is used up",
    RuleId.RTE_H_FLAG: "Human approval: an escalating flag was raised",
    RuleId.RTE_H_LLM_CONCERN: "Human approval: the reviewing analyst raised a concern",
    RuleId.RTE_H_NO_REVIEW: "Human approval: no analyst review was available",
    RuleId.RTE_B_DATA: "Blocked: part data is invalid",
    RuleId.RTE_B_NAME: "Blocked: part name is ambiguous or malformed",
    RuleId.RTE_B_INFLIGHT: "Blocked: an earlier PO for this part has an unknown outcome",
    RuleId.RTE_B_SIZE: "Blocked: quantity above the hard cap or failed the sanity invariant",
}


class Flag(StrEnum):
    # informational
    WATCH = "WATCH"
    EXCESS = "EXCESS"
    ORPHAN_STOCK = "ORPHAN_STOCK"
    AWAITING_DELIVERY = "AWAITING_DELIVERY"
    OPEN_PO_INSUFFICIENT = "OPEN_PO_INSUFFICIENT"
    # escalating (send an order to a human)
    FG_EXPOSED = "FG_EXPOSED"
    BOM_SUSPECT = "BOM_SUSPECT"
    UNMATCHED_MODEL = "UNMATCHED_MODEL"
    NEAR_DUPLICATE_NAME = "NEAR_DUPLICATE_NAME"
    DUPLICATE_PRODUCT_REF = "DUPLICATE_PRODUCT_REF"
    INSTRUCTION_LIKE_TEXT = "INSTRUCTION_LIKE_TEXT"
    PREVIOUSLY_REJECTED = "PREVIOUSLY_REJECTED"


ESCALATING_FLAGS = frozenset(
    {
        Flag.FG_EXPOSED,
        Flag.BOM_SUSPECT,
        Flag.UNMATCHED_MODEL,
        Flag.NEAR_DUPLICATE_NAME,
        Flag.DUPLICATE_PRODUCT_REF,
        Flag.INSTRUCTION_LIKE_TEXT,
        Flag.PREVIOUSLY_REJECTED,
    }
)

# Parts whose names suggest more than one unit per bike (a bike has two wheels, two pedals).
BOM_SUSPECT_PATTERN = re.compile(r"\b(wheels?|pedals?)\b", re.IGNORECASE)

# Text in inventory data that reads like an instruction to an AI. Deterministic, escalate-only.
INSTRUCTION_LIKE_PATTERN = re.compile(
    r"ignore (all|any|the|previous|above)|disregard|system prompt|you are now|as an ai"
    r"|approve (this|all|immediately)|<\s*/?\s*(system|instructions?|prompt)\s*>",
    re.IGNORECASE,
)
