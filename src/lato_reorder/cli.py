"""`lato` command-line entry point. Commands are added milestone by milestone."""

import typer
from rich.console import Console

from lato_reorder.settings import PROJECT_ROOT, get_settings

# Captured tool payloads, found from the project itself so commands work from any folder.
FIXTURES = str(PROJECT_ROOT / "tests" / "fixtures" / "live")

app = typer.Typer(help="Lato reorder agent.", no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Lato reorder agent."""


@app.command()
def status() -> None:
    """Show the active configuration (secrets redacted)."""
    s = get_settings()
    console.print(f"mode:           {s.mode}")
    console.print(f"submit enabled: {s.submit_enabled}")
    console.print(f"MCP url:        {s.mcp_url}")
    console.print(f"MCP username:   {s.mcp_username or '(not set)'}")
    console.print(f"MCP password:   {'set' if s.mcp_password.get_secret_value() else '(not set)'}")
    console.print(f"LLM model:      {s.llm_model}")
    console.print(f"data dir:       {s.data_dir}")


@app.command()
def calibrate(
    live: bool = typer.Option(False, "--live", help="Fetch fresh data from the MCP server."),
    fixtures: str = typer.Option(FIXTURES, help="Captured payloads used when not --live."),
) -> None:
    """Report how candidate policy settings behave on real data. Never orders anything."""
    import asyncio
    from pathlib import Path

    from rich.markdown import Markdown

    from lato_reorder.calibrate import build_report
    from lato_reorder.mcp_gateway.inventory import fetch_snapshot, load_fixture_snapshot
    from lato_reorder.policy.config import load_policy

    s = get_settings()
    snapshot = asyncio.run(fetch_snapshot(s)) if live else load_fixture_snapshot(Path(fixtures))
    report = build_report(snapshot, load_policy(s.config_dir))
    s.data_dir.mkdir(parents=True, exist_ok=True)
    out = s.data_dir / "calibration_report.md"
    out.write_text(report)
    console.print(Markdown(report))
    console.print(f"\nSaved to {out}")


@app.command()
def plan(
    live: bool = typer.Option(False, "--live", help="Fetch fresh data from the MCP server."),
    fixtures: str = typer.Option(FIXTURES, help="Captured payloads used when not --live."),
    llm: str = typer.Option(
        "claude", help="'claude' (LLM analyst, needs ANTHROPIC_API_KEY) or 'none' (templates)."
    ),
) -> None:
    """Preview a reorder plan: decides and explains every part, never places orders."""
    import asyncio
    from pathlib import Path

    from lato_reorder.app import build_narrator
    from lato_reorder.mcp_gateway.inventory import fetch_snapshot, load_fixture_snapshot
    from lato_reorder.plan import ReorderPlan, build_plan
    from lato_reorder.policy.config import load_policy
    from lato_reorder.report.render import print_plan, save_plan

    if llm not in ("claude", "none"):
        raise typer.BadParameter("--llm must be 'claude' or 'none'")
    s = get_settings()
    snapshot = asyncio.run(fetch_snapshot(s)) if live else load_fixture_snapshot(Path(fixtures))
    result = build_plan(snapshot, load_policy(s.config_dir), mode="PREVIEW")
    narrator = build_narrator(s) if llm == "claude" else None
    if llm == "claude" and narrator is None:
        raise typer.BadParameter("LLM disabled or ANTHROPIC_API_KEY missing; use --llm none")
    if narrator is not None:
        narrate = narrator

        async def narrate_plan() -> "ReorderPlan":
            return await narrate(result, snapshot)

        result = asyncio.run(narrate_plan())
        console.print(f"LLM: {result.llm} usage={result.llm_usage}")
        if result.summary:
            console.print(f"[bold]Summary:[/] {result.summary.headline}")
    print_plan(result, console)
    json_path, md_path = save_plan(result, s.data_dir / "plans")
    console.print(f"\nSaved {json_path} and {md_path}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    execute: bool = typer.Option(False, "--execute", help="Required (with SUBMIT_ENABLED) in PRODUCTION."),
) -> None:
    """Run the approver web UI."""
    import uvicorn

    from lato_reorder.app import build_deps
    from lato_reorder.approval.web import create_app

    s = get_settings()
    web_app = create_app(
        build_deps(s, execute=execute), s.checkpoints_path, s.approver, PROJECT_ROOT / "docs"
    )
    console.print(f"Mode [bold]{s.mode}[/]. Open http://{host}:{port}")
    uvicorn.run(web_app, host=host, port=port)


@app.command()
def run(execute: bool = typer.Option(False, "--execute")) -> None:
    """Run the workflow headless. Stops at human review; approve in the web UI, then `lato resume`."""
    import asyncio

    from lato_reorder.app import build_deps
    from lato_reorder.orchestrator.runner import Orchestrator

    s = get_settings()

    async def go() -> None:
        async with Orchestrator(build_deps(s, execute=execute), s.checkpoints_path) as orch:
            status = await orch.start()
        values = status.values
        console.print(f"Run {status.run_id}: {values.get('plan', {}).get('totals')}")
        console.print(f"Auto submission: {values.get('auto_submit')}")
        if status.waiting_for_review:
            console.print(
                f"[yellow]{len(status.pending)} orders await approval.[/] Review with `lato serve`."
            )

    asyncio.run(go())


@app.command()
def resume(run_id: str, execute: bool = typer.Option(False, "--execute")) -> None:
    """Resume a run paused for review (submits whatever has been approved)."""
    import asyncio

    from lato_reorder.app import build_deps
    from lato_reorder.orchestrator.runner import Orchestrator

    s = get_settings()

    async def go() -> None:
        async with Orchestrator(build_deps(s, execute=execute), s.checkpoints_path) as orch:
            status = await orch.resume(run_id, f"user:{s.approver}")
        console.print(f"Run {run_id} done={status.done}. Submission: {status.values.get('submit')}")

    asyncio.run(go())


ledger_app = typer.Typer(help="Inspect and maintain the PO ledger.", no_args_is_help=True)
app.add_typer(ledger_app, name="ledger")


@ledger_app.command("list")
def ledger_list(open_only: bool = typer.Option(False, "--open", help="Only open intents.")) -> None:
    """List PO intents."""
    from rich.table import Table

    from lato_reorder.ledger.repo import Ledger
    from lato_reorder.ledger.states import OPEN

    s = get_settings()
    lg = Ledger(s.ledger_path)
    table = Table("Intent", "Part", "Qty", "Status", "SAP PO", "Run", "Updated")
    for it in lg.intents(OPEN if open_only else None):
        table.add_row(
            it.intent_id,
            it.item_name,
            str(it.quantity),
            it.status.value,
            it.sap_po_number or "",
            it.run_id,
            it.updated_at[:16],
        )
    console.print(f"Ledger {s.ledger_path}")
    console.print(table)


@ledger_app.command("reset")
def ledger_reset(
    sandbox: bool = typer.Option(False, "--sandbox", help="Required: confirms a non-production reset."),
) -> None:
    """Delete the SANDBOX ledger and checkpoints (never PRODUCTION)."""
    s = get_settings()
    if s.mode.value == "PRODUCTION" or not sandbox:
        raise typer.BadParameter("only the SANDBOX ledger can be reset, and --sandbox is required")
    for path in (s.ledger_path, s.checkpoints_path):
        for suffix in ("", "-wal", "-shm"):
            p = path.with_name(path.name + suffix)
            if p.exists():
                p.unlink()
    console.print(f"Reset {s.mode} ledger and checkpoints.")


if __name__ == "__main__":
    app()
