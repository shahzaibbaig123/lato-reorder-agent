"""End-to-end sandbox demo: four runs against the live MCP server, driven through the approver web UI.

    LATO_MODE=SANDBOX uv run python scripts/demo.py --fresh

1. Run 1 plans, auto-submits the AUTO lines and pauses. The approver approves one line, edits another
   (the 1.5x guard refuses an over-large edit first) and approves the third. A crash is then simulated on
   the third line after its write-ahead commit (SUBMITTING, SAP never called). "Submit approved" resumes.
2. Run 2: the crashed line is recovered as UNKNOWN and blocks its part; nothing else is re-ordered.
3. The approver checks SAP, finds no PO and resolves the line as "no PO".
4. Run 3 re-plans that one part; the approver signs off and it is submitted.
5. Run 4 places nothing: every part is covered by an open PO.

Approvals are scripted HTTP calls to the same endpoints the buttons use; they are recorded in the ledger
under LATO_APPROVER. Output: docs/sample_run/ (write-up, plan per run, page snapshots, full event log).
"""

import argparse
import asyncio
import html
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEMO_DATA = ROOT / "data" / "demo"
OUT = ROOT / "docs" / "sample_run"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
os.environ.setdefault("LATO_DATA_DIR", str(DEMO_DATA))
os.environ.setdefault("LATO_APPROVER", "Mirza Shahzaib Baig (scripted demo)")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from lato_reorder.app import build_deps  # noqa: E402
from lato_reorder.approval.web import create_app  # noqa: E402
from lato_reorder.ledger.repo import Intent, Ledger  # noqa: E402
from lato_reorder.ledger.states import IntentStatus as S  # noqa: E402
from lato_reorder.plan import ReorderPlan  # noqa: E402
from lato_reorder.settings import PROJECT_ROOT, Mode, get_settings  # noqa: E402

log: list[str] = []


def say(text: str = "") -> None:
    print(text)
    log.append(text)


def error_text(page: str) -> str:
    m = re.search(r'<div class="error">(.*?)</div>', page, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def by_part(intents: list[Intent], name: str) -> Intent:
    return next(i for i in intents if i.item_name == name)


def sap_calls(ledger: Ledger) -> int:
    """Calls to RaiseSAPPurchaseOrder, counted from the ledger: every call ends in a recorded outcome
    (SUBMITTED, MISMATCH, FAILED or UNKNOWN). Crash recovery and never-sent releases are not calls."""
    return sum(
        1 for e in ledger.events() if e["kind"].startswith("SUBMITTING->") and e["to_status"] != "APPROVED"
    )


def plan_table(plan: ReorderPlan) -> list[str]:
    rows = [
        "| Part | Tier | On hand | On order | Decision | Qty | Route | Rule |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in plan.recommendations:
        d = r.decision
        if d.quantity == 0 and d.primary_rule.value == "HOLD-ABOVE-ROP":
            continue
        rows.append(
            f"| {d.facts.item_name} | {d.tier.value if d.tier else '-'} | {d.facts.on_hand} | "
            f"{d.facts.on_order} | {d.decision.value} | {d.quantity or ''} | {r.final_route.value} | "
            f"{d.primary_rule.value} |"
        )
    held = sum(1 for r in plan.recommendations if r.decision.primary_rule.value == "HOLD-ABOVE-ROP")
    rows.append(f"\n{held} other parts are above their reorder point (HOLD-ABOVE-ROP).")
    return rows


async def new_run(client: httpx.AsyncClient, ledger: Ledger, label: str) -> str:
    before = sap_calls(ledger)
    r = await client.post("/runs")
    assert r.status_code == 303, r.text
    run_id = r.headers["location"].rsplit("/", 1)[1]
    plan = ledger.run_plan(run_id)
    page = await client.get(f"/runs/{run_id}")
    (OUT / "pages" / f"{label.lower().replace(' ', '')}-{run_id}.html").write_text(page.text)
    t = plan.totals
    say(f"## {label}: run `{run_id}`")
    say()
    say(
        f"{t.reorder_lines} order{'' if t.reorder_lines == 1 else 's'} / {t.reorder_units} units; "
        f"{t.auto_lines} AUTO, {t.human_lines} HUMAN, "
        f"{t.blocked_lines} BLOCK. New intents: {len(ledger.intents(run_id=run_id))}. "
        f"SAP calls during planning: {sap_calls(ledger) - before}."
    )
    if plan.summary:
        say(f"\n> LLM briefing: {plan.summary.headline}")
    say()
    for line in plan_table(plan):
        say(line)
    say()
    return run_id


async def main(fresh: bool) -> None:
    settings = get_settings()
    if settings.mode is not Mode.SANDBOX:
        raise SystemExit("Run with LATO_MODE=SANDBOX: this demo places sandbox purchase orders.")
    if fresh and DEMO_DATA.exists():
        shutil.rmtree(DEMO_DATA)
    if settings.ledger_path.exists():
        raise SystemExit(
            f"{settings.ledger_path} exists; pass --fresh to start the demo from an empty ledger"
        )
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "pages").mkdir(parents=True)

    deps = build_deps(settings)
    ledger = deps.ledger
    app = create_app(deps, settings.checkpoints_path, settings.approver, PROJECT_ROOT / "docs")
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", timeout_graceful_shutdown=10)
    )
    serving = asyncio.create_task(server.serve())

    while not server.started:
        await asyncio.sleep(0.05)

    say("# Sample run: sandbox, end to end")
    say()
    say(
        f"Recorded {datetime.now(UTC):%Y-%m-%d %H:%M} UTC against the live Lato MCP sandbox, mode `SANDBOX`, "
        f"LLM `{settings.llm_model if deps.narrator else 'none (templates)'}`. Generated by "
        "`scripts/demo.py`; approvals are scripted calls to the web UI's own endpoints, recorded under "
        f"`user:{settings.approver}`. In this sandbox `RaiseSAPPurchaseOrder` has no side effects, so stock "
        "never changes after ordering: only the ledger stops a re-order."
    )
    say()
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=300) as client:
            # ---- Run 1 ------------------------------------------------------------------------------
            run1 = await new_run(client, ledger, "Run 1")
            auto = [i for i in ledger.intents(run_id=run1) if i.route == "AUTO"]
            say(
                "AUTO lines went straight to SAP: "
                + ", ".join(f"{i.item_name} x{i.quantity} → `{i.sap_po_number}`" for i in auto)
                + "."
            )
            say()
            pending = ledger.intents([S.PENDING_APPROVAL])
            names = [i.item_name for i in pending]
            say(f"Paused for sign-off on {len(pending)} lines: {', '.join(names)}.")
            say()
            first, second, third = pending[0], pending[1], pending[2]

            await client.post(f"/intents/{first.intent_id}/approve", data={})
            say(f"- **Approve** {first.item_name} x{first.quantity} as recommended.")

            too_many = second.engine_quantity * 2
            r = await client.post(
                f"/intents/{second.intent_id}/approve",
                data={"quantity": too_many, "reason": "stock up before the season"},
            )
            say(f'- **Edit** {second.item_name} to {too_many}: refused, "{error_text(r.text)}".')
            edited = (second.engine_quantity // 10 + 1) * 10
            r = await client.post(
                f"/intents/{second.intent_id}/approve",
                data={"quantity": edited, "reason": "supplier ships in tens"},
            )
            say(
                f"- **Edit** {second.item_name} from {second.engine_quantity} to {edited} "
                f'(reason "supplier ships in tens"): {ledger.get(second.intent_id).status.value}.'
            )
            await client.post(f"/intents/{third.intent_id}/approve", data={})
            say(f"- **Approve** {third.item_name} x{third.quantity}.")

            # Simulated crash: the write-ahead commit happened, then the process died before SAP answered.
            part = next(
                p
                for p in ledger.run_plan(run1).recommendations
                if p.decision.facts.part_key == third.part_key
            )
            ledger.begin_submit(third.intent_id, deps.clock(), on_hand=part.decision.facts.on_hand)
            say(
                f"- **Simulated crash** on {third.item_name}: `APPROVED → SUBMITTING` committed, then the "
                'process "died" before the SAP call. Nobody can tell from the ledger whether SAP got it.'
            )
            before = sap_calls(ledger)
            await client.post(f"/runs/{run1}/submit")
            submitted = [ledger.get(i.intent_id) for i in (first, second)]
            say(
                f"- **Submit approved**: {sap_calls(ledger) - before} SAP calls: "
                + ", ".join(f"{i.item_name} x{i.quantity} → `{i.sap_po_number}`" for i in submitted)
                + f". {third.item_name} is left alone (still `{ledger.get(third.intent_id).status.value}`)."
            )
            say()

            # ---- Run 2 ------------------------------------------------------------------------------
            await new_run(client, ledger, "Run 2")
            crashed = ledger.get(third.intent_id)
            say(
                f"Crash recovery turned {third.item_name} into `{crashed.status.value}`: it is never retried "
                "automatically, and the part is blocked (`RTE-B-INFLIGHT`) until a human checks SAP. Every "
                "other part is covered by its open PO."
            )
            say()

            # ---- Reconcile --------------------------------------------------------------------------
            say("## Reconcile the unknown outcome")
            say()
            await client.post(f"/intents/{third.intent_id}/resolve", data={"sap_po_number": ""})
            say(
                f"The approver looks up {third.item_name} in SAP, finds no PO and resolves the line as "
                f'"no PO" (`{ledger.get(third.intent_id).status.value}`). Had SAP shown a PO, entering its '
                "number would have closed the line as `SUBMITTED` and nothing would be re-ordered."
            )
            say()

            # ---- Run 3 ------------------------------------------------------------------------------
            run3 = await new_run(client, ledger, "Run 3")
            again = ledger.intents([S.PENDING_APPROVAL])
            for i in again:
                await client.post(f"/intents/{i.intent_id}/approve", data={})
            before = sap_calls(ledger)
            await client.post(f"/runs/{run3}/submit")
            done = [ledger.get(i.intent_id) for i in again]
            say(
                f"Approved and submitted ({sap_calls(ledger) - before} SAP call): "
                + ", ".join(f"{i.item_name} x{i.quantity} → `{i.sap_po_number}`" for i in done)
                + "."
            )
            say()

            # ---- Run 4 ------------------------------------------------------------------------------
            await new_run(client, ledger, "Run 4")
            (OUT / "pages" / "ledger.html").write_text((await client.get("/ledger")).text)
    finally:
        server.should_exit = True
        await serving

    # ---- Final ledger and audit trail ----------------------------------------------------------------
    intents = ledger.intents()
    say("## Final ledger")
    say()
    say("| Part | Qty (recommended) | Route | Status | SAP PO | SAP reply |")
    say("|---|---|---|---|---|---|")
    for i in intents:
        say(
            f"| {i.item_name} | {i.quantity} ({i.engine_quantity}) | {i.route} | {i.status.value} | "
            f"{i.sap_po_number or ''} | {(i.raw_response or '').strip()} |"
        )
    say()
    parts = [i.part_key for i in intents if i.status is S.SUBMITTED]
    say(
        f"**{sap_calls(ledger)} SAP calls in total, {len(parts)} open POs, and no part ordered twice** "
        f"({len(parts)} submitted intents, {len(set(parts))} distinct parts)."
    )
    say()
    say(
        "Files: `plans/` holds each run's full plan (every part, with its explanation and derivation), "
        "`pages/` the approver pages as rendered, and `events.json` the ledger's append-only event log."
    )
    events: list[dict[str, Any]] = ledger.events()
    for e in events:
        e["detail"] = json.loads(e["detail"]) if e["detail"] else None
    (OUT / "events.json").write_text(json.dumps(events, indent=1))
    shutil.copytree(settings.data_dir / "plans", OUT / "plans")
    (OUT / "README.md").write_text("\n".join(log) + "\n")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fresh", action="store_true", help="delete data/demo first")
    asyncio.run(main(parser.parse_args().fresh))
