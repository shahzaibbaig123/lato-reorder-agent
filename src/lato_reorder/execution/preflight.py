"""Pre-flight: just before submitting, re-run the engine on fresh inventory as if this order had not been
placed. Submit only if the order is still justified at roughly the approved size and route."""

from dataclasses import dataclass
from datetime import datetime

from lato_reorder.domain.models import Snapshot
from lato_reorder.ledger.repo import Intent
from lato_reorder.policy.config import PolicyConfig
from lato_reorder.policy.decision import LedgerState
from lato_reorder.policy.engine import decide_part
from lato_reorder.policy.router import line_route
from lato_reorder.policy.rules import Decision, Route

QTY_TOLERANCE = 0.10


@dataclass(frozen=True)
class Preflight:
    ok: bool
    reason: str
    on_hand: int | None = None
    fresh_quantity: int | None = None


def preflight(
    intent: Intent,
    fresh: Snapshot,
    policy: PolicyConfig,
    ledger_without_intent: LedgerState,
    now: datetime,
) -> Preflight:
    part = next((p for p in fresh.parts if p.key == intent.part_key), None)
    if part is None:
        return Preflight(False, "part missing or blocked in the fresh inventory")
    if part.item_name != intent.item_name:
        return Preflight(False, f"part renamed to {part.item_name!r} since approval", part.stock)
    if part.item_name in fresh.blocked_part_names():
        return Preflight(False, "part data is now invalid", part.stock)

    decision = decide_part(part, fresh, policy, ledger_without_intent, now)
    route, _ = line_route(decision, policy)
    if decision.decision is not Decision.REORDER:
        return Preflight(False, f"no longer needed ({decision.primary_rule.value})", part.stock, 0)
    if route is Route.BLOCK or route.rank > Route[intent.route].rank:
        return Preflight(
            False, f"route is now {route.value}, stricter than {intent.route}", part.stock, decision.quantity
        )
    lot = decision.calc.lot if decision.calc else 1
    tolerance = max(lot, QTY_TOLERANCE * intent.engine_quantity)
    if abs(decision.quantity - intent.engine_quantity) > tolerance:
        return Preflight(
            False,
            f"engine quantity changed from {intent.engine_quantity} to {decision.quantity}",
            part.stock,
            decision.quantity,
        )
    return Preflight(True, "still justified", part.stock, decision.quantity)
