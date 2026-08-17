import threading
from types import SimpleNamespace

from tools import terminal_tool
from tui_gateway import server
from tui_gateway.cxba_runtime import fetch_events, get_run, reset_for_tests
from tui_gateway.transport import bind_transport, reset_transport


def _mounts(root):
    mounts = []
    for name, target, read_only in (
        ("data", "/data", True),
        ("workspace", "/workspace", False),
        ("exchange", "/exchange/current", False),
        ("shared", "/shared", True),
    ):
        source = root / name
        source.mkdir()
        mounts.append(
            {"source": str(source), "target": target, "read_only": read_only}
        )
    memory = root / "cases" / "case-1" / "Memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# 案件长期记忆\n", encoding="utf-8")
    mounts.append(
        {"source": str(memory), "target": "/case/Memory.md", "read_only": True}
    )
    return mounts


def test_cxba_prompt_acknowledges_after_event_buffer_but_before_sandbox_start(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    run_id = "run-startup-contract"
    runtime_session_id = "runtime-startup-contract"
    binding = {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": "branch-1",
    }
    mounts = _mounts(tmp_path)
    ready = threading.Event()
    ready.set()
    session = {
        "agent": object(),
        "agent_error": None,
        "agent_ready": ready,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": "stored-startup-contract",
        "cxba_binding": binding,
        "transport": SimpleNamespace(cxba_private_authority=True),
        "last_active": 0.0,
    }
    server._sessions[runtime_session_id] = session
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda *_args: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda *_args: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    deferred_thread = SimpleNamespace(start=lambda: None)
    monkeypatch.setattr(
        server.threading,
        "Thread",
        lambda *args, **kwargs: deferred_thread,
    )
    monkeypatch.setattr(
        "tui_gateway.cxba_runtime._thread.start_new_thread",
        lambda *_args, **_kwargs: None,
    )
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "prompt-startup-contract",
                "method": "prompt.submit",
                "params": {
                    "session_id": runtime_session_id,
                    "text": "分析材料",
                    "run_context": {
                        **binding,
                        "run_id": run_id,
                        "actor_user_id": "user-1",
                        "mounts": mounts,
                    },
                    "case_context": {
                        "case_basic": {
                            "case_id": "case-1",
                            "case_name": "测试案件",
                        },
                        "global_master_links": [],
                        "investigation_mode": "STANDARD",
                        "material_catalog": [],
                    },
                },
            }
        )

        assert response["result"]["status"] == "streaming"
        run = get_run(run_id)
        assert run is not None
        assert run.runtime_session_id == runtime_session_id
        assert run.sandbox_registered is False
        assert fetch_events(run_id, case_id="case-1") == []
    finally:
        reset_transport(token)
        server._sessions.pop(runtime_session_id, None)
        terminal_tool.clear_task_env_overrides(run_id)
        reset_for_tests()


def test_background_continuation_retains_current_run_claim_artifact(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    claim_path = workspace / "evidence-items" / "final-claims.json"
    claim_path.parent.mkdir()
    claim_path.write_text('{"claims":[]}', encoding="utf-8")
    context = SimpleNamespace(run_id="continued-run")
    continued_run = SimpleNamespace(
        context=context,
        case_context={"material_catalog": []},
        runtime_session_id="runtime-continuation",
        status="RUNNING",
        sandbox_registered=True,
    )
    monkeypatch.setattr(
        "tui_gateway.cxba_runtime.attach_run",
        lambda **_kwargs: (continued_run, False),
    )
    prepare_calls = []
    monkeypatch.setattr(
        "tui_gateway.cxba_claims.prepare_claim_delivery",
        lambda run: prepare_calls.append(run),
    )
    session = {
        "session_key": "stored-continuation",
        "active_cxba_run_id": "continued-run",
    }
    monkeypatch.setattr("tui_gateway.cxba_runtime.get_run", lambda _run_id: continued_run)

    result = server._prepare_cxba_run_for_prompt(
        "runtime-continuation",
        session,
        context,
        continued_run.case_context,
    )

    assert result is continued_run
    assert prepare_calls == []
    assert claim_path.read_text(encoding="utf-8") == '{"claims":[]}'


def test_gateway_restart_recovery_retains_existing_run_claim_artifact(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    claim_path = workspace / "evidence-items" / "final-claims.json"
    claim_path.parent.mkdir()
    claim_path.write_text('{"claims":[]}', encoding="utf-8")
    context = SimpleNamespace(run_id="recovered-run")
    recovered_run = SimpleNamespace(
        context=context,
        case_context={"material_catalog": []},
        runtime_session_id="runtime-recovery",
        status="RUNNING",
        sandbox_registered=True,
        events=[{"type": "run.started"}],
    )
    monkeypatch.setattr(
        "tui_gateway.cxba_runtime.attach_run",
        lambda **_kwargs: (recovered_run, True),
    )
    prepare_calls = []
    monkeypatch.setattr(
        "tui_gateway.cxba_claims.prepare_claim_delivery",
        lambda run: prepare_calls.append(run),
    )
    session = {"session_key": "stored-recovery"}

    result = server._prepare_cxba_run_for_prompt(
        "runtime-recovery",
        session,
        context,
        recovered_run.case_context,
    )

    assert result is recovered_run
    assert prepare_calls == []
    assert claim_path.read_text(encoding="utf-8") == '{"claims":[]}'


def test_deferred_cxba_preparation_failure_removes_pre_attached_run_and_sandbox(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    run_id = "run-startup-failure"
    runtime_session_id = "runtime-startup-failure"
    binding = {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": "branch-1",
    }
    mounts = _mounts(tmp_path)
    ready = threading.Event()
    ready.set()
    session = {
        "agent": object(),
        "agent_error": None,
        "agent_ready": ready,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": "stored-startup-failure",
        "cxba_binding": binding,
        "transport": SimpleNamespace(cxba_private_authority=True),
        "last_active": 0.0,
    }
    server._sessions[runtime_session_id] = session
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda *_args: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda *_args: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    deferred_thread = SimpleNamespace(start=lambda: None)
    monkeypatch.setattr(
        server.threading,
        "Thread",
        lambda *args, **kwargs: deferred_thread,
    )
    monkeypatch.setattr(
        "tui_gateway.cxba_runtime._thread.start_new_thread",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "tui_gateway.cxba_claims.prepare_claim_delivery",
        lambda *_args: (_ for _ in ()).throw(ValueError("claim setup failed")),
    )
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "prompt-startup-failure",
                "method": "prompt.submit",
                "params": {
                    "session_id": runtime_session_id,
                    "text": "分析材料",
                    "run_context": {
                        **binding,
                        "run_id": run_id,
                        "actor_user_id": "user-1",
                        "mounts": mounts,
                    },
                    "case_context": {
                        "case_basic": {
                            "case_id": "case-1",
                            "case_name": "测试案件",
                        },
                        "global_master_links": [],
                        "investigation_mode": "STANDARD",
                        "material_catalog": [],
                    },
                },
            }
        )

        assert response["result"]["status"] == "streaming"
        run = get_run(run_id)
        assert run is not None
        assert run.sandbox_registered is False

        try:
            server._prepare_cxba_run_for_prompt(
                runtime_session_id,
                session,
                run.context,
                run.case_context,
                reuse_prepared=True,
            )
        except ValueError as exc:
            assert str(exc) == "claim setup failed"
        else:
            raise AssertionError("claim preparation failure was not propagated")

        assert get_run(run_id).status == "FAILED"
        assert run_id not in terminal_tool._task_env_overrides
    finally:
        reset_transport(token)
        server._sessions.pop(runtime_session_id, None)
        terminal_tool.clear_task_env_overrides(run_id)
        reset_for_tests()
