from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from agent.agent_init import _merge_custom_provider_extra_body
from agent.transports import get_transport
from hermes_cli.profile_distribution import read_manifest
from toolsets import resolve_toolset
from tools.skills_sync import _discover_bundled_skills


PROFILE = Path(__file__).resolve().parents[2] / "profiles" / "cxba-production"


def test_distribution_is_valid_native_profile() -> None:
    manifest = read_manifest(PROFILE)
    assert manifest.name == "cxba-production"
    assert manifest.hermes_requires == ">=0.20.0"
    assert "skills" in manifest.distribution_owned


def test_repository_bundled_entries_point_to_profile_skill_sources() -> None:
    bundled = PROFILE.parents[1] / "skills" / "cxba"
    packaged = PROFILE / "skills" / "cxba"
    for packaged_skill in packaged.iterdir():
        if not packaged_skill.is_dir():
            continue
        thin_entry = bundled / packaged_skill.name / "SKILL.md"
        assert thin_entry.is_symlink()
        assert thin_entry.resolve() == packaged_skill / "SKILL.md"


def test_native_bundled_skill_discovery_follows_thin_entries() -> None:
    bundled_root = PROFILE.parents[1] / "skills"
    discovered = {
        name
        for name, path in _discover_bundled_skills(bundled_root)
        if path.parts[-2] == "cxba"
    }
    assert discovered == {
        path.name
        for path in (PROFILE / "skills" / "cxba").iterdir()
        if path.is_dir()
    }


def test_readonly_skills_toolset_excludes_runtime_mutation() -> None:
    tools = set(resolve_toolset("skills_readonly"))
    assert tools == {"skills_list", "skill_view"}
    assert "skill_manage" not in tools


def test_production_profile_has_no_cross_session_memory_or_external_fallback() -> None:
    config = yaml.safe_load((PROFILE / "config.yaml").read_text(encoding="utf-8"))
    assert config["memory"]["memory_enabled"] is False
    assert config["memory"]["user_profile_enabled"] is False
    assert config["curator"]["enabled"] is False
    assert config["fallback_providers"] == []
    enabled = set(config["platform_toolsets"]["api_server"])
    assert "skills_readonly" in enabled
    assert "skills" not in enabled
    assert "memory" not in enabled
    assert "session_search" not in enabled


def test_production_profile_disables_runtime_network_and_context_files() -> None:
    config = yaml.safe_load((PROFILE / "config.yaml").read_text(encoding="utf-8"))
    assert config["terminal"]["backend"] == "docker"
    assert config["terminal"]["docker_network"] is True
    assert config["terminal"]["container_persistent"] is False
    assert "timeout" not in config["terminal"]
    assert "lifetime_seconds" not in config["terminal"]
    api_config = config["gateway"]["platforms"]["api_server"]
    assert api_config["skip_context_files"] is True
    assert api_config["load_soul_identity"] is False


def test_local_model_must_be_deployment_configured() -> None:
    config = yaml.safe_load((PROFILE / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["provider"] == "custom:cxba-bailian"
    assert config["model"]["default"] == "${env:CXBA_LOCAL_MODEL}"
    assert config["model"]["base_url"] == "${env:CXBA_LOCAL_MODEL_BASE_URL}"
    [provider] = config["custom_providers"]
    assert provider["name"] == "cxba-bailian"
    assert provider["key_env"] == "CXBA_BAILIAN_API_KEY"
    assert provider["extra_body"] == {"enable_thinking": False}


def test_production_provider_thinking_follows_each_session_reasoning_setting() -> None:
    config = yaml.safe_load((PROFILE / "config.yaml").read_text(encoding="utf-8"))
    agent = SimpleNamespace(
        provider=config["model"]["provider"],
        model=config["model"]["default"],
        base_url=config["model"]["base_url"],
        request_overrides={},
    )
    _merge_custom_provider_extra_body(agent, config["custom_providers"])
    transport = get_transport("chat_completions")

    for enabled, effort in ((True, "medium"), (False, "none")):
        kwargs = transport.build_kwargs(
            model=agent.model,
            messages=[{"role": "user", "content": "Hi"}],
            tools=[],
            reasoning_config={"enabled": enabled, "effort": effort},
            request_overrides=agent.request_overrides,
        )

        assert kwargs["extra_body"]["enable_thinking"] is enabled


def test_private_gateway_storage_and_spring_mcp_are_environment_bound() -> None:
    config = yaml.safe_load((PROFILE / "config.yaml").read_text(encoding="utf-8"))
    gateway = config["gateway"]
    assert gateway["case_storage_root"] == "${env:CXBA_CASE_STORAGE_ROOT}"
    assert gateway["cxba_private_ws"]["token_env"] == "CXBA_GATEWAY_PRIVATE_TOKEN"
    spring = config["mcp_servers"]["cxba_spring"]
    assert spring["url"] == "${env:CXBA_SPRING_MCP_URL}"
    assert spring["trust_env"] is False
    assert spring["headers"]["Authorization"] == "Bearer ${env:CXBA_SPRING_MCP_TOKEN}"
    assert spring["cxba_trusted_context"] is True
