import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.run_sandbox import RunMount, TrustedRunContext
from tui_gateway.cxba_runtime import (
    add_event,
    attach_run,
    binding_system_context,
    discard_queued_approval_result,
    drain_approval_events,
    fetch_events,
    has_pending_proposals,
    has_undelivered_approval_results,
    observe_tool_result,
    monitor_run_heartbeat_once,
    queue_approval_result,
    reset_for_tests,
    runtime_owns_sandbox,
    validate_run_matches_binding,
    validate_session_binding,
)


@pytest.fixture(autouse=True)
def _configured_case_storage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))


def _binding(branch_id: str = "branch-1") -> dict:
    return {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": branch_id,
        "initial_context": {
            "case_basic": {"name": "测试案件"},
            "global_master_links": [{"type": "ACCOUNT", "id": "account-1"}],
            "material_catalog": [{"material_id": "material-1", "name": "流水.xlsx"}],
        },
    }


def _run(tmp_path: Path, run_id: str = "run-1") -> TrustedRunContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id=run_id,
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )


def test_trusted_binding_has_only_minimum_initial_context() -> None:
    binding = validate_session_binding(_binding())
    context = binding_system_context(binding)

    assert "测试案件" in context
    assert "material-1" in context
    injected = json.loads(context.rsplit("\n", 1)[-1])
    assert set(injected) == {
        "case_id",
        "business_session_id",
        "business_branch_id",
        "case_basic",
        "global_master_links",
        "material_catalog",
    }
    with pytest.raises(ValueError, match="initial_context"):
        validate_session_binding(
            {
                **_binding(),
                "initial_context": {
                    **_binding()["initial_context"],
                    "confirmed_findings": [],
                },
            }
        )


def test_run_must_match_case_session_and_branch_binding(tmp_path: Path) -> None:
    binding = validate_session_binding(_binding())
    validate_run_matches_binding(binding, _run(tmp_path))

    wrong = _run(tmp_path, "run-2")
    wrong = TrustedRunContext(
        case_id=wrong.case_id,
        business_session_id=wrong.business_session_id,
        business_branch_id="model-forged-branch",
        run_id=wrong.run_id,
        actor_user_id=wrong.actor_user_id,
        mounts=wrong.mounts,
    )
    with pytest.raises(ValueError, match="business_branch_id"):
        validate_run_matches_binding(binding, wrong)


def test_reattach_rejects_changed_actor_or_mounts(tmp_path: Path) -> None:
    original = _run(tmp_path)
    attach_run(
        run_context=original,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    changed_actor = TrustedRunContext(
        case_id=original.case_id,
        business_session_id=original.business_session_id,
        business_branch_id=original.business_branch_id,
        run_id=original.run_id,
        actor_user_id="forged-user",
        mounts=original.mounts,
    )
    with pytest.raises(ValueError, match="different trusted session"):
        attach_run(
            run_context=changed_actor,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
        )

    changed_mounts = TrustedRunContext(
        case_id=original.case_id,
        business_session_id=original.business_session_id,
        business_branch_id=original.business_branch_id,
        run_id=original.run_id,
        actor_user_id=original.actor_user_id,
        mounts=(RunMount(str(tmp_path / "workspace"), "/data", True),),
    )
    with pytest.raises(ValueError, match="different trusted session"):
        attach_run(
            run_context=changed_mounts,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
        )


def test_run_events_survive_registry_reset_and_fetch_after_position(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    first = add_event("run-1", "tool.start", {"tool_id": "tool-1"})
    second = add_event("run-1", "tool.complete", {"tool_id": "tool-1"})
    assert first["event_id"] != second["event_id"]
    assert len(first["event_id"]) == 32

    event_path = (
        tmp_path
        / ".cxba-runtime"
        / "run-events"
        / "case-1"
        / "run-1"
        / "events.jsonl"
    )
    rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    assert [row["event_id"] for row in rows] == [
        first["event_id"],
        second["event_id"],
    ]
    assert rows[0]["case_id"] == "case-1"

    reset_for_tests()
    recovered = fetch_events(
        "run-1", after_event_id=first["event_id"], case_id="case-1"
    )
    assert [event["event_id"] for event in recovered] == [second["event_id"]]
    assert recovered[0]["type"] == "tool.complete"


def test_async_proposal_result_is_retained_for_same_run_and_sandbox(tmp_path: Path) -> None:
    run = _run(tmp_path)
    record, _created = attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    proposal_id = observe_tool_result(
        "run-1",
        {"result": '{"proposal_id":"proposal-1","status":"PENDING_APPROVAL"}'},
    )
    assert proposal_id == "proposal-1"
    assert record.pending_proposals == {"proposal-1"}
    assert record.status == "RUNNING"

    queued = queue_approval_result(
        "run-1",
        proposal_id="proposal-1",
        status="EXECUTED",
        content={"affected_rows": 3},
        pending_count=0,
    )
    assert queued["content"] == {"affected_rows": 3}
    assert has_pending_proposals("run-1") is False
    assert has_undelivered_approval_results("run-1") is True
    assert record.status == "RUNNING"
    assert drain_approval_events("run-1") == [queued]
    assert has_undelivered_approval_results("run-1") is False
    assert record.pending_proposals == set()


def test_proposal_tool_event_does_not_mark_running_run_waiting(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path, "run-proposal-event")
    record, _created = attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-proposal-event",
    )
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event_type, _sid, payload=None: emitted.append(
            (event_type, payload or {})
        ),
    )

    server._on_tool_complete(
        "runtime-proposal-event",
        "tool-call-1",
        "cxba_propose_master_data",
        {},
        json.dumps(
            {"proposal_id": "proposal-running", "status": "PENDING_APPROVAL"}
        ),
    )

    assert record.status == "RUNNING"
    assert record.pending_proposals == {"proposal-running"}
    assert ("approval.requested", {
        "proposal_id": "proposal-running",
        "status": "PENDING_APPROVAL",
    }) in emitted
    assert not any(event_type == "run.waiting_approval" for event_type, _ in emitted)


def test_turn_cleanup_cannot_destroy_cxba_run_sandbox_while_approval_continues(
    tmp_path: Path, monkeypatch
) -> None:
    from agent import chat_completion_helpers

    run = _run(tmp_path)
    record, _created = attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    record.sandbox_registered = True
    cleaned: list[str] = []
    monkeypatch.setattr(
        chat_completion_helpers,
        "_ra",
        lambda: SimpleNamespace(
            cleanup_vm=lambda task_id: cleaned.append(task_id),
            cleanup_browser=lambda _task_id: None,
        ),
    )
    agent = SimpleNamespace(verbose_logging=False)

    observe_tool_result(
        "run-1", {"proposal_id": "proposal-1", "status": "PENDING_APPROVAL"}
    )
    chat_completion_helpers.cleanup_task_resources(agent, "run-1")
    assert runtime_owns_sandbox("run-1") is True
    assert cleaned == []

    queue_approval_result(
        "run-1",
        proposal_id="proposal-1",
        status="EXECUTED",
        content={},
        pending_count=0,
    )
    chat_completion_helpers.cleanup_task_resources(agent, "run-1")
    assert record.sandbox_registered is True
    assert cleaned == []


def test_idle_reaper_cannot_destroy_runtime_owned_nonterminal_sandbox(
    tmp_path: Path, monkeypatch
) -> None:
    from tools import terminal_tool

    class FakeEnvironment:
        def __init__(self) -> None:
            self.cleaned = False

        def cleanup(self) -> None:
            self.cleaned = True

    run = _run(tmp_path, "run-idle-owned")
    record, _created = attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    record.sandbox_registered = True
    environment = FakeEnvironment()
    terminal_tool._active_environments[run.run_id] = environment
    terminal_tool._last_activity[run.run_id] = 100.0
    monkeypatch.setattr(terminal_tool.time, "time", lambda: 1_000.0)
    try:
        terminal_tool._cleanup_inactive_envs(lifetime_seconds=300)
        assert terminal_tool._active_environments[run.run_id] is environment
        assert environment.cleaned is False

        from tui_gateway.cxba_runtime import detach_completed_run

        detach_completed_run(run.run_id, "COMPLETED")
        terminal_tool._cleanup_inactive_envs(lifetime_seconds=300)
        assert run.run_id not in terminal_tool._active_environments
        assert environment.cleaned is True
    finally:
        terminal_tool._active_environments.pop(run.run_id, None)
        terminal_tool._last_activity.pop(run.run_id, None)

def test_compression_updates_active_run_mapping_without_changing_business_branch(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path)
    record, _created = attach_run(
        run_context=run,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-1",
    )

    emitted: list[str] = []
    monkeypatch.setattr(server, "_emit", lambda event_type, *_args: emitted.append(event_type))
    server._on_agent_event(
        "runtime-1",
        "session:compress",
        {
            "old_session_id": "stored-parent",
            "session_id": "stored-continuation",
            "in_place": False,
        },
    )

    assert emitted == ["session.mapping_changed", "session:compress"]
    assert record.stored_session_id == "stored-continuation"
    queue_approval_result(
        "run-1",
        proposal_id="proposal-after-compression",
        status="EXECUTED",
        content={"continued": True},
        pending_count=0,
    )
    assert drain_approval_events("run-1")[0]["content"] == {"continued": True}


def test_restart_rejects_changed_actor_and_single_session_rejects_second_run(
    tmp_path: Path,
) -> None:
    first = _run(tmp_path, "run-first")
    attach_run(
        run_context=first,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    second = _run(tmp_path, "run-second")
    with pytest.raises(ValueError, match="different active Run"):
        attach_run(
            run_context=second,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
        )

    reset_for_tests()
    changed_actor = TrustedRunContext(
        case_id=first.case_id,
        business_session_id=first.business_session_id,
        business_branch_id=first.business_branch_id,
        run_id=first.run_id,
        actor_user_id="different-user",
        mounts=first.mounts,
    )
    with pytest.raises(ValueError, match="different trusted session"):
        attach_run(
            run_context=changed_actor,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
        )


def test_terminal_run_cannot_be_reattached(tmp_path: Path) -> None:
    from tui_gateway.cxba_runtime import detach_completed_run

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    detach_completed_run(run.run_id, "COMPLETED")
    with pytest.raises(ValueError, match="terminal Run"):
        attach_run(
            run_context=run,
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
        )


def test_force_stop_intent_keeps_runtime_ownership_until_explicit_destroy(
    tmp_path: Path,
) -> None:
    from tui_gateway.cxba_runtime import detach_completed_run, force_stop_run

    run = _run(tmp_path)
    record, _created = attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    record.sandbox_registered = True
    force_stop_run(run.run_id)
    assert record.status == "FORCE_STOPPING"
    assert runtime_owns_sandbox(run.run_id) is True

    detach_completed_run(run.run_id, "FORCE_STOPPED")
    assert record.status == "FORCE_STOPPED"
    assert runtime_owns_sandbox(run.run_id) is False


def test_late_background_result_is_rejected_after_force_stop(tmp_path: Path) -> None:
    from tui_gateway import server
    from tui_gateway.cxba_runtime import detach_completed_run, force_stop_run

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    force_stop_run(run.run_id)
    detach_completed_run(run.run_id, "FORCE_STOPPED")
    session = {"active_cxba_run_id": None}
    event = {"origin_cxba_run_id": run.run_id, "type": "async_delegation"}
    assert server._cxba_notification_belongs_to_active_run(session, event) is False


def test_event_journal_failure_does_not_emit_untracked_live_frame(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    written: list[dict] = []
    monkeypatch.setattr(server, "write_json", lambda frame: written.append(frame))
    real_open = Path.open

    def fail_event_open(path, *args, **kwargs):
        if path.name == "events.jsonl" and args and args[0] == "a":
            raise OSError("journal unavailable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_event_open)
    with pytest.raises(RuntimeError, match="event journal write failed"):
        server._emit("tool.start", "runtime-1", {"tool": "terminal"})
    assert written == []


def test_cxba_run_lookup_failure_never_falls_back_to_ordinary_session(monkeypatch):
    from tui_gateway import cxba_runtime, server

    def fail_lookup(_runtime_session_id):
        raise RuntimeError("synthetic Run registry failure")

    monkeypatch.setattr(cxba_runtime, "run_for_session", fail_lookup)
    with pytest.raises(RuntimeError, match="synthetic Run registry failure"):
        server._is_cxba_run_session("runtime-1")


def test_material_event_journal_failure_is_not_swallowed(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    server._sessions["runtime-1"] = {
        "cxba_binding": {
            "initial_context": {
                "material_catalog": [
                    {
                        "materialId": "material-1",
                        "relativePath": "material.csv",
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event_type, *_args, **_kwargs: (
            (_ for _ in ()).throw(RuntimeError("event journal write failed"))
            if event_type == "material.access.start"
            else None
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="event journal write failed"):
            server._on_tool_start(
                "runtime-1",
                "tool-1",
                "terminal",
                {"command": "python inspect.py /data/material.csv"},
            )
    finally:
        server._sessions.pop("runtime-1", None)


def test_material_events_resolve_one_trusted_catalog_item_and_real_status(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-material",
    )
    server._sessions["runtime-material"] = {
        "cxba_binding": {
            "initial_context": {
                "material_catalog": [
                    {"materialId": "material-1", "relativePath": "first.csv"},
                    {"materialId": "material-2", "relativePath": "second.csv"},
                ]
            }
        }
    }
    emitted = []
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event_type, _sid, payload=None: emitted.append(
            (event_type, payload or {})
        ),
    )
    args = {"command": "python inspect.py '/data/second.csv'"}
    try:
        server._on_tool_start("runtime-material", "tool-1", "terminal", args)
        server._on_tool_complete(
            "runtime-material", "tool-1", "terminal", args, '{"returncode": 0}'
        )
        server._on_tool_complete(
            "runtime-material",
            "tool-2",
            "terminal",
            args,
            '{"output": "reader failed", "exit_code": 1}',
        )
    finally:
        server._sessions.pop("runtime-material", None)

    material_events = [item for item in emitted if item[0].startswith("material.")]
    assert material_events == [
        (
            "material.access.start",
            {
                "tool_id": "tool-1",
                "name": "terminal",
                "material_id": "material-2",
                "relative_path": "second.csv",
                "status": "READING",
            },
        ),
        (
            "material.access.complete",
            {
                "tool_id": "tool-1",
                "name": "terminal",
                "material_id": "material-2",
                "relative_path": "second.csv",
                "status": "READ",
            },
        ),
        (
            "material.access.failed",
            {
                "tool_id": "tool-2",
                "name": "terminal",
                "material_id": "material-2",
                "relative_path": "second.csv",
                "status": "FAILED",
            },
        ),
    ]


def test_material_events_do_not_guess_unknown_or_ambiguous_paths(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-material-none",
    )
    server._sessions["runtime-material-none"] = {
        "cxba_binding": {
            "initial_context": {
                "material_catalog": [
                    {"materialId": "material-1", "relativePath": "first.csv"},
                    {"materialId": "material-2", "relativePath": "second.csv"},
                ]
            }
        }
    }
    emitted = []
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event_type, _sid, payload=None: emitted.append(event_type),
    )
    try:
        server._on_tool_start(
            "runtime-material-none",
            "tool-unknown",
            "terminal",
            {"command": "cat /data/unknown.csv"},
        )
        server._on_tool_start(
            "runtime-material-none",
            "tool-many",
            "terminal",
            {"command": "cat /data/first.csv /data/second.csv"},
        )
    finally:
        server._sessions.pop("runtime-material-none", None)

    assert not any(event_type.startswith("material.") for event_type in emitted)


def test_file_changed_events_follow_actual_writable_mount_state(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path, "run-file-events")
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-file-events",
    )
    server._sessions["runtime-file-events"] = {}
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event_type, _sid, payload=None: emitted.append(
            (event_type, payload or {})
        ),
    )
    workspace = Path(run.mounts[0].source)
    first = workspace / "results" / "analysis.txt"
    moved = workspace / "results" / "renamed.txt"
    try:
        server._on_tool_start("runtime-file-events", "tool-1", "terminal", {})
        first.parent.mkdir(parents=True)
        first.write_text("created", encoding="utf-8")
        server._on_tool_complete(
            "runtime-file-events", "tool-1", "terminal", {}, '{"exit_code": 0}'
        )

        server._on_tool_start("runtime-file-events", "tool-2", "write_file", {})
        first.write_text("modified and longer", encoding="utf-8")
        server._on_tool_complete(
            "runtime-file-events", "tool-2", "write_file", {}, '{"bytes_written": 19}'
        )

        server._on_tool_start("runtime-file-events", "tool-3", "patch", {})
        first.rename(moved)
        server._on_tool_complete(
            "runtime-file-events", "tool-3", "patch", {}, '{"success": true}'
        )

        server._on_tool_start("runtime-file-events", "tool-4", "execute_code", {})
        moved.unlink()
        server._on_tool_complete(
            "runtime-file-events", "tool-4", "execute_code", {}, '{"success": true}'
        )
    finally:
        server._sessions.pop("runtime-file-events", None)

    assert [payload for event, payload in emitted if event == "file.changed"] == [
        {"path": "/workspace/results/analysis.txt", "change_type": "CREATED"},
        {"path": "/workspace/results/analysis.txt", "change_type": "MODIFIED"},
        {
            "path": "/workspace/results/renamed.txt",
            "change_type": "MOVED",
            "previous_path": "/workspace/results/analysis.txt",
        },
        {"path": "/workspace/results/renamed.txt", "change_type": "DELETED"},
    ]


def test_approval_event_journal_failure_is_not_swallowed(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import server

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event_type, *_args, **_kwargs: (
            (_ for _ in ()).throw(RuntimeError("event journal write failed"))
            if event_type == "approval.requested"
            else None
        ),
    )

    with pytest.raises(RuntimeError, match="event journal write failed"):
        server._on_tool_complete(
            "runtime-1",
            "tool-1",
            "mcp_cxba_propose",
            {},
            json.dumps({"proposal_id": "proposal-1", "status": "PENDING_APPROVAL"}),
        )


def test_failed_approval_delivery_attempt_is_removed_before_explicit_retry(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    observe_tool_result(
        run.run_id,
        {"proposal_id": "proposal-retry", "status": "PENDING_APPROVAL"},
    )

    first = queue_approval_result(
        run.run_id,
        proposal_id="proposal-retry",
        status="EXECUTED",
        content={"affected_rows": 3},
        pending_count=0,
    )
    assert first == {
        "proposal_id": "proposal-retry",
        "status": "EXECUTED",
        "content": {"affected_rows": 3},
        "pending_count": 0,
    }
    discard_queued_approval_result(run.run_id, first)
    assert drain_approval_events(run.run_id) == []
    retried = queue_approval_result(
        run.run_id,
        proposal_id="proposal-retry",
        status="EXECUTED",
        content={"affected_rows": 3},
        pending_count=0,
    )
    assert drain_approval_events(run.run_id) == [retried]


def test_heartbeat_probe_exception_emits_lost_then_real_probe_emits_recovered(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import cxba_runtime, server

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    emitted: list[str] = []
    monkeypatch.setattr(server, "_emit", lambda event_type, *_args: emitted.append(event_type))
    monkeypatch.setattr(
        cxba_runtime,
        "poll_run_heartbeat",
        lambda _run_id: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )

    assert monitor_run_heartbeat_once("run-1") == (True, "heartbeat.lost")
    assert emitted == ["heartbeat.lost"]

    monkeypatch.setattr(
        cxba_runtime,
        "poll_run_heartbeat",
        lambda _run_id: (
            {"runner": {"alive": True}, "sandbox": {"alive": True}},
            *cxba_runtime.record_heartbeat("run-1", healthy=True),
        ),
    )
    assert monitor_run_heartbeat_once("run-1") == (True, "heartbeat.recovered")
    assert emitted == ["heartbeat.lost", "heartbeat.recovered"]


def test_active_tool_heartbeat_loss_and_recovery_changes_run_health(
    tmp_path: Path, monkeypatch
) -> None:
    from tui_gateway import cxba_runtime

    run = _run(tmp_path)
    attach_run(
        run_context=run,
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
    )
    levels = {
        "runner": {"alive": True},
        "sandbox": {"alive": True, "container_present": True},
        "tool": {"active": True, "alive": False, "observed_at": 1.0},
    }
    monkeypatch.setattr(
        "tools.run_sandbox.probe_run_heartbeat",
        lambda _run_id: levels,
    )

    _levels, changed, event_type = cxba_runtime.poll_run_heartbeat(run.run_id)
    assert (changed, event_type) == (True, "heartbeat.lost")

    levels["tool"] = {"active": True, "alive": True, "observed_at": 2.0}
    _levels, changed, event_type = cxba_runtime.poll_run_heartbeat(run.run_id)
    assert (changed, event_type) == (True, "heartbeat.recovered")

    levels["tool"] = {"active": False, "alive": False, "observed_at": 3.0}
    _levels, changed, event_type = cxba_runtime.poll_run_heartbeat(run.run_id)
    assert (changed, event_type) == (False, None)


@pytest.mark.parametrize(
    "damaged_line",
    [
        "not-json\n",
        '{"event_id":"truncated"',
        '["not-an-event-object"]\n',
    ],
)
def test_event_fetch_fails_explicitly_for_damaged_or_truncated_log(
    tmp_path: Path, monkeypatch, damaged_line: str
) -> None:
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    event_dir = (
        tmp_path
        / ".cxba-runtime"
        / "run-events"
        / "case-1"
        / "run-damaged"
    )
    event_dir.mkdir(parents=True)
    event_path = event_dir / "events.jsonl"
    event_path.write_text(
        json.dumps({"event_id": "good-event", "type": "tool.start"})
        + "\n"
        + damaged_line,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Run event log"):
        fetch_events("run-damaged", case_id="case-1")


def test_reserved_event_path_rejects_symlink(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    reserved = tmp_path / ".cxba-runtime"
    reserved.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        attach_run(
            run_context=_run(tmp_path, "run-symlink"),
            stored_session_id="stored-1",
            runtime_session_id="runtime-1",
        )


def test_reserved_event_path_rejects_identifier_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="safe runtime identifiers"):
        fetch_events("../escape", case_id="case-1")


def teardown_function() -> None:
    reset_for_tests()
