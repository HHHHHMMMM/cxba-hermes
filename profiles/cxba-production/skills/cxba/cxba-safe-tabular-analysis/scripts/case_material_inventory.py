#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def detected_format(path: Path) -> str:
    result = subprocess.run(
        ["file", "-b", str(path)], check=False, capture_output=True, text=True
    )
    return result.stdout.strip() or "unknown"


def structural_details(path: Path) -> dict[str, object]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        import openpyxl
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
        try:
            return {"sheets": list(workbook.sheetnames)}
        finally:
            workbook.close()
    if suffix == ".xls":
        import xlrd
        workbook = xlrd.open_workbook(path, on_demand=True)
        try:
            return {"sheets": list(workbook.sheet_names())}
        finally:
            workbook.release_resources()
    if suffix == ".pdf":
        from pypdf import PdfReader
        return {"pages": len(PdfReader(str(path)).pages)}
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve(strict=True)
    output = Path(args.output).resolve()
    if not root.is_dir() or output == root or root in output.parents:
        raise SystemExit("INVALID_PATH_BOUNDARY")
    output.parent.mkdir(parents=True, exist_ok=True)

    records = []
    files = sorted(item for item in root.rglob("*") if item.is_file())
    for index, path in enumerate(files, start=1):
        record: dict[str, object] = {
            "fileId": f"F{index:04d}",
            "path": path.relative_to(root).as_posix(),
            "extension": path.suffix.lower(),
            "sizeBytes": path.stat().st_size,
            "detectedFormat": detected_format(path),
        }
        try:
            record.update(structural_details(path))
        except Exception as error:
            record["structureError"] = f"{type(error).__name__}: {error}"
        records.append(record)

    payload = {"rootLabel": root.name, "fileCount": len(records), "files": records}
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps({"status": "completed", "output": str(output), "fileCount": len(records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
