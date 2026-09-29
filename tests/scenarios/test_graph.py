"""The LangGraph workflow end to end against the fake MCP server, with a real SQLite checkpointer."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lato_reorder.execution.submitter import submit_lock
from lato_reorder.ledger.repo import Ledger
from lato_reorder.ledger.states import IntentStatus as S
from lato_reorder.orchestrator.graph import Deps
from lato_reorder.orchestrator.runner import Orchestrator
from lato_reorder.policy.config import load_policy
from lato_reorder.settings import Mode
from tests.fakes.fake_mcp import FakeLatoServer

ROOT = Path(__file__).parents[2]
POLICY = load_policy(ROOT / "config")
T0 = datetime(2026, 9, 25, 12, tzinfo=UTC)


def make(tmp_path, mode=Mode.SANDBOX, server=None):
    server = server or FakeLatoServer()
    ledger = Ledger(tmp_path / "ledger.db")
    deps = Deps(
        policy=POLICY,
        ledger=ledger,
        client_factory=server.client,
        clock=lambda: T0,
        mode=mode,
        plans_dir=tmp_path / "plans",
        lock_path=tmp_path / "submit.lock",
    )
    return server, ledger, Orchestrator(deps, tmp_path / "checkpoints.db")


async def test_run_pauses_for_human_then_submits(tmp_path):
    server, ledger, orch = make(tmp_path)
    async with orch:
        status = await orch.start("run1")
        assert status.waiting_for_review and len(status.pending) == 3
        assert len(server.write_calls) == 3  # auto-approved orders went straight through
        for intent_id in status.pending:
            ledger.approve(intent_id, "user:test", T0)
        status = await orch.resume("run1", "user:test")
    assert status.done
    assert len(server.write_calls) == 6
    assert len(ledger.intents([S.SUBMITTED])) == 6


async def test_second_run_orders_nothing(tmp_path):
    server, ledger, orch = make(tmp_path)
    async with orch:
        first = await orch.start("run1")
        for intent_id in first.pending:
            ledger.approve(intent_id, "user:test", T0)
        await orch.resume("run1", "user:test")
        second = await orch.start("run2")
    assert second.done and not second.waiting_for_review
    assert len(server.write_calls) == 6
    assert second.values["persisted"]["created"] == []


async def test_resume_is_idempotent(tmp_path):
    """Resuming twice (double click, retried request) must not submit anything twice."""
    server, ledger, orch = make(tmp_path)
    async with orch:
        status = await orch.start("run1")
        for intent_id in status.pending:
            ledger.approve(intent_id, "user:test", T0)
        await orch.resume("run1", "user:test")
        await orch.resume("run1", "user:test")
    assert len(server.write_calls) == 6


async def test_pause_survives_process_restart(tmp_path):
    server = FakeLatoServer()
    _, ledger, orch = make(tmp_path, server=server)
    async with orch:
        status = await orch.start("run1")
    for intent_id in status.pending:
        ledger.approve(intent_id, "user:test", T0)
    _, _, orch2 = make(tmp_path, server=server)  # new process: same ledger and checkpoint files
    async with orch2:
        assert (await orch2.status("run1")).waiting_for_review
        status = await orch2.resume("run1", "user:test")
    assert status.done and len(server.write_calls) == 6


@pytest.mark.parametrize("pending_left", [True])
async def test_unapproved_lines_stay_pending(tmp_path, pending_left):
    server, ledger, orch = make(tmp_path)
    async with orch:
        status = await orch.start("run1")
        ledger.approve(status.pending[0], "user:test", T0)
        await orch.resume("run1", "user:test")
    assert len(server.write_calls) == 4
    assert len(ledger.intents([S.PENDING_APPROVAL])) == 2


async def test_in_flight_submission_is_not_mistaken_for_a_crash(tmp_path):
    """A run that starts while another process is mid-call must leave SUBMITTING alone; after the
    submitter is gone, a leftover SUBMITTING intent is a crash and becomes UNKNOWN (never retried)."""
    server, ledger, orch = make(tmp_path)
    async with orch:
        first = await orch.start("run1")
        intent_id = first.pending[0]
        ledger.approve(intent_id, "user:test", T0)
        ledger.begin_submit(intent_id, T0, on_hand=None)
        with submit_lock(tmp_path / "submit.lock"):
            second = await orch.start("run2")
        assert ledger.get(intent_id).status is S.SUBMITTING
        assert second.values["reconcile"]["unknown"] == []
        third = await orch.start("run3")
    assert ledger.get(intent_id).status is S.UNKNOWN
    assert third.values["reconcile"]["unknown"] == [intent_id]
    assert len(server.write_calls) == 3  # only run1's auto orders; the in-flight line was never re-sent
