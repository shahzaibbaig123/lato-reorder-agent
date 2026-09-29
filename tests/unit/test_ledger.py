import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lato_reorder.ledger.repo import Ledger, LedgerError, StateConflict
from lato_reorder.ledger.states import IntentStatus as S
from lato_reorder.mcp_gateway.inventory import load_fixture_snapshot
from lato_reorder.plan import build_plan
from lato_reorder.policy.config import load_policy
from lato_reorder.policy.rules import Decision, Route, RuleId

ROOT = Path(__file__).parents[2]
POLICY = load_policy(ROOT / "config")
SNAP = load_fixture_snapshot(ROOT / "tests" / "fixtures" / "live")
T0 = datetime(2026, 9, 25, 12, tzinfo=UTC)


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    yield lg
    lg.close()


def plan_and_persist(ledger, now, run_id):
    plan = build_plan(SNAP, POLICY, ledger.ledger_view(now), now=now, run_id=run_id)
    return plan, ledger.persist_plan(plan, now)


def by_item(ledger, statuses=None):
    return {i.item_name: i for i in ledger.intents(statuses)}


def test_first_run_creates_auto_approved_and_pending(ledger):
    _, res = plan_and_persist(ledger, T0, "r1")
    assert len(res.created) == 6
    intents = by_item(ledger)
    assert intents["Hydraulic Suspension Fork"].status is S.PENDING_APPROVAL
    assert intents["Anti-Slip Pedals"].status is S.APPROVED
    auto_approvals = [e for e in ledger.events() if e["kind"] == "CREATED"]
    assert len(auto_approvals) == 6


def test_rerun_with_unchanged_stock_orders_nothing_new(ledger):
    """The sandbox never updates stock after a PO. The second run must not propose the same orders again."""
    plan_and_persist(ledger, T0, "r1")
    plan2, res2 = plan_and_persist(ledger, T0 + timedelta(minutes=5), "r2")
    assert res2.created == [] and res2.superseded == []
    assert len(res2.kept) == 3  # the three human-route orders still await approval, unchanged
    held = {r.decision.facts.item_name: r.decision.primary_rule for r in plan2.recommendations}
    assert held["Anti-Slip Pedals"] is RuleId.HOLD_OPEN_PO  # auto-approved in run 1, now on order
    assert len(ledger.intents()) == 6


def test_approved_quantity_counts_as_on_order(ledger):
    plan_and_persist(ledger, T0, "r1")
    fork = by_item(ledger)["Hydraulic Suspension Fork"]
    ledger.approve(fork.intent_id, "user:mirza", T0)
    view = ledger.ledger_view(T0)
    assert view[fork.part_key].on_order == 119 and view[fork.part_key].has_open_po


def test_database_enforces_one_open_intent_per_part(ledger):
    plan_and_persist(ledger, T0, "r1")
    fork = by_item(ledger)["Hydraulic Suspension Fork"]
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute(
            "INSERT INTO po_intents (intent_id, run_id, part_key, item_name, quantity, engine_quantity, "
            "route, "
            "decision_hash, status, created_at, updated_at) VALUES ('x', 'r1', ?, 'dup', 1, 1, 'AUTO', 'h', "
            "'APPROVED', 't', 't')",
            (fork.part_key,),
        )


def test_edit_requires_reason_and_rebinds(ledger):
    plan_and_persist(ledger, T0, "r1")
    fork = by_item(ledger)["Hydraulic Suspension Fork"]
    with pytest.raises(LedgerError):
        ledger.approve(fork.intent_id, "user:mirza", T0, quantity=100)
    approved = ledger.approve(fork.intent_id, "user:mirza", T0, quantity=100, reason="budget this month")
    assert approved.quantity == 100 and approved.engine_quantity == 119
    assert approved.binding_hash != fork.binding_hash


def test_double_approve_is_a_conflict(ledger):
    plan_and_persist(ledger, T0, "r1")
    fork = by_item(ledger)["Hydraulic Suspension Fork"]
    ledger.approve(fork.intent_id, "user:a", T0)
    with pytest.raises(StateConflict):
        ledger.approve(fork.intent_id, "user:b", T0)


def test_begin_submit_is_exactly_once(ledger):
    plan_and_persist(ledger, T0, "r1")
    pedals = by_item(ledger)["Anti-Slip Pedals"]
    ledger.begin_submit(pedals.intent_id, T0, on_hand=34)
    with pytest.raises(StateConflict):
        ledger.begin_submit(pedals.intent_id, T0, on_hand=34)


def test_expired_approval_cannot_be_submitted(ledger):
    plan_and_persist(ledger, T0, "r1")
    pedals = by_item(ledger)["Anti-Slip Pedals"]
    with pytest.raises(LedgerError, match="expired"):
        ledger.begin_submit(pedals.intent_id, T0 + timedelta(hours=25), on_hand=34)


def test_crash_while_submitting_becomes_unknown_and_blocks_part(ledger):
    plan_and_persist(ledger, T0, "r1")
    pedals = by_item(ledger)["Anti-Slip Pedals"]
    ledger.begin_submit(pedals.intent_id, T0, on_hand=34)
    swept = ledger.startup_sweep(T0 + timedelta(minutes=1))
    assert swept["unknown"] == [pedals.intent_id]
    plan = build_plan(SNAP, POLICY, ledger.ledger_view(T0), now=T0, run_id="r2")
    rec = next(r for r in plan.recommendations if r.decision.facts.item_name == "Anti-Slip Pedals")
    assert (rec.decision.decision, rec.final_route) == (Decision.BLOCKED, Route.BLOCK)


def test_resolve_unknown(ledger):
    plan_and_persist(ledger, T0, "r1")
    pedals = by_item(ledger)["Anti-Slip Pedals"]
    ledger.begin_submit(pedals.intent_id, T0, on_hand=34)
    ledger.finish_submit(pedals.intent_id, S.UNKNOWN, T0, raw_response="timeout")
    resolved = ledger.resolve(pedals.intent_id, "user:mirza", T0, sap_po_number="Ant-123")
    assert resolved.status is S.SUBMITTED and resolved.sap_po_number == "Ant-123"


def test_failed_submission_frees_the_part(ledger):
    plan_and_persist(ledger, T0, "r1")
    pedals = by_item(ledger)["Anti-Slip Pedals"]
    ledger.begin_submit(pedals.intent_id, T0, on_hand=34)
    ledger.finish_submit(pedals.intent_id, S.FAILED, T0, raw_response="No part named ...")
    assert pedals.part_key not in ledger.ledger_view(T0)
    _, res = plan_and_persist(ledger, T0, "r2")
    assert any(ledger.get(i).item_name == "Anti-Slip Pedals" for i in res.created)


def test_rejection_reproposes_to_human(ledger):
    plan_and_persist(ledger, T0, "r1")
    pedals_pending = by_item(ledger)["Hydraulic Suspension Fork"]
    ledger.reject(pedals_pending.intent_id, "user:mirza", "not now", T0)
    plan = build_plan(SNAP, POLICY, ledger.ledger_view(T0), now=T0, run_id="r2")
    rec = next(r for r in plan.recommendations if r.decision.facts.item_name == "Hydraulic Suspension Fork")
    assert rec.final_route is Route.HUMAN and "PREVIOUSLY_REJECTED" in rec.decision.flags


def test_receipt_closes_po(ledger):
    plan_and_persist(ledger, T0, "r1")
    wheels = by_item(ledger)["Lightweight Alloy Wheels"]
    ledger.begin_submit(wheels.intent_id, T0, on_hand=15)
    ledger.finish_submit(wheels.intent_id, S.SUBMITTED, T0, sap_po_number="Lig-1")
    restocked = SNAP.model_copy(
        update={
            "parts": tuple(
                p.model_copy(update={"stock": 50}) if p.item_name == "Lightweight Alloy Wheels" else p
                for p in SNAP.parts
            )
        }
    )
    assert ledger.apply_receipts(restocked, T0) == [wheels.intent_id]
    assert ledger.get(wheels.intent_id).status is S.RECEIVED


def test_events_are_append_only(ledger):
    plan_and_persist(ledger, T0, "r1")
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute("DELETE FROM events")


def test_run_plan_roundtrip(ledger):
    plan, _ = plan_and_persist(ledger, T0, "r1")
    assert ledger.run_plan("r1") == plan
