#!/usr/bin/env python3
"""Normalize a Spring material catalog without assigning scan-order IDs."""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path

from cxba_material_common import (
    MaterialToolError,
    catalog_entries,
    entry_value,
    read_json,
    resolve_below,
    write_json,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    root = args.data_root.resolve(strict=True)
    if not root.is_dir():
        raise MaterialToolError("data-root must be a directory")

    materials = []
    seen_ids: set[str] = set()
    for source in catalog_entries(read_json(args.catalog)):
        material_id = str(entry_value(source, "materialId", "id") or "").strip()
        relative_path = str(entry_value(source, "relativePath", "path") or "").strip()
        if not material_id:
            raise MaterialToolError("Every material requires a Spring materialId")
        if material_id in seen_ids:
            raise MaterialToolError("Spring materialId values must be unique")
        seen_ids.add(material_id)
        path = resolve_below(root, relative_path)
        materials.append(
            {
                "materialId": material_id,
                "relativePath": path.relative_to(root).as_posix(),
                "displayName": entry_value(source, "displayName", "name") or path.name,
                "sizeBytes": path.stat().st_size,
                "extension": path.suffix.lower(),
                "mediaType": entry_value(source, "mediaType", "contentType")
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
                "readStatus": entry_value(source, "readStatus") or "UNREAD",
                "note": entry_value(source, "note", "description"),
            }
        )

    write_json(
        args.output,
        {"dataRoot": str(root), "materialCount": len(materials), "materials": materials},
    )
    print(f"catalog_written materialCount={len(materials)} output={args.output}")


if __name__ == "__main__":
    try:
        main()
    except MaterialToolError as exc:
        raise SystemExit(f"material_catalog_failed: {exc}") from exc
