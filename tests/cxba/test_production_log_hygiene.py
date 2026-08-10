from __future__ import annotations

import io
import logging
from types import SimpleNamespace

from agent import tool_executor
from tui_gateway.host_supervisor import HostSupervisor


def _trusted_context(_task_id: str) -> dict[str, str]:
    return {
        "run_id": "run-test",
        "stored_session_id": "stored-test",
        "runtime_session_id": "runtime-test",
    }


def test_cxba_tool_logs_keep_structure_without_tool_content(monkeypatch, caplog):
    sensitive = "SYNTHETIC_ACCOUNT_001 /case/materials/source.csv"
    monkeypatch.setattr(tool_executor, "_cxba_tool_log_context", _trusted_context)

    with caplog.at_level(logging.INFO, logger=tool_executor.__name__):
        tool_executor._log_tool_exception(
            agent=object(),
            effective_task_id="run-test",
            function_name="read_file",
            error=RuntimeError(sensitive),
            source="handle_function_call",
        )
        assert tool_executor._log_tool_outcome(
            agent=object(),
            effective_task_id="run-test",
            function_name="read_file",
            duration=0.25,
            result=f"Error: {sensitive}",
            failed=True,
        )

    assert sensitive not in caplog.text
    assert "event=cxba_tool" in caplog.text
    assert "runId=run-test" in caplog.text
    assert "tool=read_file" in caplog.text
    assert "errorType=RuntimeError" in caplog.text
    assert "resultChars=" in caplog.text


def test_non_cxba_tool_exception_keeps_upstream_diagnostic(monkeypatch, caplog):
    monkeypatch.setattr(tool_executor, "_cxba_tool_log_context", lambda _task_id: None)

    with caplog.at_level(logging.ERROR, logger=tool_executor.__name__):
        tool_executor._log_tool_exception(
            agent=object(),
            effective_task_id="default",
            function_name="terminal",
            error=RuntimeError("ordinary diagnostic"),
            source="handle_function_call",
        )

    assert "ordinary diagnostic" in caplog.text


def test_cxba_compute_host_stderr_is_redacted(caplog, tmp_path):
    supervisor = HostSupervisor(
        registry_path=tmp_path / "registry.json",
        redact_child_stderr=True,
        autostart=False,
    )
    sensitive = "SYNTHETIC_SECRET /case/materials/source.csv"
    proc = SimpleNamespace(stderr=io.StringIO(f"{sensitive}\n"))

    with caplog.at_level(logging.WARNING, logger="tui_gateway.host_supervisor"):
        supervisor._drain_stderr(proc)

    assert sensitive not in caplog.text
    assert sensitive not in " ".join(supervisor._stderr_tail)
    assert supervisor._stderr_tail == [f"[REDACTED child stderr chars={len(sensitive)}]"]
    assert "event=compute_host stage=stderr status=reported" in caplog.text


def test_cxba_compute_host_invalid_stdout_is_redacted(caplog, tmp_path):
    supervisor = HostSupervisor(
        registry_path=tmp_path / "registry.json",
        redact_child_stderr=True,
        autostart=False,
    )
    sensitive = "SYNTHETIC_ACCOUNT_002 /case/materials/output.csv"
    proc = SimpleNamespace(stdout=io.StringIO(f"{sensitive}\n"))

    with caplog.at_level(logging.WARNING, logger="tui_gateway.host_supervisor"):
        supervisor._drain_stdout(proc)

    assert sensitive not in caplog.text
    assert "event=compute_host stage=stdout status=invalid_json" in caplog.text
