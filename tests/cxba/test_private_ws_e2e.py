import asyncio
import json
import threading

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway import ws as ws_mod


def _session_context():
    return {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": "branch-1",
    }


def _case_context():
    return {
        "case_basic": {"case_id": "case-1", "case_name": "synthetic-case"},
        "global_master_links": [],
        "investigation_mode": "STANDARD",
        "material_catalog": [],
    }


def _run_context(storage_root):
    mounts = []
    for name, target, read_only in (
        ("data", "/data", True),
        ("workspace", "/workspace", False),
        ("exchange", "/exchange/current", False),
        ("shared", "/shared", True),
    ):
        source = storage_root / name
        source.mkdir()
        mounts.append({"source": str(source), "target": target, "read_only": read_only})
    memory = storage_root / "cases" / "case-1" / "Memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# 案件长期记忆\n", encoding="utf-8")
    mounts.append(
        {"source": str(memory), "target": "/case/Memory.md", "read_only": True}
    )
    return {
        "case_id": "case-1",
        "business_session_id": "session-1",
        "business_branch_id": "branch-1",
        "run_id": "run-1",
        "actor_user_id": "user-1",
        "mounts": mounts,
    }


def test_private_websocket_first_submit_forwards_only_validated_run_context(
    tmp_path, monkeypatch
):
    private_token = "t" * 32
    monkeypatch.setenv("CXBA_GATEWAY_PRIVATE_TOKEN", private_token)
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "resolve_skin", lambda: {})
    monkeypatch.setattr(server, "_ensure_skin_watcher", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_wait_agent_for_prompt", lambda *_args: None)
    monkeypatch.setattr(server, "_release_wake_for_transport", lambda *_args: False)
    monkeypatch.setattr(
        server, "_close_sessions_for_transport", lambda *_args, **_kwargs: (0, 0)
    )

    submitted = threading.Event()
    captured = {}

    def capture_submit(_rid, sid, session, text, **kwargs):
        captured.update(
            sid=sid,
            session=session,
            text=text,
            trusted_run_context=kwargs.get("trusted_run_context"),
            trusted_case_context=kwargs.get("trusted_case_context"),
        )
        submitted.set()
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", capture_submit)
    run_context = _run_context(tmp_path)

    class FakeWebSocket:
        headers = {"x-cxba-gateway-token": private_token}
        client = None

        def __init__(self):
            self.received = 0
            self.sent = []

        async def accept(self):
            return None

        async def send_text(self, line):
            self.sent.append(json.loads(line))

        async def receive_text(self):
            self.received += 1
            if self.received == 1:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "create",
                    "method": "session.create",
                    "params": {
                        "source": "api_server",
                        "cxba_context": _session_context(),
                    },
                })
            if self.received == 2:
                create_response = next(
                    item for item in self.sent if item.get("id") == "create"
                )
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "first-submit",
                    "method": "prompt.submit",
                    "params": {
                        "session_id": create_response["result"]["session_id"],
                        "text": "inspect the synthetic material",
                        "run_context": run_context,
                        "case_context": _case_context(),
                    },
                })
            await asyncio.to_thread(submitted.wait, 5)
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            return None

    socket = FakeWebSocket()
    try:
        asyncio.run(ws_mod.handle_ws(socket))

        create_response = next(
            item for item in socket.sent if item.get("id") == "create"
        )
        submit_response = next(
            item for item in socket.sent if item.get("id") == "first-submit"
        )
        assert submit_response["result"]["status"] == "streaming"
        assert submitted.is_set()
        trusted = captured["trusted_run_context"]
        assert trusted.case_id == "case-1"
        assert trusted.business_session_id == "session-1"
        assert trusted.business_branch_id == "branch-1"
        assert trusted.run_id == "run-1"
        assert trusted.actor_user_id == "user-1"
        assert captured["trusted_case_context"] == _case_context()
        assert {mount.target for mount in trusted.mounts} == {
            "/data",
            "/workspace",
            "/exchange/current",
            "/shared",
            "/case/Memory.md",
        }
        runtime_session = server._sessions[create_response["result"]["session_id"]]
        assert captured["session"] is runtime_session
        assert captured["sid"] == create_response["result"]["session_id"]
        assert captured["text"] == "inspect the synthetic material"
        assert runtime_session["cxba_binding"] == _session_context()
        assert (
            db.get_session(create_response["result"]["stored_session_id"]) is not None
        )
    finally:
        for item in socket.sent:
            if item.get("id") == "create" and "result" in item:
                server._sessions.pop(item["result"]["session_id"], None)
        server._live_transports.clear()
        db.close()


def test_private_websocket_recovers_stable_session_for_approval_continuation(
    tmp_path, monkeypatch
):
    from tui_gateway.cxba_runtime import get_run, reset_for_tests

    private_token = "r" * 32
    monkeypatch.setenv("CXBA_GATEWAY_PRIVATE_TOKEN", private_token)
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "resolve_skin", lambda: {})
    monkeypatch.setattr(server, "_ensure_skin_watcher", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_release_wake_for_transport", lambda *_args: False)
    monkeypatch.setattr(
        server, "_close_sessions_for_transport", lambda *_args, **_kwargs: (0, 0)
    )
    monkeypatch.setattr(
        server,
        "_maybe_schedule_auto_continue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("approval recovery must not replay the interrupted turn")
        ),
    )

    submitted = threading.Event()
    captured = {}

    def capture_submit(_rid, sid, session, text, **kwargs):
        captured.update(sid=sid, session=session, text=text, **kwargs)
        submitted.set()
        return True

    monkeypatch.setattr(server, "_run_prompt_submit", capture_submit)
    run_context = _run_context(tmp_path)
    run_context["run_id"] = "run-approval-recovered"

    class FakeWebSocket:
        headers = {"x-cxba-gateway-token": private_token}
        client = None

        def __init__(self):
            self.received = 0
            self.sent = []

        async def accept(self):
            return None

        async def send_text(self, line):
            self.sent.append(json.loads(line))

        async def receive_text(self):
            self.received += 1
            if self.received == 1:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "create",
                    "method": "session.create",
                    "params": {
                        "source": "api_server",
                        "cxba_context": _session_context(),
                    },
                })
            if self.received == 2:
                created = next(item for item in self.sent if item.get("id") == "create")
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "close",
                    "method": "session.close",
                    "params": {"session_id": created["result"]["session_id"]},
                })
            if self.received == 3:
                for _ in range(100):
                    if any(item.get("id") == "close" for item in self.sent):
                        break
                    await asyncio.sleep(0.01)
                created = next(item for item in self.sent if item.get("id") == "create")
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "resume",
                    "method": "session.resume",
                    "params": {
                        "session_id": created["result"]["stored_session_id"],
                        "suppress_auto_continue": True,
                        "cxba_context": _session_context(),
                    },
                })
            if self.received == 4:
                for _ in range(100):
                    if any(item.get("id") == "resume" for item in self.sent):
                        break
                    await asyncio.sleep(0.01)
                resumed = next(item for item in self.sent if item.get("id") == "resume")
                server._sessions[resumed["result"]["session_id"]]["agent"] = object()
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": "approval",
                    "method": "run.approval.resolve",
                    "params": {
                        "session_id": resumed["result"]["session_id"],
                        "run_id": "run-approval-recovered",
                        "source_run_id": "run-failed",
                        "proposal_id": "proposal-1",
                        "status": "REJECTED",
                        "pending_count": 0,
                        "content": {"proposalStatus": "REJECTED"},
                        "run_context": run_context,
                        "case_context": _case_context(),
                    },
                })
            await asyncio.to_thread(submitted.wait, 5)
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            return None

    socket = FakeWebSocket()
    try:
        asyncio.run(ws_mod.handle_ws(socket))

        approval = next(item for item in socket.sent if item.get("id") == "approval")
        assert approval["result"]["status"] == "continuing"
        assert captured["trusted_run_context"].run_id == "run-approval-recovered"
        assert captured["trusted_case_context"] == _case_context()
        assert captured["display_kind"] == "cxba_approval_result"
        run = get_run("run-approval-recovered")
        assert run is not None
        assert run.runtime_session_id == captured["sid"]
    finally:
        resumed = next(
            (item for item in socket.sent if item.get("id") == "resume" and "result" in item),
            None,
        )
        if resumed:
            server._sessions.pop(resumed["result"]["session_id"], None)
        server._live_transports.clear()
        reset_for_tests()
        db.close()
