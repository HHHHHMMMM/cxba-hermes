from __future__ import annotations

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tui_gateway import server


class _LiveAgent:
    def __init__(self) -> None:
        self.model = "old-model"
        self.provider = "custom"
        self.base_url = "http://127.0.0.1:18080/v1"
        self.api_mode = "chat_completions"
        self.api_key = "old-key"
        self.switches: list[dict] = []

    def switch_model(self, **runtime) -> None:
        self.switches.append(runtime)
        self.model = runtime["new_model"]
        self.provider = runtime["new_provider"]
        self.base_url = runtime["base_url"]
        self.api_mode = runtime["api_mode"]
        self.api_key = runtime["api_key"]


def _write_current_deployment(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for name in (
        "HERMES_MODEL",
        "HERMES_INFERENCE_MODEL",
        "HERMES_TUI_PROVIDER",
        "HERMES_INFERENCE_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CXBA_TEST_CURRENT_MODEL_KEY", "test-key")
    (hermes_home / "config.yaml").write_text(
        """
model:
  default: current-model
  provider: custom:current-endpoint
fallback_providers: []
custom_providers:
  - name: current-endpoint
    base_url: https://current.example/v1
    model: current-model
    key_env: CXBA_TEST_CURRENT_MODEL_KEY
    api_mode: chat_completions
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return set_hermes_home_override(hermes_home)


def _cxba_session(agent: _LiveAgent) -> dict:
    return {
        "agent": agent,
        "session_key": "stored-cxba-session",
        "cxba_binding": {"case_id": "case-1"},
        "model_override": {
            "model": "old-model",
            "provider": "custom:old-endpoint",
            "base_url": "http://127.0.0.1:18080/v1",
        },
        "pending_model_switch": {"raw": "another-session-model"},
    }


def test_cxba_run_refreshes_stale_endpoint_from_current_deployment(
    tmp_path, monkeypatch
):
    home_token = _write_current_deployment(tmp_path, monkeypatch)
    # A generic Hermes launch seed must not override the current CXBA profile.
    monkeypatch.setenv("HERMES_MODEL", "seed-model")
    monkeypatch.setenv("HERMES_INFERENCE_MODEL", "seed-model")
    agent = _LiveAgent()
    session = _cxba_session(agent)
    persisted = []
    emitted = []
    monkeypatch.setattr(server, "_restart_slash_worker", lambda *_args: None)
    monkeypatch.setattr(
        server, "_persist_live_session_runtime", lambda current: persisted.append(current)
    )
    monkeypatch.setattr(server, "_session_info", lambda *_args: {"model": agent.model})
    monkeypatch.setattr(
        server, "_emit", lambda event, sid, payload: emitted.append((event, sid, payload))
    )

    try:
        server._sync_cxba_agent_with_deployment_config("runtime-cxba", session)
    finally:
        reset_hermes_home_override(home_token)

    assert agent.switches == [
        {
            "new_model": "current-model",
            "new_provider": "custom",
            "api_key": "test-key",
            "base_url": "https://current.example/v1",
            "api_mode": "chat_completions",
        }
    ]
    assert "model_override" not in session
    assert "pending_model_switch" not in session
    assert persisted == [session]
    assert emitted == [
        ("session.info", "runtime-cxba", {"model": "current-model"})
    ]


def test_cxba_run_fails_closed_when_current_deployment_cannot_resolve(
    tmp_path, monkeypatch
):
    home_token = _write_current_deployment(tmp_path, monkeypatch)
    agent = _LiveAgent()
    monkeypatch.setattr(
        server,
        "_resolve_runtime_with_fallback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    try:
        with pytest.raises(RuntimeError, match="deployment model resolution failed"):
            server._sync_cxba_agent_with_deployment_config(
                "runtime-cxba", _cxba_session(agent)
            )
    finally:
        reset_hermes_home_override(home_token)

    assert agent.switches == []


def test_ordinary_hermes_session_is_not_affected(monkeypatch):
    agent = _LiveAgent()
    session = {
        "agent": agent,
        "session_key": "ordinary-session",
        "model_override": {"model": "old-model"},
    }
    monkeypatch.setattr(
        server,
        "_resolve_startup_runtime",
        lambda: pytest.fail("ordinary session must keep its Hermes model policy"),
    )

    server._sync_cxba_agent_with_deployment_config("ordinary", session)

    assert session["model_override"] == {"model": "old-model"}
    assert agent.switches == []
