from pathlib import Path

import pytest

from lato_reorder.calibrate import simulate
from lato_reorder.mcp_gateway.inventory import load_fixture_snapshot
from lato_reorder.policy.config import load_policy
from lato_reorder.policy.maths import (
    Tier,
    cycle_demand,
    order_quantity,
    order_up_to,
    reorder_point,
    round_to_lot,
    tier_for,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("fan_out", "n", "expected"),
    [
        (0, 9, Tier.T0_ORPHAN),
        (1, 9, Tier.T3_SINGLE),
        (2, 9, Tier.T2_SHARED),
        (4, 9, Tier.T2_SHARED),  # 0.44 < 0.5
        (5, 9, Tier.T1_CRITICAL),  # 0.56
        (9, 9, Tier.T1_CRITICAL),
        (5, 10, Tier.T1_CRITICAL),  # exactly 0.5 is critical
    ],
)
def test_tier_boundaries(fan_out, n, expected):
    assert tier_for(fan_out, n, 0.5) is expected


def test_worked_example_chain():
    d = cycle_demand(10, 1, 7)
    assert (d, reorder_point(d, 1, 1.0), order_up_to(d, 1, 1, 1.0)) == (70, 140, 210)
    assert order_quantity(120, 210, 1) == 90


def test_order_quantity_zero_at_or_above_out():
    assert order_quantity(210, 210, 1) == 0
    assert order_quantity(250, 210, 1) == 0


def test_lot_rounding():
    assert round_to_lot(35, 97) == 97
    assert round_to_lot(98, 97) == 194
    assert round_to_lot(0, 97) == 0


def test_frozen_policy_loads():
    policy = load_policy(ROOT / "config")
    assert policy.version == "1.0.0"
    assert policy.demand.k_builds_per_model_per_cycle == 5
    assert policy.units_for("Racing Alloy Wheels") == 2
    assert policy.units_for("Gel Comfort Seat") == 1


def test_calibrated_result_is_reproducible():
    """Golden check: the frozen policy on the calibration snapshot orders the documented six parts."""
    snap = load_fixture_snapshot(ROOT / "tests" / "fixtures" / "live")
    policy = load_policy(ROOT / "config")
    assert snap.content_hash == policy.calibrated_on
    scenario = simulate(
        snap, policy, policy.demand.k_builds_per_model_per_cycle, "unit", policy.units_per_bike
    )
    ordered = {ln.part.item_name: ln.quantity for ln in scenario.triggered}
    assert ordered == {
        "Hydraulic Suspension Fork": 119,
        "Rear Derailleur Mechanism": 116,
        "Precision Gear Set": 109,
        "Anti-Slip Pedals": 66,
        "Lightweight Alloy Wheels": 35,
        "Lightweight Steel Frame": 16,
    }
