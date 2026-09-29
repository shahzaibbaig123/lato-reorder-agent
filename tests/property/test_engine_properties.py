"""Invariants that must hold for any inventory, not just the examples."""

from datetime import UTC, datetime
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from lato_reorder.domain.ingest import build_snapshot
from lato_reorder.policy.config import load_policy
from lato_reorder.policy.decision import LedgerState
from lato_reorder.policy.engine import decide_all
from lato_reorder.policy.maths import Tier
from lato_reorder.policy.router import route_all
from lato_reorder.policy.rules import Decision, Route

POLICY = load_policy(Path(__file__).parents[2] / "config")
NOW = datetime(2026, 9, 25, tzinfo=UTC)
N_BIKES = 9


def bikes(stocks):
    return [{"Title": f"Bike {i}", "StockQuantity": s} for i, s in enumerate(stocks)]


part_strategy = st.fixed_dictionaries(
    {
        "stock": st.integers(0, 400),
        "models": st.sets(st.integers(0, N_BIKES - 1), max_size=N_BIKES),
        "on_order": st.integers(0, 300),
    }
)


def decide_one(p, fg=None):
    raw = [
        {
            "Title": "Part X",
            "StockQuantity": p["stock"],
            "Supplier": "Acme",
            "Products": [{"Title": f"Bike {i}"} for i in sorted(p["models"])],
        }
    ]
    snap = build_snapshot(bikes(fg or [50] * N_BIKES), raw)
    ledger = {"part x": LedgerState(on_order=p["on_order"], has_open_po=p["on_order"] > 0)}
    routed = route_all(decide_all(snap, POLICY, ledger, NOW), POLICY)
    return routed[0]


@settings(max_examples=300, deadline=None)
@given(part_strategy)
def test_quantity_positive_iff_reorder(p):
    d = decide_one(p)
    assert (d.quantity > 0) == (d.decision is Decision.REORDER)


@settings(max_examples=300, deadline=None)
@given(part_strategy)
def test_order_lands_between_rop_and_out(p):
    d = decide_one(p)
    if d.decision is Decision.REORDER:
        ip = d.facts.inventory_position
        assert d.calc.reorder_point < ip + d.quantity <= d.calc.order_up_to + d.calc.lot - 1


@settings(max_examples=300, deadline=None)
@given(part_strategy)
def test_reevaluating_with_order_placed_does_not_trigger_again(p):
    first = decide_one({**p, "on_order": 0})
    if first.decision is Decision.REORDER:
        again = decide_one({**p, "on_order": first.quantity})
        assert again.decision is Decision.NO_ORDER


@settings(max_examples=300, deadline=None)
@given(part_strategy)
def test_critical_never_auto_and_orphan_never_ordered(p):
    d = decide_one(p)
    if d.tier is Tier.T1_CRITICAL:
        assert d.route is not Route.AUTO
    if d.tier is Tier.T0_ORPHAN:
        assert d.decision is Decision.NO_ORDER


@settings(max_examples=200, deadline=None)
@given(part_strategy, st.lists(st.integers(0, 120), min_size=N_BIKES, max_size=N_BIKES))
def test_finished_goods_never_change_quantity(p, fg):
    assert decide_one(p, fg).quantity == decide_one(p).quantity


@settings(max_examples=200, deadline=None)
@given(part_strategy, st.integers(1, 50))
def test_less_stock_never_orders_less(p, drop):
    lower = {**p, "stock": max(p["stock"] - drop, 0), "on_order": 0}
    higher = {**p, "on_order": 0}
    assert decide_one(lower).quantity >= decide_one(higher).quantity
