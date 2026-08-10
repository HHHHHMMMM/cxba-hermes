#!/usr/bin/env python3
"""Stream exact decimal totals and optional group totals."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cxba_material_common import (
    MaterialToolError,
    iter_table_rows,
    load_material,
    require_columns,
    write_json,
)


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered:
        return None
    negative = rendered.startswith("(") and rendered.endswith(")")
    if negative:
        rendered = rendered[1:-1].strip()
    rendered = rendered.replace(",", "")
    try:
        amount = Decimal(rendered)
    except InvalidOperation as exc:
        raise ValueError(rendered) from exc
    if not amount.is_finite():
        raise ValueError(rendered)
    return -amount if negative else amount


def group_value(row: dict[str, Any], columns: list[str]) -> tuple[str, ...]:
    return tuple("" if row[column] is None else str(row[column]).strip() for column in columns)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--amount-column", required=True)
    parser.add_argument("--group-by", action="append", default=[])
    parser.add_argument("--max-groups", type=int, default=100000)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.max_groups < 1:
        raise MaterialToolError("max-groups must be at least 1")

    entry, path = load_material(args.catalog, args.material_id)
    headers, rows = iter_table_rows(path, table=args.sheet, header_row=args.header_row)
    require_columns(headers, [args.amount_column, *args.group_by])

    total = Decimal("0")
    row_count = 0
    valid_count = 0
    null_count = 0
    invalid_count = 0
    groups: OrderedDict[tuple[str, ...], dict[str, Any]] = OrderedDict()
    for _, row in rows:
        row_count += 1
        try:
            amount = parse_decimal(row[args.amount_column])
        except ValueError:
            invalid_count += 1
            continue
        if amount is None:
            null_count += 1
            continue
        valid_count += 1
        total += amount
        key = group_value(row, args.group_by)
        bucket = groups.get(key)
        if bucket is None:
            if len(groups) >= args.max_groups:
                raise MaterialToolError(
                    "Distinct group count exceeded max-groups; use a narrower grouping"
                )
            bucket = {"sum": Decimal("0"), "rowCount": 0}
            groups[key] = bucket
        bucket["sum"] += amount
        bucket["rowCount"] += 1

    grouped_output = [
        {
            "group": dict(zip(args.group_by, key, strict=True)),
            "amount": format(bucket["sum"], "f"),
            "rowCount": bucket["rowCount"],
        }
        for key, bucket in groups.items()
    ]
    write_json(
        args.output,
        {
            "materialId": args.material_id,
            "relativePath": entry["relativePath"],
            "table": args.sheet or "data",
            "amountColumn": args.amount_column,
            "groupBy": args.group_by,
            "rowCount": row_count,
            "validAmountCount": valid_count,
            "nullAmountCount": null_count,
            "invalidAmountCount": invalid_count,
            "amount": format(total, "f"),
            "groups": grouped_output,
        },
    )
    print(
        "decimal_summary_written "
        f"materialId={args.material_id} rows={row_count} valid={valid_count} "
        f"invalid={invalid_count} output={args.output}"
    )


if __name__ == "__main__":
    try:
        main()
    except MaterialToolError as exc:
        raise SystemExit(f"decimal_summary_failed: {exc}") from exc
