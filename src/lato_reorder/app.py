"""Wire real dependencies from settings (used by the CLI and the web server)."""

from datetime import UTC, datetime

from lato_reorder.ledger.repo import Ledger
from lato_reorder.mcp_gateway.client import McpClient
from lato_reorder.orchestrator.graph import Deps, Narrator
from lato_reorder.policy.config import load_policy
from lato_reorder.settings import Settings


def build_narrator(settings: Settings) -> Narrator | None:
    """The LLM analyst when enabled and a key is configured; otherwise template explanations only."""
    if not settings.llm_enabled or settings.anthropic_api_key is None:
        return None
    from lato_reorder.llm.analyst import make_analyst

    analyst = make_analyst(
        settings.anthropic_api_key.get_secret_value(), settings.llm_model, load_policy(settings.config_dir)
    )
    return analyst.narrate_plan


def build_deps(settings: Settings, *, execute: bool = False, narrator: Narrator | None = None) -> Deps:
    def client() -> McpClient:
        return McpClient(settings.mcp_url, settings.mcp_username, settings.mcp_password.get_secret_value())

    return Deps(
        policy=load_policy(settings.config_dir),
        ledger=Ledger(settings.ledger_path),
        client_factory=client,
        clock=lambda: datetime.now(UTC),
        mode=settings.mode,
        plans_dir=settings.data_dir / "plans",
        lock_path=settings.data_dir / f"submit.{settings.mode.value.lower()}.lock",
        submit_enabled=settings.submit_enabled,
        execute=execute,
        narrator=narrator if narrator is not None else build_narrator(settings),
    )
