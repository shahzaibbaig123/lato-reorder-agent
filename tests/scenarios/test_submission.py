"""End-to-end submission scenarios against the fake MCP server. The key assertion throughout is the number
of RaiseSAPPurchaseOrder calls that reached SAP."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lato_reorder.domain.ingest import build_snapshot
from lato_reorder.execution.submitter import SubmitRefused, Submitter, submit_lock
from lato_reorder.ledger.repo import Ledger
from lato_reorder.ledger.states import IntentStatus as S
from lato_reorder.plan import build_plan
from lato_reorder.policy.config import load_policy
from lato_reorder.policy.rules import Decision, Route
from lato_reorder.settings import Mode
from tests.fakes.fake_mcp import FakeLatoServer, Fault

ROOT = Path(__file__).parents[2]
POLICY = load_policy(ROOT / "config")
T0 = datetime(2026, 9, 25, 12, tzinfo=UTC)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.server = FakeLatoServer()
        self.ledger = Ledger(tmp_path / "ledger.db")
        self.lock = tmp_path / "submit.lock"
        self.now = T0

    def snapshot(self):
        return build_snapshot(self.server.products, self.server.parts, fetched_at=self.now)

    def plan(self, run_id):
        plan = build_plan(
            self.snapshot(), POLICY, self.ledger.ledger_view(self.now), now=self.now, run_id=run_id
        )
        self.ledger.persist_plan(plan, self.now)
        return plan

    def approve_all(self):
        for it in self.ledger.intents([S.PENDING_APPROVAL]):
            self.ledger.approve(it.intent_id, "user:test", self.now)

    def submitter(self, mode=Mode.SANDBOX):
        return Submitter(
            self.ledger, POLICY, self.server.client, mode=mode, lock_path=self.lock, clock=lambda: self.now
        )

    def submit(self, mode=Mode.SANDBOX):
        return asyncio.run(self.submitter(mode).submit())

    def status(self, item):
        return next(i for i in self.ledger.intents() if i.item_name == item).status


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.ledger.close()


def test_production_requires_explicit_enable(env):
    env.plan("r1")
    with pytest.raises(SubmitRefused):
        env.submit(Mode.PRODUCTION)


def test_happy_path_submits_each_approved_order_once(env):
    env.plan("r1")
    report = env.submit()  # only the 3 auto-approved orders are APPROVED so far
    assert report.count(S.SUBMITTED) == 3 and len(env.server.write_calls) == 3
    env.approve_all()
    report = env.submit()
    assert report.count(S.SUBMITTED) == 3 and len(env.server.write_calls) == 6
    assert all(i.sap_po_number for i in env.ledger.intents([S.SUBMITTED]))


def test_rerun_after_submit_places_no_second_order(env):
    """Sandbox stock never changes after a PO. Planning and submitting again must not call SAP."""
    env.plan("r1")
    env.approve_all()
    env.submit()
    assert len(env.server.write_calls) == 6
    plan2 = env.plan("r2")
    assert plan2.orders() == []
    assert {r.decision.primary_rule.value for r in plan2.recommendations if r.decision.facts.on_order} == {
        "HOLD-OPEN-PO"
    }
    env.submit()
    assert len(env.server.write_calls) == 6


def test_timeout_after_send_is_unknown_never_retried_and_blocks_part(env):
    env.server.faults["Anti-Slip Pedals"] = Fault("timeout_after_send")
    env.plan("r1")
    env.submit()
    assert env.status("Anti-Slip Pedals") is S.UNKNOWN
    calls = len(env.server.write_calls)
    env.submit()
    assert len(env.server.write_calls) == calls  # no retry
    rec = next(r for r in env.plan("r2").recommendations if r.decision.facts.item_name == "Anti-Slip Pedals")
    assert (rec.decision.decision, rec.final_route) == (Decision.BLOCKED, Route.BLOCK)
    assert len(env.server.created_pos) == 3  # SAP did create it; the ledger still counts it


def test_connect_error_before_send_is_retried_safely(env):
    env.server.faults["Anti-Slip Pedals"] = Fault("connect_error")
    env.plan("r1")
    env.submit()
    assert env.status("Anti-Slip Pedals") is S.APPROVED
    env.submit()
    assert env.status("Anti-Slip Pedals") is S.SUBMITTED
    assert sum(c["ItemName"] == "Anti-Slip Pedals" for c in env.server.write_calls) == 1


def test_wrong_quantity_confirmation_is_mismatch_and_blocks(env):
    env.server.faults["Lightweight Alloy Wheels"] = Fault("wrong_quantity")
    env.plan("r1")
    env.submit()
    assert env.status("Lightweight Alloy Wheels") is S.SUBMITTED_MISMATCH
    rec = next(
        r for r in env.plan("r2").recommendations if r.decision.facts.item_name == "Lightweight Alloy Wheels"
    )
    assert rec.final_route is Route.BLOCK


def test_refusal_is_failed_and_reproposed(env):
    env.server.faults["Lightweight Steel Frame"] = Fault("refuse")
    env.plan("r1")
    env.submit()
    assert env.status("Lightweight Steel Frame") is S.FAILED
    rec = next(
        r for r in env.plan("r2").recommendations if r.decision.facts.item_name == "Lightweight Steel Frame"
    )
    assert rec.decision.decision is Decision.REORDER


def test_stale_approval_is_not_submitted(env):
    env.plan("r1")
    env.server.set_stock("Anti-Slip Pedals", 95)  # stock arrived after approval: no longer needed
    report = env.submit()
    assert env.status("Anti-Slip Pedals") is S.STALE
    assert all(c["ItemName"] != "Anti-Slip Pedals" for c in env.server.write_calls)
    assert report.count(S.STALE) == 1


def test_expired_approval_is_not_submitted(env):
    env.plan("r1")
    env.now = T0 + timedelta(hours=25)
    env.submit()
    assert env.server.write_calls == []
    assert {i.status for i in env.ledger.intents() if i.route == "AUTO"} == {S.EXPIRED}


def test_breaker_stops_after_two_bad_outcomes(env):
    for item in ("Anti-Slip Pedals", "Lightweight Alloy Wheels"):
        env.server.faults[item] = Fault("garbled")
    env.plan("r1")
    report = env.submit()
    assert report.breaker_tripped
    assert report.count("NOT_ATTEMPTED") == 1
    assert len(env.server.write_calls) == 2


def test_auth_failure_releases_and_stops(env):
    env.server.faults["Anti-Slip Pedals"] = Fault("auth")
    env.plan("r1")
    report = env.submit()
    assert env.status("Anti-Slip Pedals") is S.APPROVED
    assert report.breaker_tripped or report.count("NOT_ATTEMPTED") >= 1


def test_second_submitter_is_refused_while_first_holds_lock(env):
    env.plan("r1")
    with submit_lock(env.lock), pytest.raises(SubmitRefused):
        env.submit()
    assert env.server.write_calls == []


def test_crash_mid_call_recovers_as_unknown(env):
    env.plan("r1")
    pedals = next(i for i in env.ledger.intents() if i.item_name == "Anti-Slip Pedals")
    env.ledger.begin_submit(pedals.intent_id, env.now, on_hand=34)  # process dies here
    env.ledger.startup_sweep(env.now)
    assert env.status("Anti-Slip Pedals") is S.UNKNOWN
    env.submit()
    assert all(c["ItemName"] != "Anti-Slip Pedals" for c in env.server.write_calls)
