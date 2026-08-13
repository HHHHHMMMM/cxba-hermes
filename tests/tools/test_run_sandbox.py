import subprocess
from pathlib import Path

import pytest

import tools.run_sandbox as run_sandbox
from tools.run_sandbox import (
    _configured_storage_root,
    bind_run_sandbox,
    current_run_sandbox_id,
    destroy_run_sandbox,
    register_run_sandbox,
    validate_run_context,
    workspace_output_directory,
    workspace_visible_output_path,
)


def _context(root: Path, session_id: str = "session-1") -> dict:
    sources = {}
    for name in ("data", "workspace", "session", "current", "shared"):
        path = root / name
        path.mkdir()
        sources[name] = str(path)
    memory = root / "cases" / "case-1" / "Memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# 案件长期记忆\n", encoding="utf-8")
    sources["memory"] = str(memory)
    return {
        "case_id": "case-1",
        "business_session_id": session_id,
        "business_branch_id": "branch-1",
        "run_id": "run-1",
        "actor_user_id": "user-1",
        "mounts": [
            {"source": sources["data"], "target": "/data", "read_only": True},
            {"source": sources["workspace"], "target": "/workspace", "read_only": False},
            {
                "source": sources["session"],
                "target": f"/case-sessions/{session_id}",
                "read_only": True,
            },
            {
                "source": sources["current"],
                "target": "/exchange/current",
                "read_only": False,
            },
            {"source": sources["shared"], "target": "/shared", "read_only": True},
            {
                "source": sources["memory"],
                "target": "/case/Memory.md",
                "read_only": True,
            },
        ],
    }


def test_validate_run_context_accepts_exact_mount_policy(tmp_path):
    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    assert context.run_id == "run-1"
    assert {mount.target for mount in context.mounts} == {
        "/data", "/workspace", "/case-sessions/session-1", "/exchange/current",
        "/shared", "/case/Memory.md"
    }


def test_validate_run_context_rejects_case_memory_from_another_path(tmp_path):
    raw = _context(tmp_path)
    wrong = tmp_path / "wrong-memory.md"
    wrong.write_text("wrong", encoding="utf-8")
    raw["mounts"][-1]["source"] = str(wrong)

    with pytest.raises(ValueError, match="current case Memory.md"):
        validate_run_context(raw, storage_root=tmp_path)


def test_configured_storage_root_surfaces_config_load_failure(monkeypatch):
    from hermes_cli import config

    monkeypatch.delenv("CXBA_CASE_STORAGE_ROOT", raising=False)

    def fail_load():
        raise RuntimeError("synthetic configuration failure")

    monkeypatch.setattr(config, "load_config", fail_load)
    with pytest.raises(RuntimeError, match="synthetic configuration failure"):
        _configured_storage_root()


def test_validate_run_context_rejects_mount_exposing_reserved_runtime_root(tmp_path):
    raw = _context(tmp_path)
    raw["mounts"][1]["source"] = str(tmp_path)

    with pytest.raises(ValueError, match="reserved runtime directory"):
        validate_run_context(raw, storage_root=tmp_path)


def test_validate_run_context_accepts_other_session_read_only_mounts(tmp_path):
    raw = _context(tmp_path)
    other_workspace = tmp_path / "other-workspace"
    other_exchange = tmp_path / "other-exchange"
    other_workspace.mkdir()
    other_exchange.mkdir()
    raw["mounts"].extend([
        {
            "source": str(other_workspace),
            "target": "/case-sessions/session-2",
            "read_only": True,
        },
        {
            "source": str(other_exchange),
            "target": "/exchange/session-2",
            "read_only": True,
        },
    ])

    context = validate_run_context(raw, storage_root=tmp_path)
    assert "/case-sessions/session-2" in {mount.target for mount in context.mounts}
    assert "/exchange/session-2" in {mount.target for mount in context.mounts}


def test_validate_run_context_rejects_writable_other_session_mount(tmp_path):
    raw = _context(tmp_path)
    other_exchange = tmp_path / "other-exchange"
    other_exchange.mkdir()
    raw["mounts"].append({
        "source": str(other_exchange),
        "target": "/exchange/session-2",
        "read_only": False,
    })

    with pytest.raises(ValueError, match="read-only"):
        validate_run_context(raw, storage_root=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw, root: raw["mounts"][0].update(source=str(root.parent)), "outside"),
        (lambda raw, root: raw["mounts"][0].update(target="/etc"), "not allowed"),
        (lambda raw, root: raw["mounts"].append(dict(raw["mounts"][0])), "duplicate"),
        (lambda raw, root: raw["mounts"][0].update(read_only=False), "read-only"),
        (lambda raw, root: raw["mounts"][1].update(read_only=True), "writable"),
    ],
)
def test_validate_run_context_fails_closed(tmp_path, mutation, message):
    raw = _context(tmp_path)
    mutation(raw, tmp_path)
    with pytest.raises(ValueError, match=message):
        validate_run_context(raw, storage_root=tmp_path)


def test_bound_run_id_overrides_child_task_key(tmp_path):
    from tools import terminal_tool

    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    try:
        with bind_run_sandbox(context.run_id):
            assert current_run_sandbox_id() == "run-1"
            assert terminal_tool._resolve_container_task_id("child-agent-99") == "run-1"
    finally:
        destroy_run_sandbox(context.run_id)
    assert current_run_sandbox_id() is None
    assert context.run_id not in terminal_tool._task_env_overrides


def test_destroy_run_sandbox_is_idempotent(monkeypatch):
    from tools import terminal_tool
    from tools.environments import docker as docker_env

    run_id = "run-idempotent"
    calls = []

    def cleanup(task_id, *, force_remove=False):
        calls.append(("cleanup", task_id, force_remove))

    def remove(task_id, *, profile_filter=None, docker_exe=None):
        calls.append(("remove", task_id, profile_filter))
        return 0

    def clear(task_id):
        calls.append(("clear", task_id))

    monkeypatch.setattr(terminal_tool, "cleanup_vm_and_wait", cleanup)
    monkeypatch.setattr(terminal_tool, "clear_task_env_overrides", clear)
    monkeypatch.setattr(docker_env, "_get_active_profile_name", lambda: "cxba-production")
    monkeypatch.setattr(docker_env, "remove_task_containers", remove)
    run_sandbox._destroyed_run_ids.discard(run_id)
    run_sandbox._destroy_locks.pop(run_id, None)
    try:
        destroy_run_sandbox(run_id)
        destroy_run_sandbox(run_id)
    finally:
        run_sandbox._destroyed_run_ids.discard(run_id)
        run_sandbox._destroy_locks.pop(run_id, None)

    assert calls == [
        ("cleanup", run_id, True),
        ("remove", run_id, "cxba-production"),
        ("clear", run_id),
    ]


def test_run_container_config_is_ephemeral_and_uses_only_dynamic_mounts(tmp_path):
    from tools import terminal_tool

    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    try:
        overrides = terminal_tool.resolve_task_overrides(context.run_id)
        config = terminal_tool._container_config_for_task(
            {
                "container_persistent": True,
                "docker_persist_across_processes": True,
                "docker_volumes": ["/unexpected:/unexpected"],
            },
            overrides,
        )
        assert config["container_persistent"] is False
        assert config["docker_persist_across_processes"] is False
        assert config["docker_deny_database_credentials"] is True
        assert config["docker_volumes"] == overrides["docker_volumes"]
        assert all("/unexpected" not in mount for mount in config["docker_volumes"])
    finally:
        destroy_run_sandbox(context.run_id)


def test_large_output_diagnostics_use_controlled_runtime_directory(tmp_path):
    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    try:
        output_dir = workspace_output_directory(context.run_id)
        assert output_dir == (
            tmp_path / ".cxba-runtime" / "run-output" / "case-1" / context.run_id
        )
        spill = output_dir / "out-123.log"
        spill.write_text("complete large output", encoding="utf-8")
        assert workspace_visible_output_path(context.run_id, str(spill)) == (
            "/run-diagnostics/out-123.log"
        )
        assert spill.read_text(encoding="utf-8") == "complete large output"
    finally:
        destroy_run_sandbox(context.run_id)


def test_trusted_run_never_exposes_an_uncontrolled_host_output_path(tmp_path):
    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    try:
        outside = tmp_path / "outside.log"
        outside.write_text("must stay private", encoding="utf-8")
        with pytest.raises(ValueError, match="outside the controlled"):
            workspace_visible_output_path(context.run_id, str(outside))
    finally:
        destroy_run_sandbox(context.run_id)


def test_workspace_symlink_cannot_redirect_large_output(tmp_path):
    raw = _context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "workspace" / ".cxba").symlink_to(outside, target_is_directory=True)
    context = validate_run_context(raw, storage_root=tmp_path)

    register_run_sandbox(context)
    try:
        output_dir = workspace_output_directory(context.run_id)
        spill = output_dir / "safe.log"
        spill.write_text("controlled", encoding="utf-8")
        assert spill.is_file()
        assert list(outside.iterdir()) == []
    finally:
        destroy_run_sandbox(context.run_id)


def test_reserved_runtime_symlink_is_rejected(tmp_path):
    raw = _context(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".cxba-runtime").symlink_to(outside, target_is_directory=True)
    context = validate_run_context(raw, storage_root=tmp_path)

    with pytest.raises(ValueError, match="must not be a symlink"):
        register_run_sandbox(context)


def test_mount_source_replacement_is_rejected_before_docker_creation(tmp_path):
    from tools import terminal_tool

    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    workspace = tmp_path / "workspace"
    original = tmp_path / "workspace-original"
    replacement = tmp_path / "workspace-replacement"
    workspace.rename(original)
    replacement.mkdir()
    workspace.symlink_to(replacement, target_is_directory=True)
    try:
        overrides = terminal_tool.resolve_task_overrides(context.run_id)
        with pytest.raises(ValueError, match="source"):
            terminal_tool._container_config_for_task({}, overrides)
    finally:
        destroy_run_sandbox(context.run_id)


def test_same_run_id_cannot_be_registered_twice(tmp_path):
    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    try:
        with pytest.raises(RuntimeError, match="already registered"):
            register_run_sandbox(context)
    finally:
        destroy_run_sandbox(context.run_id)


def test_destroy_forces_container_removal_without_deleting_mounts(tmp_path, monkeypatch):
    from tools import terminal_tool

    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)
    calls = []
    monkeypatch.setattr(
        terminal_tool,
        "cleanup_vm_and_wait",
        lambda task_id, force_remove=False: calls.append((task_id, force_remove)),
    )
    destroy_run_sandbox(context.run_id)
    assert calls == [("run-1", True)]
    assert (tmp_path / "workspace").is_dir()
    assert (tmp_path / "current").is_dir()


def test_destroy_keeps_registration_when_explicit_cleanup_fails(tmp_path, monkeypatch):
    from tools import terminal_tool

    context = validate_run_context(_context(tmp_path), storage_root=tmp_path)
    register_run_sandbox(context)

    def fail_cleanup(task_id, force_remove=False):
        raise RuntimeError("docker remove failed")

    monkeypatch.setattr(terminal_tool, "cleanup_vm_and_wait", fail_cleanup)
    with pytest.raises(RuntimeError, match="docker remove failed"):
        destroy_run_sandbox(context.run_id)
    assert context.run_id in terminal_tool._task_env_overrides
    terminal_tool.clear_task_env_overrides(context.run_id)


def test_cleanup_vm_and_wait_accepts_missing_forced_container(monkeypatch):
    from tools import terminal_tool

    class MissingContainerEnv:
        _container_id = "already-gone"
        _docker_exe = "/usr/bin/docker"

        def cleanup(self, *, force_remove=False):
            assert force_remove is True

        def wait_for_cleanup_result(self):
            raise RuntimeError("docker rm exited with status 1")

    task_id = "missing-forced-container"

    def inspect_missing(cmd, **kwargs):
        assert cmd == ["/usr/bin/docker", "inspect", "already-gone"]
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="Error response from daemon: No such object: already-gone",
        )

    monkeypatch.setattr(terminal_tool.subprocess, "run", inspect_missing)
    with terminal_tool._env_lock:
        terminal_tool._active_environments[task_id] = MissingContainerEnv()
        terminal_tool._last_activity[task_id] = 1.0
    try:
        terminal_tool.cleanup_vm_and_wait(task_id, force_remove=True)
        assert task_id not in terminal_tool._active_environments
        assert task_id not in terminal_tool._last_activity
    finally:
        with terminal_tool._env_lock:
            terminal_tool._active_environments.pop(task_id, None)
            terminal_tool._last_activity.pop(task_id, None)


def test_tool_heartbeat_becomes_unhealthy_when_active_probe_is_stale(monkeypatch):
    from tools import run_sandbox

    with run_sandbox._registry_lock:
        run_sandbox._tool_process_heartbeats.clear()
    clock = {"now": 100.0}
    monkeypatch.setattr(run_sandbox.time, "time", lambda: clock["now"])

    run_sandbox.record_tool_process_heartbeat("run-heartbeat", True)
    assert run_sandbox.probe_run_heartbeat("run-heartbeat")["tool"] == {
        "active": True,
        "alive": True,
        "observed_at": 100.0,
    }

    clock["now"] += run_sandbox._TOOL_HEARTBEAT_STALE_SECONDS + 1
    stale = run_sandbox.probe_run_heartbeat("run-heartbeat")["tool"]
    assert stale["active"] is True
    assert stale["alive"] is False

    run_sandbox.record_tool_process_heartbeat("run-heartbeat", False)
    completed = run_sandbox.probe_run_heartbeat("run-heartbeat")["tool"]
    assert completed["active"] is False
    assert completed["alive"] is False
    with run_sandbox._registry_lock:
        run_sandbox._tool_process_heartbeats.clear()
