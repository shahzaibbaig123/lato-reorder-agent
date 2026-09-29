"""Runtime settings, read from environment variables and the local .env file."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Mode(StrEnum):
    SANDBOX = "SANDBOX"  # calls RaiseSAPPurchaseOrder on the shared sandbox (no side effects there)
    PRODUCTION = "PRODUCTION"  # real POs; needs SUBMIT_ENABLED and an explicit execute flag


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    mcp_url: str = Field(
        "https://lato-product-inventory-accp.apps.eu-1c.mendixcloud.com/mendix-mcp/mcp",
        alias="LATO_MCP_URL",
    )
    mcp_username: str = Field("", alias="LATO_MCP_USERNAME")
    mcp_password: SecretStr = Field(SecretStr(""), alias="LATO_MCP_PASSWORD")

    llm_model: str = Field("claude-haiku-4-5", alias="LATO_LLM_MODEL")
    llm_enabled: bool = Field(True, alias="LATO_LLM_ENABLED")  # False = template explanations only
    anthropic_api_key: SecretStr | None = Field(None, alias="ANTHROPIC_API_KEY")

    mode: Mode = Field(Mode.SANDBOX, alias="LATO_MODE")
    submit_enabled: bool = Field(False, alias="SUBMIT_ENABLED")

    approver: str = Field("approver", alias="LATO_APPROVER")  # demo identity; production would use SSO

    data_dir: Path = Field(PROJECT_ROOT / "data", alias="LATO_DATA_DIR")
    config_dir: Path = Field(PROJECT_ROOT / "config", alias="LATO_CONFIG_DIR")

    @property
    def ledger_path(self) -> Path:
        """One ledger per mode, so sandbox history can never count as on-order stock in production."""
        return self.data_dir / f"ledger.{self.mode.value.lower()}.db"

    @property
    def checkpoints_path(self) -> Path:
        return self.data_dir / f"checkpoints.{self.mode.value.lower()}.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
