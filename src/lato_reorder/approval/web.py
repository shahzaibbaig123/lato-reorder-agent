"""Approver web UI (FastAPI + Jinja2 + HTMX).

Every approve / edit / reject click is written to the ledger immediately (audited, bound to the exact
payload). "Submit approved" resumes the paused LangGraph run, which submits through the ledger-guarded
submitter. All template output is auto-escaped: part names and descriptions are untrusted text.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from lato_reorder.ledger.repo import Intent, LedgerError, StateConflict
from lato_reorder.ledger.states import OPEN, UNRESOLVED
from lato_reorder.ledger.states import IntentStatus as S
from lato_reorder.orchestrator.graph import Deps
from lato_reorder.orchestrator.runner import Orchestrator
from lato_reorder.policy.rules import RULE_DESCRIPTIONS, Route, RuleId

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
EDIT_OVERRIDE_FACTOR = 1.5
ROUTE_ORDER = {Route.BLOCK: 0, Route.HUMAN: 1, Route.AUTO: 2, Route.NONE: 3}


def create_app(
    deps: Deps, checkpoints_path: Path | str, approver: str, docs_dir: Path | None = None
) -> FastAPI:
    orchestrator = Orchestrator(deps, checkpoints_path)
    ledger = deps.ledger
    actor = f"user:{approver}"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with orchestrator:
            yield

    app = FastAPI(title="Lato reorder agent", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        ctx.update(mode=deps.mode.value, approver=approver, rules=RULE_DESCRIPTIONS)
        return TEMPLATES.TemplateResponse(request, name, ctx)

    def intent_row(request: Request, intent: Intent, error: str | None = None) -> HTMLResponse:
        return render(request, "_intent.html", it=intent, error=error, S=S)

    def guarded(fn: Callable[[], Intent], request: Request, intent_id: str) -> HTMLResponse:
        try:
            return intent_row(request, fn())
        except (LedgerError, StateConflict, ValueError) as exc:
            return intent_row(request, ledger.get(intent_id), error=str(exc))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        runs = []
        for r in ledger.runs():
            st = await orchestrator.status(r["run_id"])
            runs.append({**r, "waiting": st.waiting_for_review, "done": st.done})
        return render(request, "index.html", runs=runs)

    @app.post("/runs")
    async def start_run() -> Response:
        status = await orchestrator.start()
        return RedirectResponse(f"/runs/{status.run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_page(request: Request, run_id: str) -> HTMLResponse:
        try:
            plan = ledger.run_plan(run_id)
        except LedgerError as exc:
            raise HTTPException(404, str(exc)) from exc
        status = await orchestrator.status(run_id)
        pending = ledger.intents([S.PENDING_APPROVAL])
        run_intents = {i.part_key: i for i in ledger.intents(run_id=run_id)}
        recs = sorted(plan.recommendations, key=lambda r: (ROUTE_ORDER[r.final_route], -r.decision.priority))
        return render(
            request,
            "run.html",
            plan=plan,
            recs=recs,
            status=status,
            pending=pending,
            run_intents=run_intents,
            S=S,
            RuleId=RuleId,
        )

    @app.post("/intents/{intent_id}/approve", response_class=HTMLResponse)
    async def approve(
        request: Request,
        intent_id: str,
        quantity: int | None = Form(None),
        reason: str = Form(""),
        override: bool = Form(False),
    ) -> HTMLResponse:
        def act() -> Intent:
            it = ledger.get(intent_id)
            if quantity is not None and quantity != it.quantity:
                hard_max = deps.policy.routing.hard_max_units_per_po
                if not 1 <= quantity <= hard_max:
                    raise ValueError(f"quantity must be between 1 and {hard_max}")
                if quantity > EDIT_OVERRIDE_FACTOR * it.engine_quantity and not override:
                    raise ValueError(
                        f"more than {EDIT_OVERRIDE_FACTOR}x the recommended {it.engine_quantity}: "
                        "tick 'override' to confirm"
                    )
            return ledger.approve(intent_id, actor, deps.clock(), quantity=quantity, reason=reason or None)

        return guarded(act, request, intent_id)

    @app.post("/intents/{intent_id}/reject", response_class=HTMLResponse)
    async def reject(request: Request, intent_id: str, reason: str = Form("")) -> HTMLResponse:
        def act() -> Intent:
            return ledger.reject(intent_id, actor, reason, deps.clock())

        return guarded(act, request, intent_id)

    @app.post("/runs/{run_id}/approve-all")
    async def approve_all(run_id: str, confirm_count: int = Form(...)) -> Response:
        pending = ledger.intents([S.PENDING_APPROVAL])
        if confirm_count != len(pending):
            raise HTTPException(400, f"type the number of lines ({len(pending)}) to confirm a batch sign-off")
        for it in pending:
            ledger.approve(it.intent_id, actor, deps.clock(), reason="batch sign-off")
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/submit")
    async def submit(run_id: str) -> Response:
        await orchestrator.resume(run_id, actor)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/ledger", response_class=HTMLResponse)
    async def ledger_page(request: Request) -> HTMLResponse:
        intents = list(reversed(ledger.intents()))
        return render(request, "ledger.html", intents=intents, S=S, OPEN=OPEN, UNRESOLVED=UNRESOLVED)

    @app.post("/intents/{intent_id}/resolve")
    async def resolve(intent_id: str, sap_po_number: str = Form("")) -> Response:
        ledger.resolve(intent_id, actor, deps.clock(), sap_po_number.strip() or None)
        return RedirectResponse("/ledger", status_code=303)

    @app.post("/intents/{intent_id}/received")
    async def received(intent_id: str) -> Response:
        ledger.mark_received(intent_id, actor, deps.clock())
        return RedirectResponse("/ledger", status_code=303)

    @app.get("/intents/{intent_id}/events", response_class=HTMLResponse)
    async def events(request: Request, intent_id: str) -> HTMLResponse:
        return render(request, "_events.html", events=ledger.events(intent_id))

    @app.get("/calibration", response_class=HTMLResponse)
    async def calibration(request: Request) -> HTMLResponse:
        path = (docs_dir or Path("docs")) / "CALIBRATION.md"
        text = path.read_text() if path.exists() else "No calibration record found."
        return render(request, "calibration.html", text=text)

    return app
