#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path


def read_json(path: str) -> dict:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--tabular", required=True)
    parser.add_argument("--documents", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    inventory = read_json(args.inventory)
    tabular = read_json(args.tabular)
    documents = read_json(args.documents)
    tabular_by_path = {item["file"]: item for item in tabular["files"]}
    documents_by_path = {item["file"]: item for item in documents["files"]}

    materials = []
    for item in inventory["files"]:
        path = item["path"]
        material = {
            "fileId": item["fileId"],
            "path": path,
            "extension": item["extension"],
            "sizeBytes": item["sizeBytes"],
            "structure": tabular_by_path.get(path),
            "documentContent": documents_by_path.get(path),
        }
        if material["structure"] is not None:
            material["contentStatus"] = "tabular_profiled"
        elif material["documentContent"] is not None:
            material["contentStatus"] = material["documentContent"].get("status")
        else:
            material["contentStatus"] = "unsupported_or_not_profiled"
        materials.append(material)

    payload = {
        "rootLabel": inventory.get("rootLabel"),
        "fileCount": len(materials),
        "tabularFileCount": len(tabular_by_path),
        "sheetCount": sum(len(item.get("sheets", [])) for item in tabular["files"]),
        "documentFileCount": len(documents_by_path),
        "materials": materials,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    statuses = Counter(item["contentStatus"] for item in materials)
    summary = {
        "sourceFileCount": inventory.get("fileCount", 0),
        "catalogFileCount": len(materials),
        "uncoveredFileCount": sum(item["contentStatus"] == "unsupported_or_not_profiled" for item in materials),
        "tabularFileCount": len(tabular_by_path),
        "sheetCount": payload["sheetCount"],
        "tabularParseErrorCount": sum("parseError" in item for item in tabular["files"]),
        "documentFileCount": len(documents_by_path),
        "documentParseErrorCount": sum(item.get("status") == "error" for item in documents["files"]),
        "contentStatusCounts": dict(sorted(statuses.items())),
    }
    summary_output = output.with_name("material-summary.json")
    summary_temporary = summary_output.with_suffix(summary_output.suffix + ".tmp")
    summary_temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(summary_temporary, summary_output)
    print(json.dumps({
        "status": "completed",
        "output": str(output),
        "summaryOutput": str(summary_output),
        "fileCount": payload["fileCount"],
        "tabularFileCount": payload["tabularFileCount"],
        "sheetCount": payload["sheetCount"],
        "documentFileCount": payload["documentFileCount"],
        "uncoveredFileCount": summary["uncoveredFileCount"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
