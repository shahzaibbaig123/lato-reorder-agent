"""Approver web UI end to end: start a run, review, submit, reconcile."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lato_reorder.approval.web import create_app
from lato_reorder.ledger.repo import Ledger
from lato_reorder.ledger.states import IntentStatus as S
from lato_reorder.orchestrator.graph import Deps
from lato_reorder.policy.config import load_policy
from lato_reorder.settings import Mode
from tests.fakes.fake_mcp import FakeLatoServer, Fault

ROOT = Path(__file__).parents[2]
T0 = datetime(2026, 9, 25, 12, tzinfo=UTC)


@pytest.fixture
def web(tmp_path):
    server = FakeLatoServer()
    ledger = Ledger(tmp_path / "ledger.db")
    deps = Deps(
        policy=load_policy(ROOT / "config"),
        ledger=ledger,
        client_factory=server.client,
        clock=lambda: T0,
        mode=Mode.SANDBOX,
        plans_dir=tmp_path / "plans",
        lock_path=tmp_path / "submit.lock",
    )
    app = create_app(deps, tmp_path / "checkpoints.db", approver="tester", docs_dir=ROOT / "docs")
    with TestClient(app) as client:
        yield client, server, ledger
    ledger.close()


def start(client):
    resp = client.post("/runs", follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_full_review_flow(web):
    client, server, ledger = web
    run_id = start(client)
    page = client.get(f"/runs/{run_id}").text
    assert "Awaiting your approval (3)" in page
    assert "Hydraulic Suspension Fork" in page and "QTY-OUT" in page
    assert len(server.write_calls) == 3  # auto-approved orders already submitted

    pending = {i.item_name: i for i in ledger.intents([S.PENDING_APPROVAL])}
    fork = pending["Hydraulic Suspension Fork"]
    derailleur = pending["Rear Derailleur Mechanism"]
    gears = pending["Precision Gear Set"]
    ok = client.post(f"/intents/{fork.intent_id}/approve", data={"quantity": fork.quantity})
    assert "APPROVED" in ok.text
    edited = client.post(f"/intents/{derailleur.intent_id}/approve", data={"quantity": 100, "reason": "cash"})
    assert "approved 100" in edited.text
    rejected = client.post(f"/intents/{gears.intent_id}/reject", data={"reason": "ordered by phone"})
    assert "REJECTED" in rejected.text

    client.post(f"/runs/{run_id}/submit")
    assert len(server.write_calls) == 5
    assert {c["ItemName"]: c["Quantity"] for c in server.write_calls}["Rear Derailleur Mechanism"] == 100
    assert "Submission after review" in client.get(f"/runs/{run_id}").text


def test_edit_without_reason_is_refused(web):
    client, _, ledger = web
    start(client)
    it = ledger.intents([S.PENDING_APPROVAL])[0]
    resp = client.post(f"/intents/{it.intent_id}/approve", data={"quantity": it.quantity - 10})
    assert "reason is required" in resp.text
    assert ledger.get(it.intent_id).status is S.PENDING_APPROVAL


def test_large_edit_needs_override(web):
    client, _, ledger = web
    start(client)
    it = ledger.intents([S.PENDING_APPROVAL])[0]
    resp = client.post(f"/intents/{it.intent_id}/approve", data={"quantity": 290, "reason": "stock up"})
    assert "override" in resp.text and ledger.get(it.intent_id).status is S.PENDING_APPROVAL
    resp = client.post(
        f"/intents/{it.intent_id}/approve", data={"quantity": 290, "reason": "stock up", "override": "true"}
    )
    assert ledger.get(it.intent_id).quantity == 290


def test_batch_signoff_requires_typed_count(web):
    client, _, ledger = web
    run_id = start(client)
    assert client.post(f"/runs/{run_id}/approve-all", data={"confirm_count": 2}).status_code == 400
    client.post(f"/runs/{run_id}/approve-all", data={"confirm_count": 3})
    assert ledger.intents([S.PENDING_APPROVAL]) == []


def test_unknown_outcome_reconciled_in_ledger_page(web):
    client, server, ledger = web
    server.faults["Anti-Slip Pedals"] = Fault("timeout_after_send")
    start(client)
    page = client.get("/ledger").text
    assert "UNKNOWN" in page and "Reconcile" in page
    unknown = ledger.intents([S.UNKNOWN])[0]
    client.post(f"/intents/{unknown.intent_id}/resolve", data={"sap_po_number": "Ant-101"})
    assert ledger.get(unknown.intent_id).status is S.SUBMITTED


def test_untrusted_text_is_escaped(web):
    client, server, _ = web
    part = next(p for p in server.parts if p["Title"] == "Performance Front Derailleur")
    part["Title"] = "<script>alert(1)</script>"
    run_id = start(client)
    page = client.get(f"/runs/{run_id}").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_static_htmx_served(web):
    client, _, _ = web
    js = client.get("/static/htmx.min.js")
    assert js.status_code == 200 and re.match(r"var htmx=", js.text)


def test_llm_briefing_and_concerns_are_shown_to_the_approver(tmp_path):
    """An LLM concern moves an AUTO order to the approver, and the page says why, quoting the data."""
    from lato_reorder.llm.analyst import Analyst
    from lato_reorder.mcp_gateway.inventory import load_fixture_snapshot
    from lato_reorder.plan import ConcernCode, build_plan
    from tests.fakes.fake_llm import FakeCaller, concern, good_narrative

    policy = load_policy(ROOT / "config")
    plan = build_plan(load_fixture_snapshot(ROOT / "tests" / "fixtures" / "live"), policy, now=T0, run_id="x")
    decisions = {r.decision.facts.part_key: r.decision for r in plan.recommendations}

    def script(key, attempt):
        if key == "anti slip pedals":
            return good_narrative(decisions[key], concerns=[concern(ConcernCode.OTHER, "anti-slip surface")])
        return None

    server = FakeLatoServer()
    ledger = Ledger(tmp_path / "ledger.db")
    deps = Deps(
        policy=policy,
        ledger=ledger,
        client_factory=server.client,
        clock=lambda: T0,
        mode=Mode.SANDBOX,
        plans_dir=tmp_path / "plans",
        lock_path=tmp_path / "submit.lock",
        narrator=Analyst(FakeCaller(decisions, script), policy).narrate_plan,
    )
    app = create_app(deps, tmp_path / "checkpoints.db", approver="tester", docs_dir=ROOT / "docs")
    with TestClient(app) as client:
        page = client.get(f"/runs/{start(client)}").text
    ledger.close()
    assert "Analyst briefing" in page and "Orders ready for review." in page
    assert "Awaiting your approval (4)" in page  # pedals no longer auto-approved
    assert "RTE-H-LLM-CONCERN" in page and "anti-slip surface" in page and "sent to a human" in page
    assert len(server.write_calls) == 2  # only the two remaining AUTO orders went through
