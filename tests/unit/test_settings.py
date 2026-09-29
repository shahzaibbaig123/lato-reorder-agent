from lato_reorder.settings import Mode, Settings


def test_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("LATO_MODE", raising=False)
    monkeypatch.delenv("SUBMIT_ENABLED", raising=False)
    s = Settings(_env_file=None)
    assert s.mode is Mode.SANDBOX
    assert s.submit_enabled is False


def test_password_is_redacted_in_repr(monkeypatch):
    monkeypatch.setenv("LATO_MCP_PASSWORD", "test-password")
    s = Settings(_env_file=None)
    assert "test-password" not in repr(s)
    assert s.mcp_password.get_secret_value() == "test-password"
