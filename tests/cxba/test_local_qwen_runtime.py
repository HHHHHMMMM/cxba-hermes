"""Opt-in validation against the deployment's local Qwen OpenAI endpoint."""

import os
import threading
import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_local_qwen_handles_chinese_case_prompt_without_repeating_plan(tmp_path):
    base_url = os.getenv("CXBA_QWEN_BASE_URL", "").strip()
    model = os.getenv("CXBA_QWEN_MODEL", "").strip()
    if not base_url or not model:
        pytest.skip("set CXBA_QWEN_BASE_URL and CXBA_QWEN_MODEL for local model validation")

    from run_agent import AIAgent

    agent = AIAgent(
        api_key=os.getenv("CXBA_QWEN_API_KEY", "local"),
        base_url=base_url,
        model=model,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        enabled_toolsets=[],
    )
    result = agent.run_conversation(
        "请用中文回答：已读取一份账户流水材料，下一步应先核对什么？只给三条简短行动。",
        task_id="cxba-qwen-integration",
    )

    assert result.get("completed") is True
    response = str(result.get("final_response") or "").strip()
    assert response
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    assert len(lines) <= 12
    assert len(lines) == len(dict.fromkeys(lines))


@pytest.mark.integration
def test_local_qwen_real_gateway_controls_and_new_run_recovery(tmp_path, monkeypatch):
    """Exercise the CXBA control plane with a real model and real Docker tools."""
    base_url = os.getenv("CXBA_QWEN_BASE_URL", "").strip()
    model = os.getenv("CXBA_QWEN_MODEL", "").strip()
    if not base_url or not model:
        pytest.skip("set CXBA_QWEN_BASE_URL and CXBA_QWEN_MODEL for local model validation")
    if os.getenv("CXBA_QWEN_CONTROL_E2E", "").strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        pytest.skip("set CXBA_QWEN_CONTROL_E2E=1 for the long real control E2E")

    from hermes_state import SessionDB
    from run_agent import AIAgent
    from tools import terminal_tool
    from tools.run_sandbox import destroy_run_sandbox
    from tui_gateway import cxba_runtime, server
    from tui_gateway.transport import bind_transport, reset_transport

    class CaptureTransport:
        cxba_private_authority = True

        def __init__(self):
            self.frames = []
            self.condition = threading.Condition()

        def write(self, frame):
            with self.condition:
                self.frames.append(frame)
                self.condition.notify_all()
            return True

        def close(self):
            return None

        def wait_event(self, event_type, run_id, *, start=0, timeout=900):
            deadline = time.monotonic() + timeout
            with self.condition:
                while True:
                    for index, frame in enumerate(self.frames[start:], start=start):
                        params = frame.get("params") or {}
                        payload = params.get("payload") or {}
                        if (
                            frame.get("method") == "event"
                            and params.get("type") == event_type
                            and payload.get("run_id") == run_id
                        ):
                            return index, payload
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        seen = [
                            (frame.get("params") or {}).get("type")
                            for frame in self.frames[start:]
                            if frame.get("method") == "event"
                        ]
                        raise AssertionError(
                            f"timed out waiting for {event_type} in {run_id}; seen={seen}"
                        )
                    self.condition.wait(min(remaining, 1.0))

    case_id = "case-qwen-control"
    business_session_id = "business-session-qwen-control"
    business_branch_id = "branch-qwen-control"
    storage_root = tmp_path / "case-storage"
    storage_root.mkdir()
    mount_paths = {}
    for name in ("data", "workspace", "session", "exchange", "shared"):
        path = storage_root / name
        path.mkdir()
        mount_paths[name] = path
    memory = storage_root / "cases" / case_id / "Memory.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("# 案件长期记忆\n", encoding="utf-8")
    mount_paths["memory"] = memory

    binding = {
        "case_id": case_id,
        "business_session_id": business_session_id,
        "business_branch_id": business_branch_id,
    }
    case_context = {
        "case_basic": {"case_id": case_id, "case_name": "本地Qwen控制测试"},
        "global_master_links": [],
        "material_catalog": [],
    }

    def run_context(run_id):
        return {
            "case_id": case_id,
            "business_session_id": business_session_id,
            "business_branch_id": business_branch_id,
            "run_id": run_id,
            "actor_user_id": "user-qwen-control",
            "mounts": [
                {
                    "source": str(mount_paths["data"]),
                    "target": "/data",
                    "read_only": True,
                },
                {
                    "source": str(mount_paths["workspace"]),
                    "target": "/workspace",
                    "read_only": False,
                },
                {
                    "source": str(mount_paths["session"]),
                    "target": f"/case-sessions/{business_session_id}",
                    "read_only": True,
                },
                {
                    "source": str(mount_paths["exchange"]),
                    "target": "/exchange/current",
                    "read_only": False,
                },
                {
                    "source": str(mount_paths["shared"]),
                    "target": "/shared",
                    "read_only": True,
                },
                {
                    "source": str(mount_paths["memory"]),
                    "target": "/case/Memory.md",
                    "read_only": True,
                },
            ],
        }

    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("CXBA_CASE_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv(
        "TERMINAL_DOCKER_IMAGE",
        os.getenv("CXBA_SANDBOX_IMAGE", "cxba-hermes-sandbox:local"),
    )
    monkeypatch.setenv("TERMINAL_DOCKER_NETWORK", "true")
    monkeypatch.setenv("TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE", "false")
    monkeypatch.setenv("TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES", "false")
    monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "false")

    db = SessionDB(db_path=hermes_home / "state.db")
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    transport = CaptureTransport()
    token = bind_transport(transport)
    sid = None
    run_ids = ["run-qwen-steer", "run-qwen-safe", "run-qwen-force", "run-qwen-recovery"]
    try:
        created = server.handle_request(
            {
                "id": "create-qwen-control",
                "method": "session.create",
                "params": {"cxba_context": binding, "source": "api_server"},
            }
        )
        assert "error" not in created
        sid = created["result"]["session_id"]
        stored_session_id = created["result"]["stored_session_id"]
        assert db.get_session(stored_session_id) is not None

        agent = AIAgent(
            api_key=os.getenv("CXBA_QWEN_API_KEY", "local"),
            base_url=base_url,
            model=model,
            session_id=stored_session_id,
            gateway_session_key=stored_session_id,
            session_db=db,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
            enabled_toolsets=["terminal"],
            max_iterations=8,
        )
        for name, callback in server._agent_cbs(sid).items():
            setattr(agent, name, callback)
        session = server._sessions[sid]
        session["agent"] = agent
        session["agent_error"] = None
        session["agent_ready"].set()

        def submit(run_id, text):
            response = server.handle_request(
                {
                    "id": f"submit-{run_id}",
                    "method": "prompt.submit",
                    "params": {
                        "session_id": sid,
                        "text": text,
                        "run_context": run_context(run_id),
                        "case_context": case_context,
                    },
                }
            )
            assert response.get("result", {}).get("status") == "streaming", response

        # Real tool + Steer: steer arrives while the Docker command is active,
        # and the same Qwen turn consumes it after the tool boundary.
        start = len(transport.frames)
        submit(
            run_ids[0],
            "必须先调用terminal工具，command只能是 `sleep 8; printf QWEN_TOOL_OK`。"
            "工具结束后用一句话说明结果。",
        )
        _tool_index, tool_started = transport.wait_event(
            "tool.start", run_ids[0], start=start
        )
        assert tool_started["name"] == "terminal"
        steered = server.handle_request(
            {
                "id": "steer-real-qwen",
                "method": "session.steer",
                "params": {
                    "session_id": sid,
                    "text": "工具完成后最终只回答 STEER_OK。",
                    "case_context": case_context,
                },
            }
        )
        assert steered["result"]["control_outcome"] == "accepted"
        transport.wait_event("tool.complete", run_ids[0], start=start)
        _message_index, steer_message = transport.wait_event(
            "message.complete", run_ids[0], start=start
        )
        assert "STEER_OK" in str(steer_message.get("text") or "")
        transport.wait_event("run.completed", run_ids[0], start=start)

        # Safe stop: the real tool completes, is durably reported, and no next
        # model/tool step starts before the Run reaches SAFE_STOPPED.
        start = len(transport.frames)
        submit(
            run_ids[1],
            "必须调用terminal工具，command只能是 `sleep 8; printf SAFE_TOOL_DONE`。"
            "工具结束后再继续分析。",
        )
        tool_index, _payload = transport.wait_event(
            "tool.start", run_ids[1], start=start
        )
        stopped = server.handle_request(
            {
                "id": "safe-stop-real-qwen",
                "method": "session.safe_stop",
                "params": {"session_id": sid},
            }
        )
        assert stopped["result"]["control_outcome"] == "accepted"
        complete_index, _payload = transport.wait_event(
            "tool.complete", run_ids[1], start=tool_index
        )
        safe_index, _payload = transport.wait_event(
            "safe_stop.completed", run_ids[1], start=complete_index
        )
        assert tool_index < complete_index < safe_index
        assert cxba_runtime.get_run(run_ids[1]).status == "SAFE_STOPPED"

        # Force stop: interrupt a live long command, remove that Run Sandbox,
        # then wait for the real turn thread to release the Session.
        start = len(transport.frames)
        submit(
            run_ids[2],
            "必须调用terminal工具，command只能是 `sleep 60; printf SHOULD_NOT_FINISH`。",
        )
        transport.wait_event("tool.start", run_ids[2], start=start)
        interrupted = server.handle_request(
            {
                "id": "force-stop-real-qwen",
                "method": "session.interrupt",
                "params": {"session_id": sid},
            }
        )
        assert interrupted["result"]["control_outcome"] == "accepted"
        transport.wait_event("force_stop.completed", run_ids[2], start=start)
        assert cxba_runtime.get_run(run_ids[2]).status == "FORCE_STOPPED"
        deadline = time.monotonic() + 120
        while server._sessions[sid].get("running") and time.monotonic() < deadline:
            time.sleep(0.1)
        assert server._sessions[sid].get("running") is False

        # Human recovery is a new Run, never an automatic replay of the
        # force-stopped command.  The same real Qwen Session answers normally.
        start = len(transport.frames)
        submit(run_ids[3], "不要调用任何工具，最终只回答 RECOVERED_OK。")
        _message_index, recovered = transport.wait_event(
            "message.complete", run_ids[3], start=start
        )
        assert "RECOVERED_OK" in str(recovered.get("text") or "")
        transport.wait_event("run.completed", run_ids[3], start=start)
        assert cxba_runtime.get_run(run_ids[3]).status == "COMPLETED"
        assert not any(
            frame.get("params", {}).get("type") == "tool.start"
            and frame.get("params", {}).get("payload", {}).get("run_id") == run_ids[3]
            for frame in transport.frames[start:]
        )
    finally:
        if sid and sid in server._sessions and server._sessions[sid].get("running"):
            server.handle_request(
                {
                    "id": "cleanup-qwen-control",
                    "method": "session.interrupt",
                    "params": {"session_id": sid},
                }
            )
        for run_id in run_ids:
            record = cxba_runtime.get_run(run_id)
            if record is not None and record.sandbox_registered:
                destroy_run_sandbox(run_id)
            terminal_tool.clear_task_env_overrides(run_id)
        if sid:
            server._close_session_by_id(sid, end_reason="integration_test_cleanup")
        cxba_runtime.reset_for_tests()
        reset_transport(token)
        db.close()
