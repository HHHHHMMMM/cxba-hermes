"""Trusted per-Run sandbox context for the CXBA private gateway.

The model never constructs this context.  The private Spring gateway supplies
it alongside ``prompt.submit`` and this module validates the host mounts before
registering a one-Run Docker environment.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_registry_lock = threading.Lock()
_destroy_locks: dict[str, threading.Lock] = {}
_destroyed_run_ids: set[str] = set()
_tool_process_heartbeats: dict[str, tuple[bool, float]] = {}
_TOOL_HEARTBEAT_STALE_SECONDS = 15.0
_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cxba_run_sandbox_id", default=None
)


@dataclass(frozen=True)
class RunMount:
    source: str
    target: str
    read_only: bool


@dataclass(frozen=True)
class TrustedRunContext:
    case_id: str
    business_session_id: str
    business_branch_id: str
    run_id: str
    actor_user_id: str
    mounts: tuple[RunMount, ...]
    storage_root: str = ""


def _configured_storage_root() -> Path:
    raw = os.getenv("CXBA_CASE_STORAGE_ROOT", "").strip()
    if not raw:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        gateway_cfg = cfg.get("gateway") or {}
        raw = str(gateway_cfg.get("case_storage_root") or "").strip()
    if not raw:
        raise ValueError(
            "CXBA case storage root is not configured "
            "(set gateway.case_storage_root or CXBA_CASE_STORAGE_ROOT)"
        )
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError("CXBA case storage root must be an absolute path")
    return root.resolve(strict=True)


def _configured_knowledge_root() -> Path:
    raw = os.getenv("CXBA_KNOWLEDGE_VAULT_ROOT", "").strip()
    if not raw:
        raise ValueError("CXBA knowledge Vault root is not configured")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError("CXBA knowledge Vault root must be an absolute path")
    resolved = root.resolve(strict=True)
    if root.is_symlink() or not resolved.is_dir():
        raise ValueError("CXBA knowledge Vault root must be a regular directory")
    return resolved


def _required_identity(raw: dict[str, Any], name: str, *, run_id: bool = False) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"run_context.{name} must be a non-empty string")
    value = value.strip()
    matcher = _RUN_ID_RE if run_id else _IDENTITY_RE
    if not matcher.fullmatch(value) or (run_id and value == "default"):
        raise ValueError(f"run_context.{name} has an invalid value")
    return value


def _normalize_target(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError("run_context mount target must be an absolute POSIX path")
    raw_parts = value.split("/")
    if ".." in raw_parts:
        raise ValueError("run_context mount target must not contain '..'")
    target = str(PurePosixPath(value))
    fixed_targets = {
        "/data", "/workspace", "/exchange/current", "/shared",
        "/case/Memory.md", "/knowledge"
    }
    parts = PurePosixPath(target).parts
    scoped_target = bool(
        len(parts) == 3
        and parts[1] in {"case-sessions", "exchange"}
        and _IDENTITY_RE.fullmatch(parts[2])
    )
    if target not in fixed_targets and not scoped_target:
        raise ValueError(f"run_context mount target is not allowed: {target}")
    return target


def validate_run_context(raw: Any, *, storage_root: Path | None = None) -> TrustedRunContext:
    if not isinstance(raw, dict):
        raise ValueError("run_context must be an object")
    expected = {
        "case_id", "business_session_id", "business_branch_id", "run_id",
        "actor_user_id", "mounts"
    }
    unknown = set(raw) - expected
    if unknown:
        raise ValueError(f"run_context contains unknown fields: {', '.join(sorted(unknown))}")

    case_id = _required_identity(raw, "case_id")
    session_id = _required_identity(raw, "business_session_id")
    branch_id = _required_identity(raw, "business_branch_id")
    run_id = _required_identity(raw, "run_id", run_id=True)
    actor_user_id = _required_identity(raw, "actor_user_id")
    root = (storage_root or _configured_storage_root()).resolve(strict=True)
    reserved_runtime_root = root / ".cxba-runtime"

    mount_values = raw.get("mounts")
    if not isinstance(mount_values, list) or not mount_values:
        raise ValueError("run_context.mounts must be a non-empty array")
    mounts: list[RunMount] = []
    targets: set[str] = set()
    for index, item in enumerate(mount_values):
        if not isinstance(item, dict) or set(item) != {"source", "target", "read_only"}:
            raise ValueError(
                f"run_context.mounts[{index}] must contain only source, target and read_only"
            )
        source_value = item.get("source")
        if not isinstance(source_value, str) or not source_value.startswith("/"):
            raise ValueError(f"run_context.mounts[{index}].source must be absolute")
        if any(char in source_value for char in ("\n", "\r", ":")):
            raise ValueError(f"run_context.mounts[{index}].source contains an invalid character")
        source_path = Path(source_value)
        source = source_path.resolve(strict=True)
        target = _normalize_target(item.get("target"))
        if target == "/knowledge":
            if source_path.is_symlink() or source != _configured_knowledge_root():
                raise ValueError(
                    "run_context /knowledge source must be the configured knowledge Vault root"
                )
        else:
            try:
                source.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"run_context.mounts[{index}].source is outside the configured case storage root"
                ) from exc
        try:
            reserved_runtime_root.relative_to(source)
        except ValueError:
            pass
        else:
            raise ValueError(
                f"run_context.mounts[{index}].source would expose the reserved runtime directory"
            )

        if target in targets:
            raise ValueError(f"run_context contains duplicate mount target: {target}")
        targets.add(target)
        read_only = item.get("read_only")
        if not isinstance(read_only, bool):
            raise ValueError(f"run_context.mounts[{index}].read_only must be boolean")
        writable = target in {"/workspace", "/exchange/current"}
        if read_only == writable:
            required = "writable" if writable else "read-only"
            raise ValueError(f"run_context mount {target} must be {required}")
        if target == "/case/Memory.md":
            expected_memory = root / "cases" / case_id / "Memory.md"
            if (
                Path(source_value) != expected_memory
                or Path(source_value).is_symlink()
                or not source.is_file()
            ):
                raise ValueError(
                    "run_context /case/Memory.md source must be the current case Memory.md regular file"
                )
        mounts.append(RunMount(str(source), target, read_only))

    required_targets = {
        "/data", "/workspace", "/exchange/current", "/shared", "/case/Memory.md"
    }
    missing = required_targets - targets
    if missing:
        raise ValueError(f"run_context is missing required mount targets: {', '.join(sorted(missing))}")
    return TrustedRunContext(
        case_id,
        session_id,
        branch_id,
        run_id,
        actor_user_id,
        tuple(mounts),
        str(root),
    )


def register_run_sandbox(context: TrustedRunContext) -> None:
    from tools import terminal_tool

    output_directory = _run_output_directory(
        context.case_id,
        context.run_id,
        create=True,
        storage_root=Path(context.storage_root) if context.storage_root else None,
    )
    volumes = [
        f"{mount.source}:{mount.target}{':ro' if mount.read_only else ''}"
        for mount in context.mounts
    ]
    volumes.append(f"{output_directory}:/run-diagnostics:ro")
    validate_registered_mount_sources(volumes)
    with _registry_lock:
        _destroyed_run_ids.discard(context.run_id)
        with terminal_tool._env_lock:
            if context.run_id in terminal_tool._active_environments:
                raise RuntimeError(f"run sandbox already exists: {context.run_id}")
            if context.run_id in terminal_tool._task_env_overrides:
                raise RuntimeError(f"run sandbox context already registered: {context.run_id}")
        terminal_tool.register_task_env_overrides(
            context.run_id,
            {
                "run_sandbox": True,
                "env_type": "docker",
                "cwd": "/workspace",
                "docker_volumes": volumes,
                "container_persistent": False,
                "docker_persist_across_processes": False,
                "docker_mount_cwd_to_workspace": False,
                "docker_deny_database_credentials": True,
                "run_output_case_id": context.case_id,
                "run_output_storage_root": context.storage_root,
                "run_output_directory": str(output_directory),
            },
        )


def validate_registered_mount_sources(volumes: Any) -> None:
    """Reject a trusted bind source that was replaced after context validation."""
    if not isinstance(volumes, list):
        raise ValueError("trusted Run volumes must be an array")
    for volume in volumes:
        if not isinstance(volume, str):
            raise ValueError("trusted Run volume must be a string")
        source, separator, _target = volume.partition(":")
        if not separator or not source.startswith("/"):
            raise ValueError("trusted Run volume has an invalid source")
        source_path = Path(source)
        if source_path.is_symlink():
            raise ValueError("trusted Run volume source must not be a symlink")
        resolved = source_path.resolve(strict=True)
        if str(resolved) != source:
            raise ValueError("trusted Run volume source changed after validation")


def destroy_run_sandbox(run_id: str) -> None:
    if not run_id or run_id == "default" or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("refusing to destroy a default or empty sandbox key")
    from tools.terminal_tool import cleanup_vm_and_wait, clear_task_env_overrides

    from tools.environments.docker import _get_active_profile_name, remove_task_containers

    with _registry_lock:
        if run_id in _destroyed_run_ids:
            return
        destroy_lock = _destroy_locks.setdefault(run_id, threading.Lock())
    with destroy_lock:
        with _registry_lock:
            if run_id in _destroyed_run_ids:
                return
        cleanup_vm_and_wait(run_id, force_remove=True)
        remove_task_containers(run_id, profile_filter=_get_active_profile_name())
        clear_task_env_overrides(run_id)
        with _registry_lock:
            _destroyed_run_ids.add(run_id)
            _destroy_locks.pop(run_id, None)
            _tool_process_heartbeats.pop(run_id, None)


@contextlib.contextmanager
def bind_run_sandbox(run_id: str) -> Iterator[None]:
    token = _current_run_id.set(run_id)
    try:
        yield
    finally:
        _current_run_id.reset(token)


def current_run_sandbox_id() -> str | None:
    return _current_run_id.get()


def record_tool_process_heartbeat(run_id: str, alive: bool) -> None:
    if not run_id or run_id == "default":
        return
    with _registry_lock:
        _tool_process_heartbeats[run_id] = (bool(alive), time.time())


def probe_run_heartbeat(run_id: str) -> dict[str, Any]:
    """Inspect the real local Runner, Docker container and foreground process."""
    if not run_id or run_id == "default":
        raise ValueError("a concrete run_id is required")
    from tools import terminal_tool

    with terminal_tool._env_lock:
        env = terminal_tool._active_environments.get(run_id)
    sandbox_alive = False
    container_id = str(getattr(env, "_container_id", "") or "") if env else ""
    if container_id:
        try:
            import subprocess

            inspected = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            sandbox_alive = (
                inspected.returncode == 0 and inspected.stdout.strip().lower() == "true"
            )
        except (OSError, subprocess.SubprocessError):
            sandbox_alive = False
    with _registry_lock:
        tool_state = _tool_process_heartbeats.get(run_id)
    tool_active = bool(tool_state and tool_state[0])
    tool_observed_at = tool_state[1] if tool_state else None
    tool_alive = bool(
        tool_active
        and tool_observed_at is not None
        and time.time() - tool_observed_at <= _TOOL_HEARTBEAT_STALE_SECONDS
    )
    return {
        "runner": {"alive": True},
        "sandbox": {"alive": sandbox_alive, "container_present": bool(container_id)},
        "tool": {
            "active": tool_active,
            "alive": tool_alive,
            "observed_at": tool_observed_at,
        },
    }


def _run_output_directory(
    case_id: str,
    run_id: str,
    *,
    create: bool,
    storage_root: Path | None = None,
) -> Path:
    if not _IDENTITY_RE.fullmatch(case_id) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("case_id and run_id must be safe runtime identifiers")
    root = (storage_root or _configured_storage_root()).resolve(strict=True)
    parts = (root / ".cxba-runtime", root / ".cxba-runtime" / "run-output")
    case_directory = parts[-1] / case_id
    run_directory = case_directory / run_id
    for directory in (*parts, case_directory, run_directory):
        if directory.is_symlink():
            raise ValueError("reserved Run output directory must not be a symlink")
        if create:
            directory.mkdir(exist_ok=True)
        elif not directory.is_dir():
            return run_directory
    resolved_output_root = parts[-1].resolve(strict=True)
    resolved_run_directory = run_directory.resolve(strict=True)
    try:
        resolved_run_directory.relative_to(resolved_output_root)
    except ValueError as exc:
        raise ValueError("Run output path escaped the reserved runtime directory") from exc
    return resolved_run_directory


def purge_run_output(case_id: str, run_id: str) -> None:
    directory = _run_output_directory(case_id, run_id, create=False)
    if directory.is_symlink():
        raise ValueError("reserved Run output directory must not be a symlink")
    if directory.is_dir():
        shutil.rmtree(directory)


def workspace_output_directory(run_id: str) -> Path | None:
    """Return the controlled host diagnostics directory for this trusted Run."""
    from tools import terminal_tool

    with terminal_tool._env_lock:
        override = terminal_tool._task_env_overrides.get(run_id)
    if not isinstance(override, dict) or not override.get("run_sandbox"):
        return None
    case_id = str(override.get("run_output_case_id") or "")
    storage_root = str(override.get("run_output_storage_root") or "")
    configured = str(override.get("run_output_directory") or "")
    expected = _run_output_directory(
        case_id,
        run_id,
        create=True,
        storage_root=Path(storage_root) if storage_root else None,
    )
    if configured != str(expected):
        raise ValueError("registered Run output directory does not match trusted context")
    return expected


def workspace_visible_output_path(run_id: str, host_path: str) -> str:
    directory = workspace_output_directory(run_id)
    if directory is None:
        return host_path
    try:
        relative = Path(host_path).resolve().relative_to(directory.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(
            "trusted Run output path is outside the controlled diagnostics directory"
        ) from exc
    return str(PurePosixPath("/run-diagnostics") / relative)
