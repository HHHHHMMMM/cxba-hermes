from __future__ import annotations

import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "profiles"
    / "cxba-production"
    / "skills"
    / "cxba"
    / "cxba-material-profiling"
    / "scripts"
    / "safe_extract_archive.py"
)


def run_extract(data: Path, workspace: Path, archive: Path, *, success: bool = True):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--archive",
            str(archive),
            "--material-id",
            "material-1",
            "--source-root",
            str(data),
            "--workspace",
            str(workspace),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is success, result.stderr
    return result


def test_extracts_to_workspace_with_source_manifest_and_reuses_completed_output(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()
    archive = data / "材料.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as target:
        target.writestr("第一批/流水.csv", "账户,金额\nA,1\n")

    first = json.loads(run_extract(data, workspace, archive).stdout)
    assert first["reused"] is False
    extracted = workspace / "extracted" / "material-1"
    assert (extracted / "第一批" / "流水.csv").read_text(encoding="utf-8") == "账户,金额\nA,1\n"
    manifest = json.loads((extracted / "archive-manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceMaterialId"] == "material-1"
    assert manifest["archivePath"] == "材料.zip"
    assert manifest["members"][0]["archiveMember"] == "第一批/流水.csv"
    assert manifest["members"][0]["extractedPath"] == "extracted/material-1/第一批/流水.csv"

    second = json.loads(run_extract(data, workspace, archive).stdout)
    assert second["reused"] is True


def test_rejects_path_traversal(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()
    archive = data / "escape.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("../escape.txt", "blocked")

    result = run_extract(data, workspace, archive, success=False)
    assert "ARCHIVE_PATH_TRAVERSAL" in result.stderr
    assert not (tmp_path / "escape.txt").exists()


def test_rejects_symbolic_link_member(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()
    archive = data / "symlink.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(link, "../../outside")

    result = run_extract(data, workspace, archive, success=False)
    assert "ARCHIVE_SYMLINK_REJECTED" in result.stderr


def test_rejects_zip_bomb_compression_ratio(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()
    archive = data / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
        target.writestr("zeros.bin", b"0" * (2 * 1024 * 1024))

    result = run_extract(data, workspace, archive, success=False)
    assert "ARCHIVE_COMPRESSION_RATIO_LIMIT_EXCEEDED" in result.stderr


def test_encrypted_archive_requires_password_without_persisting_password_file(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir()
    workspace.mkdir()
    archive = data / "encrypted.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as target:
        target.writestr("record.txt", "content")
    payload = bytearray(archive.read_bytes())
    local = payload.index(b"PK\x03\x04")
    central = payload.index(b"PK\x01\x02")
    payload[local + 6:local + 8] = (1).to_bytes(2, "little")
    payload[central + 8:central + 10] = (1).to_bytes(2, "little")
    archive.write_bytes(payload)

    result = run_extract(data, workspace, archive, success=False)
    assert "ARCHIVE_PASSWORD_REQUIRED" in result.stderr
    source = SCRIPT.read_text(encoding="utf-8")
    assert "--password-file" not in source
    assert 'workspace / ".pip-cache" / "cxba-archive-stage"' in source


def test_router_and_material_skill_require_the_shared_archive_path() -> None:
    skill_root = ROOT / "profiles" / "cxba-production" / "skills" / "cxba"
    router = (skill_root / "cxba-analysis-router" / "SKILL.md").read_text(encoding="utf-8")
    profiling = (skill_root / "cxba-material-profiling" / "SKILL.md").read_text(encoding="utf-8")
    investigator = (skill_root / "cxba-case-investigator" / "SKILL.md").read_text(encoding="utf-8")

    assert "统一安全解压脚本" in router
    assert "safe_extract_archive.py" in profiling
    assert "不得直接调用`unzip`" in profiling
    assert "archive-manifest.json" in profiling
    assert "解压副本及清单属于Workspace" in investigator
