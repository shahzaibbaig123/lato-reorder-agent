"""Render a plan for the terminal (rich) and as Markdown / JSON files."""

from pathlib import Path

from rich.console import Console
from rich.table import Table

from lato_reorder.plan import ReorderPlan
from lato_reorder.policy.rules import Route

ROUTE_STYLE = {Route.BLOCK: "bold red", Route.HUMAN: "yellow", Route.AUTO: "green", Route.NONE: "dim"}


def print_plan(plan: ReorderPlan, console: Console) -> None:
    t = plan.totals
    console.print(
        f"[bold]Plan {plan.run_id}[/] mode={plan.mode} llm={plan.llm} policy={plan.policy_version} "
        f"snapshot={plan.snapshot_hash[:12]}"
    )
    console.print(
        f"{t.reorder_lines} orders / {t.reorder_units} units: {t.human_lines} for a human, "
        f"{t.auto_lines} auto ({t.auto_units} units), {t.blocked_lines} blocked. {t.parts} parts reviewed."
    )
    table = Table(show_lines=False, expand=True)
    for col in ("Part", "Tier", "Stock", "IP/ROP", "Decision", "Qty", "Route", "Rules", "Why"):
        table.add_column(col, overflow="fold")
    for r in plan.recommendations:
        d = r.decision
        c = d.calc
        table.add_row(
            d.facts.item_name,
            (d.tier.value.split("_", 1)[1] if d.tier else "-"),
            str(d.facts.on_hand if d.facts.on_hand is not None else "?"),
            f"{d.facts.inventory_position}/{c.reorder_point}" if c else "-",
            d.decision.value,
            str(d.quantity or ""),
            f"[{ROUTE_STYLE[r.final_route]}]{r.final_route.value}[/]",
            ", ".join(x.value for x in d.route_reasons if x.value != "RTE-AUTO") or d.primary_rule.value,
            r.narrative.justification,
        )
    console.print(table)


def to_markdown(plan: ReorderPlan) -> str:
    t = plan.totals
    lines = [
        f"# Reorder plan {plan.run_id}",
        "",
        f"Created {plan.created_at:%Y-%m-%d %H:%M} UTC. Mode `{plan.mode}`, LLM `{plan.llm}`, policy "
        f"`{plan.policy_version}` (`{plan.policy_hash}`), snapshot `{plan.snapshot_hash[:16]}`.",
        "",
        f"**{t.reorder_lines} orders, {t.reorder_units} units:** {t.human_lines} need a human, "
        f"{t.auto_lines} auto-approved ({t.auto_units} units), {t.blocked_lines} blocked. "
        f"{t.parts} parts reviewed.",
        "",
    ]
    for title, keep in (
        ("Orders", lambda r: r.decision.quantity > 0 or r.final_route is Route.BLOCK),
        ("No order", lambda r: r.decision.quantity == 0 and r.final_route is not Route.BLOCK),
    ):
        lines += [f"## {title}", ""]
        for r in filter(keep, plan.recommendations):
            d = r.decision
            head = f"### {d.facts.item_name}: {d.decision.value}"
            head += f" {d.quantity} → {r.final_route.value}" if d.quantity else ""
            lines += [head, "", r.narrative.justification, "", r.narrative.quantity_explanation, ""]
            lines.append(f"- Rules: {', '.join(x.value for x in d.rules_applied)}")
            if d.flags:
                lines.append(f"- Flags: {', '.join(x.value for x in d.flags)}")
            if d.calc:
                lines += [f"- `{s.expression}`" for s in d.calc.steps]
            lines.append("")
    return "\n".join(lines)


def save_plan(plan: ReorderPlan, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{plan.run_id}.json"
    md_path = directory / f"{plan.run_id}.md"
    json_path.write_text(plan.model_dump_json(indent=2))
    md_path.write_text(to_markdown(plan))
    return json_path, md_path
