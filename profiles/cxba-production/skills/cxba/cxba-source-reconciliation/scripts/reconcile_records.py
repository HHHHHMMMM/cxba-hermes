#!/usr/bin/env python3
"""Reconcile normalized detail and summary records without fuzzy deduplication."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


class ReconciliationError(ValueError):
    pass


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def require_string_map(
    value: Any,
    field: str,
    location: str,
    *,
    allow_null_values: bool = False,
    allow_empty: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReconciliationError(f"{location}: {field} must be an object")
    if not allow_empty and not value:
        raise ReconciliationError(f"{location}: {field} must not be empty")
    for key, item in value.items():
        if not nonempty_string(key):
            raise ReconciliationError(f"{location}: {field} has an empty field name")
        if item is None and allow_null_values:
            continue
        if not nonempty_string(item):
            raise ReconciliationError(
                f"{location}: {field}.{key} must be a non-empty string"
            )
    return value


def parse_amount(value: Any, field: str, location: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError(f"{location}: {field} must be a decimal string or null")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ReconciliationError(f"{location}: {field} is not a decimal string") from exc
    if not amount.is_finite():
        raise ReconciliationError(f"{location}: {field} must be finite")
    return amount


def validate_record(record: Any, location: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ReconciliationError(f"{location}: each JSONL line must be an object")
    if not nonempty_string(record.get("source_id")):
        raise ReconciliationError(f"{location}: source_id must be a non-empty string")
    locator = record.get("source_locator")
    if not isinstance(locator, dict) or not locator:
        raise ReconciliationError(f"{location}: source_locator must be a non-empty object")

    record_type = record.get("record_type")
    if record_type == "detail":
        business_key = record.get("business_key")
        if business_key is not None:
            require_string_map(
                business_key, "business_key", location, allow_empty=False
            )
        require_string_map(
            record.get("critical_fields"),
            "critical_fields",
            location,
            allow_null_values=True,
        )
        parse_amount(record.get("amount"), "amount", location)
        dimensions = record.get("dimensions", {})
        require_string_map(dimensions, "dimensions", location)
        amount = record.get("amount")
        critical_amount = record["critical_fields"].get("amount")
        if critical_amount != amount:
            raise ReconciliationError(
                f"{location}: amount must equal critical_fields.amount"
            )
    elif record_type == "summary":
        summary = record.get("summary")
        if not isinstance(summary, dict):
            raise ReconciliationError(f"{location}: summary must be an object")
        count = summary.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise ReconciliationError(
                f"{location}: summary.count must be a non-negative integer or null"
            )
        parse_amount(summary.get("amount"), "summary.amount", location)
        if count is None and summary.get("amount") is None:
            raise ReconciliationError(
                f"{location}: summary must provide count or amount"
            )
        require_string_map(record.get("scope", {}), "scope", location)
        targets = record.get("detail_source_ids", [])
        if not isinstance(targets, list) or any(not nonempty_string(item) for item in targets):
            raise ReconciliationError(
                f"{location}: detail_source_ids must be a list of non-empty strings"
            )
        if len(set(targets)) != len(targets):
            raise ReconciliationError(
                f"{location}: detail_source_ids must not contain duplicates"
            )
    else:
        raise ReconciliationError(
            f"{location}: record_type must be 'detail' or 'summary'"
        )
    return record


def load_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError as exc:
            raise ReconciliationError(f"cannot read {path}: {exc}") from exc
        with stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                location = f"{path}:{line_number}"
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReconciliationError(f"{location}: invalid JSON: {exc.msg}") from exc
                records.append(validate_record(record, location))
    if not records:
        raise ReconciliationError("input contains no records")
    return records


def canonical_map(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def key_signature(record: dict[str, Any]) -> str | None:
    business_key = record.get("business_key")
    return canonical_map(business_key) if business_key else None


def critical_complete(record: dict[str, Any]) -> bool:
    fields = record["critical_fields"]
    return bool(fields) and all(nonempty_string(value) for value in fields.values())


def group_is_consistent(records: list[dict[str, Any]]) -> bool:
    if len(records) < 2:
        return True
    if not all(critical_complete(record) for record in records):
        return False
    signatures = {canonical_map(record["critical_fields"]) for record in records}
    return len(signatures) == 1


def conflict_reason(records: list[dict[str, Any]]) -> str:
    if not all(critical_complete(record) for record in records):
        return "critical_fields_incomplete"
    return "critical_fields_disagree"


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def amount_stats(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = Decimal("0")
    missing = 0
    for record in records:
        amount = parse_amount(record.get("amount"), "amount", "validated record")
        if amount is None:
            missing += 1
        else:
            total += amount
    return {"amount": decimal_text(total), "missing_amount_count": missing}


def record_sample(record: dict[str, Any]) -> dict[str, Any]:
    sample = {
        "source_id": record["source_id"],
        "source_locator": record["source_locator"],
    }
    if record["record_type"] == "detail":
        sample.update(
            {
                "business_key": record.get("business_key"),
                "critical_fields": record["critical_fields"],
                "amount": record.get("amount"),
            }
        )
    return sample


def build_groups(
    details: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    keyed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unkeyed: list[dict[str, Any]] = []
    for record in details:
        signature = key_signature(record)
        if signature is None:
            unkeyed.append(record)
        else:
            keyed[signature].append(record)
    return dict(keyed), unkeyed


def source_stats(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_source[record["source_id"]].append(record)

    result = []
    for source_id in sorted(by_source):
        source_records = by_source[source_id]
        details = [record for record in source_records if record["record_type"] == "detail"]
        summaries = [record for record in source_records if record["record_type"] == "summary"]
        stats = amount_stats(details)
        result.append(
            {
                "source_id": source_id,
                "detail_record_count": len(details),
                "detail_amount": stats["amount"],
                "detail_missing_amount_count": stats["missing_amount_count"],
                "stable_key_record_count": sum(key_signature(record) is not None for record in details),
                "unkeyed_record_count": sum(key_signature(record) is None for record in details),
                "summary_record_count": len(summaries),
            }
        )
    return result


def pair_relationships(
    details: list[dict[str, Any]], keyed: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    source_keys: dict[str, set[str]] = defaultdict(set)
    detail_sources = sorted({record["source_id"] for record in details})
    for signature, records in keyed.items():
        for source_id in {record["source_id"] for record in records}:
            source_keys[source_id].add(signature)

    relationships = []
    for source_a, source_b in combinations(detail_sources, 2):
        keys_a = source_keys[source_a]
        keys_b = source_keys[source_b]
        common = keys_a & keys_b
        conflict_count = 0
        consistent_count = 0
        for signature in common:
            pair_records = [
                record
                for record in keyed[signature]
                if record["source_id"] in {source_a, source_b}
            ]
            if group_is_consistent(pair_records):
                consistent_count += 1
            else:
                conflict_count += 1

        if not keys_a or not keys_b:
            relationship = "insufficient_stable_keys"
        elif conflict_count:
            relationship = "conflict"
        elif keys_a == keys_b:
            relationship = "same_business_coverage"
        elif keys_a > keys_b:
            relationship = "source_a_contains_source_b"
        elif keys_b > keys_a:
            relationship = "source_b_contains_source_a"
        else:
            relationship = "supplements"

        relationships.append(
            {
                "source_a": source_a,
                "source_b": source_b,
                "relationship": relationship,
                "source_a_stable_key_count": len(keys_a),
                "source_b_stable_key_count": len(keys_b),
                "common_key_count": len(common),
                "consistent_common_key_count": consistent_count,
                "conflict_common_key_count": conflict_count,
                "source_a_only_key_count": len(keys_a - keys_b),
                "source_b_only_key_count": len(keys_b - keys_a),
            }
        )
    return relationships


def analyze_key_groups(
    keyed: dict[str, list[dict[str, Any]]], sample_limit: int
) -> dict[str, Any]:
    overlap_samples = []
    conflict_samples = []
    overlap_count = 0
    consistent_overlap_count = 0
    conflict_count = 0
    safely_reconciled: list[dict[str, Any]] = []
    source_only: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]], bool]]] = defaultdict(list)

    for signature in sorted(keyed):
        records = keyed[signature]
        sources = sorted({record["source_id"] for record in records})
        repeated = len(records) > 1
        consistent = not repeated or group_is_consistent(records)

        if len(sources) > 1:
            overlap_count += 1
            if consistent:
                consistent_overlap_count += 1
                if len(overlap_samples) < sample_limit:
                    overlap_samples.append(
                        {
                            "business_key": records[0]["business_key"],
                            "source_ids": sources,
                            "record_count": len(records),
                            "critical_fields": records[0]["critical_fields"],
                        }
                    )

        if repeated and not consistent:
            conflict_count += 1
            if len(conflict_samples) < sample_limit:
                conflict_samples.append(
                    {
                        "business_key": records[0]["business_key"],
                        "reason": conflict_reason(records),
                        "source_ids": sources,
                        "records": [record_sample(record) for record in records[:sample_limit]],
                    }
                )
        else:
            safely_reconciled.append(records[0])

        if len(sources) == 1:
            source_only[sources[0]].append((records[0]["business_key"], records, consistent))

    source_only_report = []
    for source_id in sorted(source_only):
        groups = source_only[source_id]
        source_only_report.append(
            {
                "source_id": source_id,
                "key_count": len(groups),
                "record_count": sum(len(records) for _, records, _ in groups),
                "conflict_key_count": sum(not consistent for _, _, consistent in groups),
                "samples": [
                    {
                        "business_key": business_key,
                        "record_count": len(records),
                        "consistent": consistent,
                        "source_locators": [
                            record["source_locator"] for record in records[:sample_limit]
                        ],
                    }
                    for business_key, records, consistent in groups[:sample_limit]
                ],
            }
        )

    reconciled_amounts = amount_stats(safely_reconciled)
    return {
        "stable_key_overlap": {
            "overlap_key_count": overlap_count,
            "consistent_overlap_key_count": consistent_overlap_count,
            "conflict_overlap_key_count": overlap_count - consistent_overlap_count,
            "samples": overlap_samples,
        },
        "conflicts": {"key_count": conflict_count, "samples": conflict_samples},
        "source_only": source_only_report,
        "safely_reconciled": safely_reconciled,
        "reconciled_details": {
            "stable_key_record_count": len(safely_reconciled),
            "stable_key_amount": reconciled_amounts["amount"],
            "stable_key_missing_amount_count": reconciled_amounts["missing_amount_count"],
            "excluded_conflict_key_count": conflict_count,
        },
    }


def matches_scope(record: dict[str, Any], scope: dict[str, str]) -> bool:
    dimensions = record.get("dimensions", {})
    return all(dimensions.get(key) == value for key, value in scope.items())


def reconcile_subset(details: list[dict[str, Any]]) -> dict[str, Any]:
    keyed, unkeyed = build_groups(details)
    accepted: list[dict[str, Any]] = []
    conflict_count = 0
    for records in keyed.values():
        if len(records) > 1 and not group_is_consistent(records):
            conflict_count += 1
        else:
            accepted.append(records[0])
    accepted.extend(unkeyed)
    stats = amount_stats(accepted)
    return {
        "record_count": len(accepted),
        "amount": stats["amount"],
        "missing_amount_count": stats["missing_amount_count"],
        "unkeyed_record_count": len(unkeyed),
        "conflict_key_count": conflict_count,
    }


def summary_checks(
    summaries: list[dict[str, Any]], details: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    checks = []
    available_sources = {record["source_id"] for record in details}
    for summary_record in summaries:
        targets = summary_record.get("detail_source_ids", [])
        result: dict[str, Any] = {
            "source_id": summary_record["source_id"],
            "source_locator": summary_record["source_locator"],
            "scope": summary_record.get("scope", {}),
            "detail_source_ids": targets,
            "declared": summary_record["summary"],
        }
        if not targets:
            result.update({"comparison_status": "unlinked", "calculated": None})
            checks.append(result)
            continue
        missing_sources = sorted(set(targets) - available_sources)
        selected = [
            record
            for record in details
            if record["source_id"] in targets
            and matches_scope(record, summary_record.get("scope", {}))
        ]
        calculated = reconcile_subset(selected)
        declared = summary_record["summary"]
        count_matches = declared.get("count") is None or declared["count"] == calculated["record_count"]
        declared_amount = parse_amount(
            declared.get("amount"), "summary.amount", "validated record"
        )
        amount_matches = (
            declared_amount is None
            or (
                calculated["missing_amount_count"] == 0
                and declared_amount == Decimal(calculated["amount"])
            )
        )
        if missing_sources:
            status = "missing_detail_sources"
        elif calculated["conflict_key_count"]:
            status = "unresolved_conflicts"
        elif calculated["missing_amount_count"] and declared_amount is not None:
            status = "incomplete_amounts"
        elif count_matches and amount_matches:
            status = "matches"
        else:
            status = "mismatch"
        result.update(
            {
                "comparison_status": status,
                "missing_detail_source_ids": missing_sources,
                "calculated": calculated,
            }
        )
        checks.append(result)
    return checks


def build_report(
    records: list[dict[str, Any]], input_paths: list[Path], sample_limit: int
) -> dict[str, Any]:
    if sample_limit < 0 or sample_limit > 100:
        raise ReconciliationError("sample-limit must be between 0 and 100")
    details = [record for record in records if record["record_type"] == "detail"]
    summaries = [record for record in records if record["record_type"] == "summary"]
    keyed, unkeyed = build_groups(details)
    grouped = analyze_key_groups(keyed, sample_limit)
    unkeyed_stats = amount_stats(unkeyed)
    grouped["reconciled_details"].update(
        {
            "unkeyed_record_count": len(unkeyed),
            "unkeyed_amount": unkeyed_stats["amount"],
            "unkeyed_missing_amount_count": unkeyed_stats["missing_amount_count"],
        }
    )
    return {
        "input": {
            "paths": [str(path) for path in input_paths],
            "record_count": len(records),
            "sample_limit": sample_limit,
        },
        "sources": source_stats(records),
        "relationships": pair_relationships(details, keyed),
        "stable_key_overlap": grouped["stable_key_overlap"],
        "conflicts": grouped["conflicts"],
        "source_only": grouped["source_only"],
        "unkeyed": {
            "record_count": len(unkeyed),
            "amount": unkeyed_stats["amount"],
            "missing_amount_count": unkeyed_stats["missing_amount_count"],
            "samples": [record_sample(record) for record in unkeyed[:sample_limit]],
        },
        "reconciled_details": grouped["reconciled_details"],
        "summary_checks": summary_checks(summaries, details),
    }


def run_self_test() -> None:
    detail = lambda source, row, key, amount, critical=None: {
        "source_id": source,
        "record_type": "detail",
        "source_locator": {"row": row},
        "business_key": {"transaction_id": key} if key else None,
        "critical_fields": critical or {"amount": amount, "currency": "CNY"},
        "amount": amount,
        "dimensions": {"month": "2026-01"},
    }
    fixture = [
        detail("a", 1, "K1", "10.00"),
        detail("a", 2, "K2", "20.00"),
        detail("a", 3, "K4", "40.00"),
        detail("a", 4, None, "5.00"),
        detail("b", 1, "K1", "10.00"),
        detail("b", 2, "K3", "30.00"),
        detail("b", 3, "K4", "41.00"),
        detail("c", 1, "K1", "10.00"),
        detail("c", 2, "K2", "20.00"),
        {
            "source_id": "summary",
            "record_type": "summary",
            "source_locator": {"row": 1},
            "summary": {"count": 4, "amount": "75.00"},
            "scope": {"month": "2026-01"},
            "detail_source_ids": ["a"],
        },
    ]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "records.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for record in fixture:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        records = load_records([path])
        report = build_report(records, [path], 2)

    relations = {
        (item["source_a"], item["source_b"]): item["relationship"]
        for item in report["relationships"]
    }
    assert relations[("a", "b")] == "conflict"
    assert relations[("a", "c")] == "source_a_contains_source_b"
    assert relations[("b", "c")] == "supplements"
    assert report["stable_key_overlap"]["overlap_key_count"] == 3
    assert report["conflicts"]["key_count"] == 1
    assert report["unkeyed"]["record_count"] == 1
    assert report["summary_checks"][0]["comparison_status"] == "matches"
    assert report["summary_checks"][0]["calculated"]["unkeyed_record_count"] == 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile normalized JSONL records using stable business keys."
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="normalized JSONL files")
    parser.add_argument("--output", type=Path, help="write JSON report to this new path")
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        run_self_test()
        print("self-test: ok")
        return 0
    if not args.inputs:
        raise ReconciliationError("provide at least one normalized JSONL input")
    input_paths = [path.resolve() for path in args.inputs]
    if len(set(input_paths)) != len(input_paths):
        raise ReconciliationError("the same input path must not be provided more than once")
    if args.output and args.output.resolve() in input_paths:
        raise ReconciliationError("output path must not overwrite an input file")
    records = load_records(input_paths)
    report = build_report(records, input_paths, args.sample_limit)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
        except FileExistsError as exc:
            raise ReconciliationError("output path already exists; choose a new path") from exc
        except OSError as exc:
            raise ReconciliationError(f"cannot write {args.output}: {exc}") from exc
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReconciliationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
