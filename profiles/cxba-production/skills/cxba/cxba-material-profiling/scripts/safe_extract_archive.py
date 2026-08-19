#!/usr/bin/env python3
"""Safely extract one case ZIP from read-only materials into Session Workspace."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


MAX_ENTRIES = 10_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
SAFE_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ArchiveError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def inside(root: Path, candidate: Path, code: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ArchiveError(code)
    return resolved


def normalized_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or any(character in name for character in ("\x00", "\t", "\n", "\r")):
        raise ArchiveError("ARCHIVE_MEMBER_NAME_INVALID")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", name)
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ArchiveError("ARCHIVE_SYMLINK_REJECTED", name)
    return path


def validate_limits(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ENTRIES:
        raise ArchiveError("ARCHIVE_ENTRY_LIMIT_EXCEEDED", str(len(infos)))
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        if info.file_size > MAX_MEMBER_BYTES:
            raise ArchiveError("ARCHIVE_MEMBER_SIZE_LIMIT_EXCEEDED", info.filename)
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ArchiveError("ARCHIVE_TOTAL_SIZE_LIMIT_EXCEEDED", str(total))
        if info.file_size > 0:
            if info.compress_size <= 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise ArchiveError("ARCHIVE_COMPRESSION_RATIO_LIMIT_EXCEEDED", info.filename)


def extract(
    archive_path: Path,
    source_root: Path,
    workspace: Path,
    material_id: str,
) -> dict[str, object]:
    if not SAFE_KEY.fullmatch(material_id):
        raise ArchiveError("MATERIAL_ID_INVALID")
    archive = inside(source_root, archive_path, "ARCHIVE_OUTSIDE_SOURCE_ROOT")
    if not archive.is_file() or archive.suffix.lower() != ".zip":
        raise ArchiveError("ARCHIVE_FILE_INVALID")
    workspace = workspace.resolve(strict=True)
    extracted_root = workspace / "extracted"
    extracted_root.mkdir(parents=True, exist_ok=True)
    output = extracted_root / material_id
    manifest_path = output / "archive-manifest.json"
    if output.exists():
        if not manifest_path.is_file():
            raise ArchiveError("ARCHIVE_OUTPUT_PARTIAL")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("sourceMaterialId") != material_id or manifest.get("archivePath") != archive.relative_to(source_root.resolve()).as_posix():
            raise ArchiveError("ARCHIVE_OUTPUT_SOURCE_MISMATCH")
        return {**manifest, "reused": True}

    # Stage below the existing internal workspace cache boundary. Incremental
    # file events and terminal indexing intentionally exclude .pip-cache, so a
    # killed extraction cannot publish half-written members. The completed tree
    # is atomically renamed into /workspace/extracted afterwards.
    stage_root = workspace / ".pip-cache" / "cxba-archive-stage"
    stage_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{material_id}-", dir=stage_root))
    members: list[dict[str, object]] = []
    seen_targets: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as source:
            infos = source.infolist()
            validate_limits(infos)
            if any(info.flag_bits & 0x1 for info in infos):
                raise ArchiveError("ARCHIVE_PASSWORD_REQUIRED")
            for info in infos:
                relative = normalized_member(info)
                relative_text = relative.as_posix().rstrip("/")
                if not relative_text:
                    continue
                if relative_text in seen_targets:
                    raise ArchiveError("ARCHIVE_DUPLICATE_MEMBER", relative_text)
                seen_targets.add(relative_text)
                target = temporary.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    media_type = "inode/directory"
                    size = 0
                    kind = "DIRECTORY"
                else:
                    with source.open(info) as source_file, target.open("xb") as target_file:
                        shutil.copyfileobj(source_file, target_file, length=1024 * 1024)
                    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    size = target.stat().st_size
                    kind = "FILE"
                members.append({
                    "archiveMember": relative_text,
                    "extractedPath": f"extracted/{material_id}/{relative_text}",
                    "kind": kind,
                    "size": size,
                    "mediaType": media_type,
                })
        manifest = {
            "status": "COMPLETE",
            "sourceMaterialId": material_id,
            "archivePath": archive.relative_to(source_root.resolve()).as_posix(),
            "outputDirectory": f"extracted/{material_id}",
            "entryCount": len(members),
            "fileCount": sum(member["kind"] == "FILE" for member in members),
            "directoryCount": sum(member["kind"] == "DIRECTORY" for member in members),
            "totalBytes": sum(int(member["size"]) for member in members),
            "members": members,
        }
        (temporary / "archive-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
        return {**manifest, "reused": False}
    except zipfile.BadZipFile as error:
        raise ArchiveError("ARCHIVE_FORMAT_INVALID") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--archive", required=True)
    result.add_argument("--material-id", required=True)
    result.add_argument("--source-root", default="/data")
    result.add_argument("--workspace", default="/workspace")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = extract(
            Path(args.archive),
            Path(args.source_root),
            Path(args.workspace),
            args.material_id,
        )
        print(json.dumps({key: value for key, value in result.items() if key != "members"}, ensure_ascii=False))
        return 0
    except (ArchiveError, OSError, json.JSONDecodeError) as error:
        code = error.code if isinstance(error, ArchiveError) else "ARCHIVE_EXTRACTION_FAILED"
        detail = error.detail if isinstance(error, ArchiveError) else error.__class__.__name__
        print(f"{code}{': ' + detail if detail else ''}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
