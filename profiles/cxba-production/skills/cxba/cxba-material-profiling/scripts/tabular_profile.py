#!/usr/bin/env python3
"""Create bounded structural profiles for supported tabular materials."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from pathlib import Path
from typing import Any

from cxba_material_common import (
    MaterialToolError,
    iter_table_rows,
    list_tables,
    load_material,
    write_json,
)


def value_kind(value: Any) -> str:
    if value is None or value == "":
        return "empty"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if hasattr(value, "isoformat"):
        return "date_or_time"
    return "text"


def bounded_sample(row: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for column, value in row.items():
        text = value if value is None else str(value)
        rendered[column] = text if value is None or len(text) <= limit else text[:limit] + "…"
    return rendered


def profile_table(path: Path, table: str, header_row: int, sample_rows: int) -> dict[str, Any]:
    headers, rows = iter_table_rows(path, table=table, header_row=header_row)
    first: list[dict[str, Any]] = []
    last: deque[dict[str, Any]] = deque(maxlen=sample_rows)
    kinds = {header: Counter() for header in headers}
    row_count = 0
    for row_number, row in rows:
        row_count += 1
        rendered = {"rowNumber": row_number, "values": bounded_sample(row)}
        if len(first) < sample_rows:
            first.append(rendered)
        last.append(rendered)
        for header, value in row.items():
            kinds[header][value_kind(value)] += 1
    return {
        "table": table,
        "headerRow": header_row,
        "headers": headers,
        "rowCount": row_count,
        "columnKinds": {header: dict(counter) for header, counter in kinds.items()},
        "firstRows": first,
        "lastRows": list(last),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--sample-rows", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.sample_rows < 1 or args.sample_rows > 50:
        raise MaterialToolError("sample-rows must be between 1 and 50")

    entry, path = load_material(args.catalog, args.material_id)
    tables = [args.sheet] if args.sheet else list_tables(path)
    profiles = [profile_table(path, table, args.header_row, args.sample_rows) for table in tables]
    write_json(
        args.output,
        {
            "materialId": args.material_id,
            "relativePath": entry["relativePath"],
            "tableCount": len(profiles),
            "tables": profiles,
        },
    )
    print(f"tabular_profile_written tableCount={len(profiles)} output={args.output}")


if __name__ == "__main__":
    try:
        main()
    except MaterialToolError as exc:
        raise SystemExit(f"tabular_profile_failed: {exc}") from exc
