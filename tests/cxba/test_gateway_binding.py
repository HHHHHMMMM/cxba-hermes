import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_state import SessionDB
from agent.conversation_compression import _compression_child_model_config
from tui_gateway import server
from tui_gateway.transport import bind_transport, reset_transport


def _context(*, case_id="case-1", session_id="session-1", branch_id="branch-1"):
    return {
        "case_id": case_id,
        "business_session_id": session_id,
        "business_branch_id": branch_id,
    }


def _case_context(*, case_id="case-1", material_catalog=None):
    return {
        "case_basic": {"case_id": case_id, "case_name": "测试案件"},
        "global_master_links": [],
        "investigation_mode": "STANDARD",
        "material_catalog": list(material_catalog or []),
    }


def _mounts(root, case_id="case-1"):
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
    memory = root / "cases" / case_id / "Memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# 案件长期记忆\n", encoding="utf-8")
    mounts.append(
        {"source": str(memory), "target": "/case/Memory.md", "read_only": True}
    )
    return mounts


def _live_record(binding):
    ready = threading.Event()
    ready.set()
    return {
        "agent": object(),
        "agent_error": None,
        "agent_ready": ready,
        "history": [{"role": "user", "content": "原消息"}],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": "stored-parent",
        "cxba_binding": binding,
        "transport": SimpleNamespace(cxba_private_authority=True),
    }


def test_trusted_cxba_agent_skips_repository_context_but_keeps_soul(monkeypatch):
    sid = "trusted-cxba-context-policy"
    server._sessions[sid] = {"cxba_binding": _context()}
    fake_runtime = {
        "provider": "custom",
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "no-key-required",
        "api_mode": "chat_completions",
        "command": None,
        "args": None,
        "credential_pool": None,
    }
    try:
        with (
            patch("tui_gateway.server._load_cfg", return_value={"agent": {}}),
            patch("tui_gateway.server._get_db", return_value=MagicMock()),
            patch("tui_gateway.server._resolve_startup_runtime", return_value=("test-model", "custom")),
            patch("tui_gateway.server._resolve_runtime_with_fallback") as resolve,
            patch("tui_gateway.server._load_reasoning_config", return_value=None),
            patch("tui_gateway.server._load_service_tier", return_value=None),
            patch("tui_gateway.server._load_enabled_toolsets", return_value=None),
            patch("run_agent.AIAgent") as agent_type,
        ):
            resolve.return_value = SimpleNamespace(
                runtime=fake_runtime, used_fallback=False, selected_model=None
            )
            server._make_agent(sid, "stored-session", session_id="stored-session")

        kwargs = agent_type.call_args.kwargs
        assert kwargs["skip_context_files"] is True
        assert kwargs["load_soul_identity"] is True
        assert "CXBA trusted case context follows" in kwargs["ephemeral_system_prompt"]
        assert "mounted read-only under /data" in kwargs["ephemeral_system_prompt"]
        assert "/workspace/input/materials.json" in kwargs["ephemeral_system_prompt"]
        assert "cxba-analysis-router" in kwargs["ephemeral_system_prompt"]
        assert "cxba-analysis-notebook" in kwargs["ephemeral_system_prompt"]
        assert "cxba-claim-delivery" in kwargs["ephemeral_system_prompt"]
        assert "person-person" in kwargs["ephemeral_system_prompt"]
        assert "enterprise-enterprise" in kwargs["ephemeral_system_prompt"]
        assert "account-account" in kwargs["ephemeral_system_prompt"]
        assert "cxba_read_dictionaries" in kwargs["ephemeral_system_prompt"]
        assert "cxba_propose_person_person_relations" in kwargs["ephemeral_system_prompt"]
        assert "KINSHIP means relatives" in kwargs["ephemeral_system_prompt"]
        assert "COLLEAGUE means colleagues" in kwargs["ephemeral_system_prompt"]
        assert "shared WORKS_FOR relations do not replace" in kwargs["ephemeral_system_prompt"]
        assert "Never create an enterprise master from its name alone" in kwargs["ephemeral_system_prompt"]
        assert "UNIFIED_SOCIAL_CREDIT_CODE or REGISTRATION_NUMBER only" in kwargs["ephemeral_system_prompt"]
        assert "never invent another" in kwargs["ephemeral_system_prompt"]
        assert "existing approved master IDs" in kwargs["ephemeral_system_prompt"]
    finally:
        server._sessions.pop(sid, None)


def test_private_create_persists_binding_and_resume_rejects_cross_case(
    tmp_path, monkeypatch
):
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    authority = SimpleNamespace(cxba_private_authority=True)
    token = bind_transport(authority)
    sid = None
    try:
        created = server.handle_request(
            {
                "id": "create-1",
                "method": "session.create",
                "params": {"cxba_context": _context(), "source": "cxba-workbench"},
            }
        )
        sid = created["result"]["session_id"]
        session = server._sessions[sid]
        server._ensure_session_db_row(session)
        row = db.get_session(created["result"]["stored_session_id"])
        stored_config = row["model_config"]
        if isinstance(stored_config, str):
            stored_config = json.loads(stored_config)
        assert stored_config["_cxba_binding"] == _context()
    finally:
        reset_transport(token)
        if sid:
            server._sessions.pop(sid, None)

    token = bind_transport(authority)
    try:
        rejected = server.handle_request(
            {
                "id": "resume-1",
                "method": "session.resume",
                "params": {
                    "session_id": row["id"],
                    "cxba_context": _context(case_id="case-2"),
                },
            }
        )
        assert rejected["error"]["code"] == 4036
    finally:
        reset_transport(token)
        db.close()


def test_private_empty_create_survives_close_and_resumes_by_stable_id(
    tmp_path, monkeypatch
):
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    authority = SimpleNamespace(cxba_private_authority=True)
    token = bind_transport(authority)
    resumed_sid = None
    try:
        created = server.handle_request(
            {
                "id": "create-empty",
                "method": "session.create",
                "params": {"cxba_context": _context(), "source": "cxba-workbench"},
            }
        )
        runtime_sid = created["result"]["session_id"]
        stored_sid = created["result"]["stored_session_id"]
        assert db.get_session(stored_sid) is not None
        assert db.get_messages_as_conversation(stored_sid) == []

        closed = server.handle_request(
            {
                "id": "close-empty",
                "method": "session.close",
                "params": {"session_id": runtime_sid},
            }
        )
        assert closed["result"]["closed"] is True

        resumed = server.handle_request(
            {
                "id": "resume-empty",
                "method": "session.resume",
                "params": {
                    "session_id": stored_sid,
                    "cxba_context": _context(),
                },
            }
        )
        assert "error" not in resumed
        assert resumed["result"]["resumed"] == stored_sid
        assert resumed["result"]["session_key"] == stored_sid
        assert resumed["result"]["messages"] == []
        resumed_sid = resumed["result"]["session_id"]
        assert server._sessions[resumed_sid]["source"] == "cxba-workbench"
    finally:
        reset_transport(token)
        if resumed_sid:
            server._sessions.pop(resumed_sid, None)
        db.close()


def test_private_cxba_resume_uses_current_runtime_instead_of_stored_route(
    tmp_path, monkeypatch
):
    db = SessionDB(db_path=tmp_path / "state.db")
    binding = _context()
    db.create_session(
        "stored-stale-route",
        source="api_server",
        model="old-model",
        model_config={
            "_cxba_binding": binding,
            "model": "old-model",
            "provider": "custom:old-endpoint",
            "base_url": "http://127.0.0.1:18080/v1",
            "api_mode": "chat_completions",
        },
    )
    captured = {}
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_set_session_context", lambda _target: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda _tokens: None)

    def make_current_agent(_sid, _key, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model="current-model", provider="custom")

    def init_session(sid, key, agent, history, **_kwargs):
        server._sessions[sid] = {
            "agent": agent,
            "session_key": key,
            "history": history,
            "history_lock": threading.Lock(),
            "created_at": 1.0,
            "running": False,
        }

    monkeypatch.setattr(server, "_make_agent", make_current_agent)
    monkeypatch.setattr(server, "_init_session", init_session)
    monkeypatch.setattr(server, "_transfer_db_to_agent", lambda *_args: None)
    monkeypatch.setattr(
        server,
        "_session_info",
        lambda agent, _session=None: {"model": agent.model, "provider": agent.provider},
    )
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    runtime_sid = None
    try:
        resumed = server.handle_request(
            {
                "id": "resume-current-route",
                "method": "session.resume",
                "params": {
                    "session_id": "stored-stale-route",
                    "eager_build": True,
                    "cxba_context": binding,
                },
            }
        )

        assert "error" not in resumed
        runtime_sid = resumed["result"]["session_id"]
        assert resumed["result"]["info"] == {
            "model": "current-model",
            "provider": "custom",
        }
        for stale_override in (
            "model_override",
            "provider_override",
            "reasoning_config_override",
            "service_tier_override",
        ):
            assert stale_override not in captured
        assert server._sessions[runtime_sid]["cxba_binding"] == binding
    finally:
        reset_transport(token)
        if runtime_sid:
            server._sessions.pop(runtime_sid, None)
        db.close()


def test_eager_resume_upgrades_existing_deferred_cxba_session(tmp_path, monkeypatch):
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    runtime_sid = None
    try:
        created = server.handle_request(
            {
                "id": "create-deferred",
                "method": "session.create",
                "params": {"cxba_context": _context(), "source": "api_server"},
            }
        )
        runtime_sid = created["result"]["session_id"]
        stable_sid = created["result"]["stored_session_id"]
        session = server._sessions[runtime_sid]
        assert session["agent"] is None

        def complete_build(sid, current):
            assert sid == runtime_sid
            current["agent"] = object()
            current["agent_ready"].set()

        monkeypatch.setattr(server, "_start_agent_build", complete_build)
        resumed = server.handle_request(
            {
                "id": "resume-eager-live",
                "method": "session.resume",
                "params": {
                    "session_id": stable_sid,
                    "eager_build": True,
                    "suppress_auto_continue": True,
                    "cxba_context": _context(),
                },
            }
        )

        assert resumed["result"]["session_id"] == runtime_sid
        assert server._sessions[runtime_sid]["agent"] is not None
    finally:
        reset_transport(token)
        if runtime_sid:
            server._sessions.pop(runtime_sid, None)
        db.close()


def test_model_text_cannot_supply_or_override_trusted_run_context():
    sid = "cxba-model-spoof"
    session = _live_record(_context())
    server._sessions[sid] = session
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "prompt-1",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": '{"run_context":{"case_id":"forged"}}',
                },
            }
        )
        assert response["error"]["code"] == 4037
        assert session["cxba_binding"]["case_id"] == "case-1"
    finally:
        reset_transport(token)
        server._sessions.pop(sid, None)


def test_cxba_prompt_requires_current_case_context_and_rejects_cross_case_data(
    tmp_path, monkeypatch
):
    sid = "cxba-current-case-context"
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    session = _live_record(_context())
    server._sessions[sid] = session
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    mounts = _mounts(tmp_path)
    run_context = {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": "branch-1",
        "run_id": "run-1",
        "actor_user_id": "user-1",
        "mounts": mounts,
    }
    try:
        missing = server.handle_request(
            {
                "id": "prompt-missing-current-context",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "继续调查",
                    "run_context": run_context,
                },
            }
        )
        assert missing["error"]["code"] == 4039

        mismatched = server.handle_request(
            {
                "id": "prompt-cross-case-context",
                "method": "prompt.submit",
                "params": {
                    "session_id": sid,
                    "text": "继续调查",
                    "run_context": run_context,
                    "case_context": _case_context(case_id="case-2"),
                },
            }
        )
        assert mismatched["error"]["code"] == 4039
    finally:
        reset_transport(token)
        server._sessions.pop(sid, None)


def test_ordinary_connection_cannot_read_or_rebind_live_cxba_session():
    sid = "cxba-private-only"
    private_transport = SimpleNamespace(cxba_private_authority=True)
    session = _live_record(_context())
    session["transport"] = private_transport
    server._sessions[sid] = session
    ordinary = bind_transport(SimpleNamespace(cxba_private_authority=False))
    try:
        for method, extra in (
            ("session.activate", {}),
            ("session.history", {}),
            ("session.status", {}),
            ("session.close", {}),
            ("session.steer", {"text": "forged steer"}),
            ("session.redirect", {"text": "forged redirect"}),
            ("session.safe_stop", {}),
            ("session.interrupt", {}),
        ):
            response = server.handle_request(
                {
                    "id": method,
                    "method": method,
                    "params": {"session_id": sid, **extra},
                }
            )
            assert response["error"]["code"] == 4035, (method, response)
        assert session["transport"] is private_transport
    finally:
        reset_transport(ordinary)
        server._sessions.pop(sid, None)


def test_private_connection_can_read_live_cxba_history():
    sid = "cxba-private-history"
    session = _live_record(_context())
    session["session_key"] = ""
    server._sessions[sid] = session
    private = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "history",
                "method": "session.history",
                "params": {"session_id": sid},
            }
        )
        assert response["result"]["messages"][0]["text"] == "原消息"
    finally:
        reset_transport(private)
        server._sessions.pop(sid, None)


def test_idle_waiting_run_steer_starts_same_run_readonly_continuation(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import attach_run, mark_waiting_approval, reset_for_tests

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-readonly",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=context,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-readonly",
        case_context=_case_context(),
    )
    mark_waiting_approval(context.run_id)
    session = _live_record(_context())
    session["active_cxba_run_id"] = context.run_id
    server._sessions["runtime-readonly"] = session
    captured = {}

    def start_same_run(_rid, sid, _session, text, **kwargs):
        captured.update(sid=sid, text=text, **kwargs)
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", start_same_run)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    refreshed_case_context = _case_context(
        material_catalog=[
            {"materialId": "new-material", "relativePath": "补充材料.txt"}
        ]
    )
    try:
        response = server.handle_request(
            {
                "id": "readonly-steer",
                "method": "session.steer",
                "params": {
                    "session_id": "runtime-readonly",
                    "text": "继续核对另一份只读材料",
                    "case_context": refreshed_case_context,
                },
            }
        )
        assert response["result"]["status"] == "continuing"
        assert response["result"]["control_outcome"] == "accepted"
        assert captured["trusted_run_context"] is context
        assert captured["trusted_case_context"] == refreshed_case_context
        assert captured["text"] == "继续核对另一份只读材料"
        assert session["running"] is True
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-readonly", None)
        reset_for_tests()


def test_idle_approval_start_failure_can_be_explicitly_retried(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import (
        approval_events_snapshot,
        attach_run,
        observe_tool_result,
        reset_for_tests,
    )

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-approval-retry",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=context,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-approval-retry",
    )
    observe_tool_result(
        context.run_id,
        {"proposal_id": "proposal-1", "status": "PENDING_APPROVAL"},
    )
    session = _live_record(_context())
    session["active_cxba_run_id"] = context.run_id
    server._sessions["runtime-approval-retry"] = session
    start_attempts = []

    def start_on_second_attempt(*_args, **_kwargs):
        start_attempts.append(_kwargs)
        return len(start_attempts) == 2

    monkeypatch.setattr(server, "_run_prompt_submit", start_on_second_attempt)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "approval-retry",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-approval-retry",
                    "run_id": context.run_id,
                    "proposal_id": "proposal-1",
                    "status": "EXECUTED",
                    "pending_count": 0,
                    "content": {"affected_rows": 1},
                },
            }
        )
        assert response["error"]["code"] == 5031
        assert approval_events_snapshot(context.run_id) == []
        assert session["running"] is False

        retried = server.handle_request(
            {
                "id": "approval-retry-2",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-approval-retry",
                    "run_id": context.run_id,
                    "proposal_id": "proposal-1",
                    "status": "EXECUTED",
                    "pending_count": 0,
                    "content": {"affected_rows": 1},
                },
            }
        )
        assert retried["result"]["status"] == "continuing"
        queued = approval_events_snapshot(context.run_id)
        assert len(queued) == 1
        assert queued[0]["proposal_id"] == "proposal-1"
        assert len(start_attempts) == 2
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-approval-retry", None)
        reset_for_tests()


def test_multiple_approval_results_resume_idle_run_once_after_all_are_resolved(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import (
        approval_events_snapshot,
        attach_run,
        observe_tool_result,
        reset_for_tests,
    )

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-multiple-approvals",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=context,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-multiple-approvals",
    )
    for proposal_id in ("proposal-1", "proposal-2"):
        observe_tool_result(
            context.run_id,
            {"proposal_id": proposal_id, "status": "PENDING_APPROVAL"},
        )
    session = _live_record(_context())
    session["active_cxba_run_id"] = context.run_id
    server._sessions["runtime-multiple-approvals"] = session
    submissions = []

    def capture_submit(_rid, _sid, _session, text, **kwargs):
        submissions.append({"text": text, **kwargs})
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", capture_submit)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        first = server.handle_request(
            {
                "id": "approval-multiple-1",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-multiple-approvals",
                    "run_id": context.run_id,
                    "proposal_id": "proposal-1",
                    "status": "EXECUTED",
                    "pending_count": 1,
                    "content": {"affected_rows": 1},
                },
            }
        )

        assert first["result"]["status"] == "queued"
        assert first["result"]["pending_count"] == 1
        assert submissions == []
        assert session["running"] is False
        assert [
            item["proposal_id"] for item in approval_events_snapshot(context.run_id)
        ] == ["proposal-1"]

        second = server.handle_request(
            {
                "id": "approval-multiple-2",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-multiple-approvals",
                    "run_id": context.run_id,
                    "proposal_id": "proposal-2",
                    "status": "REJECTED",
                    "pending_count": 0,
                    "content": {"proposalStatus": "REJECTED"},
                },
            }
        )

        assert second["result"]["status"] == "continuing"
        assert len(submissions) == 1
        assert '"proposal_id": "proposal-1"' in submissions[0]["text"]
        assert '"proposal_id": "proposal-2"' in submissions[0]["text"]
        assert submissions[0]["consumes_cxba_approvals"] is True
        assert session["running"] is True
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-multiple-approvals", None)
        reset_for_tests()


def test_recovered_approval_attaches_new_run_without_replaying_old_tools(
    tmp_path, monkeypatch
):
    from tui_gateway.cxba_runtime import approval_events_snapshot, get_run, reset_for_tests

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    mounts = _mounts(tmp_path)
    run_context = {
        **_context(),
        "run_id": "run-recovered-approval",
        "actor_user_id": "user-2",
        "mounts": mounts,
    }
    session = _live_record(_context())
    server._sessions["runtime-recovered-approval"] = session
    captured = {}

    def capture_submit(_rid, _sid, _session, text, **kwargs):
        captured.update(text=text, **kwargs)
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", capture_submit)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "approval-recovered",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-recovered-approval",
                    "run_id": "run-recovered-approval",
                    "source_run_id": "run-failed",
                    "proposal_id": "proposal-1",
                    "status": "REJECTED",
                    "pending_count": 0,
                    "content": {"proposalStatus": "REJECTED"},
                    "run_context": run_context,
                    "case_context": _case_context(),
                },
            }
        )

        assert response["result"]["status"] == "continuing"
        run = get_run("run-recovered-approval")
        assert run is not None
        assert run.runtime_session_id == "runtime-recovered-approval"
        assert run.case_context == _case_context()
        assert captured["trusted_run_context"].run_id == "run-recovered-approval"
        assert captured["trusted_case_context"] == _case_context()
        assert captured["display_kind"] == "cxba_approval_result"
        assert "without replaying prior tools" in captured["text"]
        queued = approval_events_snapshot("run-recovered-approval")
        assert queued[0]["source_run_id"] == "run-failed"
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-recovered-approval", None)
        reset_for_tests()


def test_recovered_approval_start_failure_detaches_new_run(tmp_path, monkeypatch):
    from tui_gateway.cxba_runtime import get_run, reset_for_tests, run_for_session

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    mounts = _mounts(tmp_path)
    run_context = {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": "branch-1",
        "run_id": "run-recovered-failed-start",
        "actor_user_id": "user-2",
        "mounts": mounts,
    }
    session = _live_record(_context())
    server._sessions["runtime-recovered-failed-start"] = session
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "approval-recovered-failed-start",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-recovered-failed-start",
                    "run_id": "run-recovered-failed-start",
                    "source_run_id": "run-failed",
                    "proposal_id": "proposal-1",
                    "status": "REJECTED",
                    "pending_count": 0,
                    "content": {},
                    "run_context": run_context,
                    "case_context": _case_context(),
                },
            }
        )

        assert response["error"]["code"] == 5031
        assert get_run("run-recovered-failed-start").status == "FAILED"
        assert run_for_session("runtime-recovered-failed-start") is None
        assert session["running"] is False
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-recovered-failed-start", None)
        reset_for_tests()


def test_active_approval_steer_failure_can_be_explicitly_retried(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import (
        approval_events_snapshot,
        attach_run,
        observe_tool_result,
        reset_for_tests,
    )

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-active-approval-retry",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=context,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-active-approval-retry",
    )
    observe_tool_result(
        context.run_id,
        {"proposal_id": "proposal-1", "status": "PENDING_APPROVAL"},
    )

    class RetrySteerAgent:
        def __init__(self):
            self.attempts = 0

        def steer(self, _prompt):
            self.attempts += 1
            return self.attempts == 2

    session = _live_record(_context())
    session["running"] = True
    session["agent"] = RetrySteerAgent()
    session["active_cxba_run_id"] = context.run_id
    server._sessions["runtime-active-approval-retry"] = session
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    request = {
        "method": "run.approval.resolve",
        "params": {
            "session_id": "runtime-active-approval-retry",
            "run_id": context.run_id,
            "proposal_id": "proposal-1",
            "status": "EXECUTED",
            "pending_count": 0,
            "content": {"affected_rows": 1},
        },
    }
    try:
        failed = server.handle_request({"id": "active-retry-1", **request})
        assert failed["error"]["code"] == 4091
        assert approval_events_snapshot(context.run_id) == []

        retried = server.handle_request({"id": "active-retry-2", **request})
        assert retried["result"]["status"] == "queued"
        assert session["agent"].attempts == 2
        assert len(approval_events_snapshot(context.run_id)) == 1
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-active-approval-retry", None)
        reset_for_tests()


def test_multiple_approval_results_steer_active_run_once_after_all_are_resolved(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import attach_run, observe_tool_result, reset_for_tests

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-active-multiple-approvals",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=context,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-active-multiple-approvals",
    )
    for proposal_id in ("proposal-1", "proposal-2"):
        observe_tool_result(
            context.run_id,
            {"proposal_id": proposal_id, "status": "PENDING_APPROVAL"},
        )

    class CapturingAgent:
        def __init__(self):
            self.prompts = []

        def steer(self, prompt):
            self.prompts.append(prompt)
            return True

    session = _live_record(_context())
    session["running"] = True
    session["agent"] = CapturingAgent()
    session["active_cxba_run_id"] = context.run_id
    server._sessions["runtime-active-multiple-approvals"] = session
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        first = server.handle_request(
            {
                "id": "active-multiple-1",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-active-multiple-approvals",
                    "run_id": context.run_id,
                    "proposal_id": "proposal-1",
                    "status": "EXECUTED",
                    "pending_count": 1,
                    "content": {"affected_rows": 1},
                },
            }
        )
        assert first["result"]["status"] == "queued"
        assert session["agent"].prompts == []

        second = server.handle_request(
            {
                "id": "active-multiple-2",
                "method": "run.approval.resolve",
                "params": {
                    "session_id": "runtime-active-multiple-approvals",
                    "run_id": context.run_id,
                    "proposal_id": "proposal-2",
                    "status": "EXECUTED",
                    "pending_count": 0,
                    "content": {"affected_rows": 2},
                },
            }
        )
        assert second["result"]["status"] == "queued"
        assert len(session["agent"].prompts) == 1
        assert '"proposal_id": "proposal-1"' in session["agent"].prompts[0]
        assert '"proposal_id": "proposal-2"' in session["agent"].prompts[0]
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-active-multiple-approvals", None)
        reset_for_tests()


def test_cxba_branch_requires_new_unique_branch_context():
    parent_sid = "cxba-parent"
    duplicate_sid = "cxba-existing-branch"
    parent_binding = _context(branch_id="branch-1")
    server._sessions[parent_sid] = _live_record(parent_binding)
    server._sessions[duplicate_sid] = _live_record(_context(branch_id="branch-2"))
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        missing = server.handle_request(
            {
                "id": "branch-missing",
                "method": "session.branch",
                "params": {"session_id": parent_sid, "target_message_index": 0},
            }
        )
        assert missing["error"]["code"] == 4034

        duplicate = server.handle_request(
            {
                "id": "branch-duplicate",
                "method": "session.branch",
                "params": {
                    "session_id": parent_sid,
                    "target_message_index": 0,
                    "edited_content": "修改后消息",
                    "cxba_context": _context(branch_id="branch-2"),
                },
            }
        )
        assert duplicate["error"]["code"] == 4092
    finally:
        reset_transport(token)
        server._sessions.pop(parent_sid, None)
        server._sessions.pop(duplicate_sid, None)


def test_compression_child_inherits_trusted_binding_from_parent_row(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    binding = _context()
    db.create_session(
        session_id="stored-parent",
        source="api_server",
        model="test-model",
        model_config={"max_iterations": 10, "_cxba_binding": binding},
    )
    agent = SimpleNamespace(
        _session_db=db,
        _session_init_model_config={"max_iterations": 10},
    )
    try:
        child_config = _compression_child_model_config(agent, "stored-parent")
        assert child_config["_cxba_binding"] == binding
        assert child_config["max_iterations"] == 10
    finally:
        db.close()


def test_branch_uses_stable_row_id_after_compression_and_returns_child_row_id(
    tmp_path, monkeypatch
):
    db = SessionDB(db_path=tmp_path / "state.db")
    binding = _context(branch_id="branch-1")
    db.create_session(
        "stored-parent",
        source="api_server",
        model="test-model",
        model_config={"_cxba_binding": binding},
        cwd=str(tmp_path),
    )
    db.set_session_title("stored-parent", "parent")
    db.append_message("stored-parent", "user", "inspect")
    db.append_message(
        "stored-parent",
        "assistant",
        "",
        tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "terminal", "arguments": '{"command":"pwd"}'},
            }
        ],
    )
    db.append_message(
        "stored-parent",
        "tool",
        "workspace",
        tool_name="terminal",
        tool_call_id="call-1",
    )
    target_row_id = db.append_message(
        "stored-parent",
        "assistant",
        "old answer",
        api_content="provider-only content",
    )
    db.archive_and_compact(
        "stored-parent",
        [{"role": "user", "content": "compressed summary"}],
    )

    parent = _live_record(binding)
    parent["session_key"] = "stored-parent"
    parent["history"] = [{"role": "user", "content": "compressed summary"}]
    parent["agent"] = SimpleNamespace(model="test-model", session_id="stored-parent")
    server._sessions["runtime-parent"] = parent
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    monkeypatch.setattr(server, "_session_cwd", lambda _session: str(tmp_path))
    monkeypatch.setattr(server, "_register_session_cwd", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_attach_worker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(server, "_set_session_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server, "_clear_session_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_transfer_db_to_agent", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(server, "_session_info", lambda *_args, **_kwargs: {})

    def make_agent(*_args, **kwargs):
        return SimpleNamespace(
            model="test-model",
            session_id=kwargs.get("session_id"),
            _session_db=kwargs.get("session_db"),
        )

    def init_session(sid, key, agent, history, **kwargs):
        server._sessions[sid] = {
            "agent": agent,
            "history": list(history),
            "history_lock": threading.Lock(),
            "running": False,
            "session_key": key,
            "source": kwargs.get("source") or "api_server",
            "cwd": str(tmp_path),
            "created_at": 1.0,
            "last_active": 1.0,
            "cols": 80,
            "transport": SimpleNamespace(cxba_private_authority=True),
        }

    monkeypatch.setattr(server, "_make_agent", make_agent)
    monkeypatch.setattr(server, "_init_session", init_session)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        first = server.handle_request(
            {
                "id": "branch-row-id",
                "method": "session.branch",
                "params": {
                    "session_id": "runtime-parent",
                    "target_message_row_id": target_row_id,
                    "edited_content": "new answer",
                    "cxba_context": _context(branch_id="branch-2"),
                },
            }
        )
        assert "result" in first, first
        child_key = first["result"]["stored_session_id"]
        child_row_id = first["result"]["edited_message_row_id"]
        assert child_row_id != target_row_id
        assert first["result"]["messages"][-1]["row_id"] == child_row_id
        child_history = db.get_messages_as_conversation(
            child_key, include_row_ids=True
        )
        assert child_history[1]["tool_calls"][0]["function"]["name"] == "terminal"
        assert child_history[-1]["content"] == "new answer"
        assert child_history[-1].get("api_content") is None

        second = server.handle_request(
            {
                "id": "branch-child-row-id",
                "method": "session.branch",
                "params": {
                    "session_id": first["result"]["session_id"],
                    "target_message_row_id": child_row_id,
                    "edited_content": "newer answer",
                    "cxba_context": _context(branch_id="branch-3"),
                },
            }
        )
        assert "result" in second, second
        assert second["result"]["edited_message_row_id"] != child_row_id
    finally:
        reset_transport(token)
        server._sessions.clear()
        db.close()


def test_private_retire_preserves_messages_and_cleans_only_runtime_state(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import (
        add_event,
        attach_run,
        detach_completed_run,
        reset_for_tests,
    )
    from tui_gateway.turn_marker import read_turn_marker, record_turn_start

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)

    parent_binding = _context(branch_id="branch-1")
    child_binding = _context(branch_id="branch-2")
    db.create_session(
        "stored-parent",
        source="api_server",
        model="test-model",
        model_config={"_cxba_binding": parent_binding},
    )
    db.append_message("stored-parent", "user", "请检查材料")
    db.append_message("stored-parent", "assistant", "已完成初步检查")
    db.create_session(
        "stored-child",
        source="api_server",
        model="test-model",
        model_config={
            "_cxba_binding": child_binding,
            "_branched_from": "stored-parent",
        },
        parent_session_id="stored-parent",
    )
    db.append_message("stored-child", "user", "继续核对")
    db.append_message("stored-child", "assistant", "已继续核对")
    messages_before = {
        session_id: db.get_messages_as_conversation(session_id)
        for session_id in ("stored-parent", "stored-child")
    }
    for session_id in messages_before:
        record_turn_start(tmp_path, session_id, "未完成的继续运行标记")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-retired",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=run_context,
        stored_session_id="stored-parent",
        runtime_session_id="runtime-retired",
    )
    add_event("run-retired", "tool.complete", {"tool_id": "tool-1"})
    detach_completed_run("run-retired", "COMPLETED")
    run_dir = (
        tmp_path
        / ".cxba-runtime"
        / "run-events"
        / "case-1"
        / "run-retired"
    )
    assert run_dir.is_dir()
    run_output_dir = (
        tmp_path
        / ".cxba-runtime"
        / "run-output"
        / "case-1"
        / "run-retired"
    )
    run_output_dir.mkdir(parents=True)
    (run_output_dir / "tool-result.txt").write_text(
        "temporary diagnostic", encoding="utf-8"
    )

    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        retired = server.handle_request(
            {
                "id": "retire-1",
                "method": "cxba.business_session.retire",
                "params": {
                    "case_id": "case-1",
                    "business_session_id": "session-1",
                    "reason": "CASE_CLOSED",
                },
            }
        )
        result = retired["result"]
        assert result["status"] == "retired"
        assert result["messages_preserved"] is True
        assert result["already_retired"] is False
        assert set(result["stable_session_ids"]) == {
            "stored-parent",
            "stored-child",
        }
        assert result["retired_run_ids"] == ["run-retired"]

        for session_id, expected_messages in messages_before.items():
            assert db.get_messages_as_conversation(session_id) == expected_messages
            assert (
                db.get_session_model_config_value(
                    session_id, "_cxba_retired", False
                )
                is True
            )
            assert read_turn_marker(tmp_path, session_id) is None
        assert not run_dir.exists()
        assert not run_output_dir.exists()

        resume = server.handle_request(
            {
                "id": "resume-retired",
                "method": "session.resume",
                "params": {"session_id": "stored-parent"},
            }
        )
        assert resume["error"]["code"] == 4100

        repeated = server.handle_request(
            {
                "id": "retire-2",
                "method": "cxba.business_session.retire",
                "params": {
                    "case_id": "case-1",
                    "business_session_id": "session-1",
                    "reason": "CASE_CLOSED",
                },
            }
        )
        assert repeated["result"]["already_retired"] is True
        assert repeated["result"]["retired_run_ids"] == []
    finally:
        reset_transport(token)
        reset_for_tests()
        db.close()


def test_private_retire_rejects_nonterminal_run_without_partial_cleanup(
    tmp_path, monkeypatch
):
    from tools.run_sandbox import RunMount, TrustedRunContext
    from tui_gateway.cxba_runtime import add_event, attach_run, reset_for_tests

    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    db.create_session(
        "stored-active",
        source="api_server",
        model="test-model",
        model_config={"_cxba_binding": _context()},
    )
    db.append_message("stored-active", "user", "正在处理")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-active",
        actor_user_id="user-1",
        mounts=(RunMount(str(workspace), "/workspace", False),),
    )
    attach_run(
        run_context=run_context,
        stored_session_id="stored-active",
        runtime_session_id="runtime-active",
    )
    add_event("run-active", "tool.start", {"tool_id": "tool-1"})
    run_dir = (
        tmp_path
        / ".cxba-runtime"
        / "run-events"
        / "case-1"
        / "run-active"
    )

    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "retire-active",
                "method": "cxba.business_session.retire",
                "params": {
                    "case_id": "case-1",
                    "business_session_id": "session-1",
                    "reason": "ARCHIVED_SESSION_DELETED",
                },
            }
        )
        assert response["error"]["code"] == 4093
        assert (
            db.get_session_model_config_value(
                "stored-active", "_cxba_retired", False
            )
            is False
        )
        assert run_dir.is_dir()
        assert db.get_messages_as_conversation("stored-active")[0]["content"] == "正在处理"
    finally:
        reset_transport(token)
        reset_for_tests()
        db.close()


def test_retire_claim_wins_against_prompt_that_already_read_live_session(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    binding = _context()
    db.create_session(
        "stored-race",
        source="api_server",
        model="test-model",
        model_config={"_cxba_binding": binding},
    )
    db.append_message("stored-race", "user", "原对话")
    live = _live_record(binding)
    live["session_key"] = "stored-race"
    server._sessions["runtime-race"] = live
    monkeypatch.setattr(server, "_teardown_popped_session", lambda *_args, **_kwargs: True)

    original_sess_nowait = server._sess_nowait
    session_read = threading.Event()
    continue_prompt = threading.Event()

    def paused_sess_nowait(params, rid):
        result = original_sess_nowait(params, rid)
        session_read.set()
        assert continue_prompt.wait(timeout=5)
        return result

    monkeypatch.setattr(server, "_sess_nowait", paused_sess_nowait)
    prompt_result = {}

    def submit_prompt():
        token = bind_transport(SimpleNamespace(cxba_private_authority=True))
        try:
            prompt_result.update(
                server.handle_request(
                    {
                        "id": "prompt-race",
                        "method": "prompt.submit",
                        "params": {
                            "session_id": "runtime-race",
                            "text": "继续分析",
                            "run_context": {
                                "case_id": "case-1",
                                "business_session_id": "session-1",
                                "business_branch_id": "branch-1",
                                "run_id": "run-race",
                                "actor_user_id": "user-1",
                                "mounts": [],
                            },
                        },
                    }
                )
            )
        finally:
            reset_transport(token)

    thread = threading.Thread(target=submit_prompt)
    thread.start()
    assert session_read.wait(timeout=5)
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        retired = server.handle_request(
            {
                "id": "retire-race",
                "method": "cxba.business_session.retire",
                "params": {
                    "case_id": "case-1",
                    "business_session_id": "session-1",
                    "reason": "ARCHIVED_SESSION_DELETED",
                },
            }
        )
        assert retired["result"]["status"] == "retired"
    finally:
        reset_transport(token)
        continue_prompt.set()
        thread.join(timeout=5)
        server._sessions.pop("runtime-race", None)
        db.close()

    assert not thread.is_alive()
    assert prompt_result["error"]["code"] == 4100
    assert live["_cxba_retiring"] is True


def test_running_prompt_claim_prevents_retire_without_marking_session(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(server, "_profile_home", lambda _profile: None)
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    binding = _context()
    db.create_session(
        "stored-running",
        source="api_server",
        model="test-model",
        model_config={"_cxba_binding": binding},
    )
    live = _live_record(binding)
    live["running"] = True
    live["session_key"] = "stored-running"
    server._sessions["runtime-running"] = live
    token = bind_transport(SimpleNamespace(cxba_private_authority=True))
    try:
        response = server.handle_request(
            {
                "id": "retire-running",
                "method": "cxba.business_session.retire",
                "params": {
                    "case_id": "case-1",
                    "business_session_id": "session-1",
                    "reason": "CASE_CLOSED",
                },
            }
        )
        assert response["error"]["code"] == 4093
        assert "_cxba_retiring" not in live
        assert (
            db.get_session_model_config_value(
                "stored-running", "_cxba_retired", False
            )
            is False
        )
    finally:
        reset_transport(token)
        server._sessions.pop("runtime-running", None)
        db.close()
