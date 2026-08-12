#!/usr/bin/env python3
"""Analyze bounded candidates from normalized expense JSONL.

This module uses only the Python standard library. It reports reproducible
aggregates and candidate source references; it never labels conduct unlawful or
exports the normalized row set.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO


STATISTICAL_UNITS = {"CLAIM", "LINE_ITEM", "PAYMENT"}
CATEGORY_KINDS = {"REIMBURSEMENT", "BONUS", "ALLOWANCE", "OTHER", "UNKNOWN"}
STATUSES = {
    "SUBMITTED",
    "APPROVED",
    "REJECTED",
    "PAID",
    "REFUNDED",
    "REVERSED",
    "CANCELLED",
    "UNKNOWN",
}
DATE_FIELDS = {"applied_at", "approved_at", "paid_at", "status_at"}
SUBJECT_FIELDS = {"applicant_id", "payee_id"}
MAX_TOP = 50
MAX_REFS_PER_CANDIDATE = 3
NOTICE = (
    "Pattern candidates only. Large amounts, high frequency, concentration, "
    "spikes, repeated amounts, same-day clusters, threshold proximity, weekend "
    "or night timing, refunds, and reversals do not establish misconduct."
)

REQUIRED_FIELDS = {
    "record_id",
    "statistical_unit",
    "expense_id",
    "business_key",
    "applicant_id",
    "payee_id",
    "project_id",
    "category_kind",
    "category",
    "status",
    "amount",
    "impact_amount",
    "currency",
    "applied_at",
    "approved_at",
    "paid_at",
    "status_at",
    "source_ref",
}


class ExpenseValidationError(ValueError):
    """Raised when input or analysis scope violates the expense contract."""


@dataclass(frozen=True)
class SourceRef:
    source_file: str
    source_sheet: str
    source_row: int | str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_sheet": self.source_sheet,
            "source_row": self.source_row,
        }


@dataclass
class ExpenseRecord:
    record_id: str
    statistical_unit: str
    expense_id: str | None
    business_key: str | None
    applicant_id: str | None
    payee_id: str | None
    project_id: str | None
    category_kind: str
    category: str
    status: str
    amount: Decimal
    impact_amount: Decimal | None
    currency: str
    applied_at: datetime | None
    approved_at: datetime | None
    paid_at: datetime | None
    status_at: datetime | None
    source_refs: list[SourceRef]
    input_line: int
    record_ids: list[str] = field(default_factory=list)
    analysis_at: datetime | None = None

    def duplicate_signature(self) -> tuple[Any, ...]:
        return (
            self.statistical_unit,
            self.expense_id,
            self.applicant_id,
            self.payee_id,
            self.project_id,
            self.category_kind,
            self.category,
            self.status,
            self.amount,
            self.impact_amount,
            self.currency,
            self.applied_at,
            self.approved_at,
            self.paid_at,
            self.status_at,
        )


@dataclass(frozen=True)
class LoadSummary:
    input_rows: int
    canonical_rows: int
    keyed_rows: int
    unkeyed_rows: int
    merged_groups: int
    merged_rows: int


@dataclass(frozen=True)
class Boundary:
    value: date | datetime
    date_only: bool


@dataclass
class Aggregate:
    count: int = 0
    gross_amount: Decimal = Decimal(0)
    known_impact_amount: Decimal = Decimal(0)
    unknown_impact_count: int = 0
    records: list[ExpenseRecord] = field(default_factory=list)

    def add(self, record: ExpenseRecord) -> None:
        self.count += 1
        self.gross_amount += record.amount
        if record.impact_amount is None:
            self.unknown_impact_count += 1
        else:
            self.known_impact_amount += record.impact_amount
        self.records.append(record)


def require_text(value: Any, field_name: str, line_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} must be a non-empty string"
        )
    return value.strip()


def optional_text(value: Any, field_name: str, line_number: int) -> str | None:
    if value is None:
        return None
    return require_text(value, field_name, line_number)


def require_string(
    value: Any,
    field_name: str,
    line_number: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ExpenseValidationError(f"line {line_number}: {field_name} must be a string")
    text = value.strip()
    if not allow_empty and not text:
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} must be a non-empty string"
        )
    return text


def require_choice(
    value: Any,
    field_name: str,
    choices: set[str],
    line_number: int,
) -> str:
    text = require_text(value, field_name, line_number).upper()
    if text not in choices:
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} is outside the contract"
        )
    return text


def parse_decimal(
    value: Any,
    field_name: str,
    line_number: int,
    *,
    positive: bool,
) -> Decimal:
    if isinstance(value, bool):
        raise ExpenseValidationError(f"line {line_number}: {field_name} is invalid")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} must be a finite decimal"
        ) from None
    if not result.is_finite() or (positive and result <= 0):
        qualifier = "positive " if positive else ""
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} must be a {qualifier}finite decimal"
        )
    return result


def optional_decimal(value: Any, field_name: str, line_number: int) -> Decimal | None:
    if value is None:
        return None
    return parse_decimal(value, field_name, line_number, positive=False)


def parse_timestamp(value: Any, field_name: str, line_number: int) -> datetime | None:
    if value is None:
        return None
    text = require_text(value, field_name, line_number)
    if "T" not in text and " " not in text:
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} must include date and time"
        )
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        raise ExpenseValidationError(
            f"line {line_number}: {field_name} must be ISO-8601"
        ) from None


def require_source_row(value: Any, line_number: int) -> int | str:
    if isinstance(value, str):
        locator = value.strip()
        if locator:
            return locator
    elif not isinstance(value, bool):
        try:
            row = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            row = Decimal(0)
        if row.is_finite() and row == row.to_integral() and row > 0:
            return int(row)
    raise ExpenseValidationError(
        f"line {line_number}: source_ref.source_row must be a positive integer or locator"
    )


def parse_source_ref(value: Any, line_number: int) -> SourceRef:
    if not isinstance(value, dict):
        raise ExpenseValidationError(f"line {line_number}: source_ref must be an object")
    expected = {"source_file", "source_sheet", "source_row"}
    if set(value) != expected:
        raise ExpenseValidationError(
            f"line {line_number}: source_ref fields do not match the contract"
        )
    return SourceRef(
        source_file=require_text(
            value["source_file"], "source_ref.source_file", line_number
        ),
        source_sheet=require_string(
            value["source_sheet"],
            "source_ref.source_sheet",
            line_number,
            allow_empty=True,
        ),
        source_row=require_source_row(value["source_row"], line_number),
    )


def parse_record(raw: Any, line_number: int) -> ExpenseRecord:
    if not isinstance(raw, dict):
        raise ExpenseValidationError(f"line {line_number}: each JSON value must be an object")
    missing = sorted(REQUIRED_FIELDS - raw.keys())
    unexpected = sorted(raw.keys() - REQUIRED_FIELDS)
    if missing:
        raise ExpenseValidationError(
            f"line {line_number}: missing required fields: {', '.join(missing)}"
        )
    if unexpected:
        raise ExpenseValidationError(
            f"line {line_number}: unexpected fields: {', '.join(unexpected)}"
        )
    record_id = require_text(raw["record_id"], "record_id", line_number)
    currency = require_text(raw["currency"], "currency", line_number).upper()
    if currency != raw["currency"]:
        raise ExpenseValidationError(f"line {line_number}: currency must be uppercase")
    category = require_text(raw["category"], "category", line_number)
    if category != category.upper():
        raise ExpenseValidationError(f"line {line_number}: category must be an uppercase code")
    record = ExpenseRecord(
        record_id=record_id,
        record_ids=[record_id],
        statistical_unit=require_choice(
            raw["statistical_unit"], "statistical_unit", STATISTICAL_UNITS, line_number
        ),
        expense_id=optional_text(raw["expense_id"], "expense_id", line_number),
        business_key=optional_text(raw["business_key"], "business_key", line_number),
        applicant_id=optional_text(raw["applicant_id"], "applicant_id", line_number),
        payee_id=optional_text(raw["payee_id"], "payee_id", line_number),
        project_id=optional_text(raw["project_id"], "project_id", line_number),
        category_kind=require_choice(
            raw["category_kind"], "category_kind", CATEGORY_KINDS, line_number
        ),
        category=category,
        status=require_choice(raw["status"], "status", STATUSES, line_number),
        amount=parse_decimal(raw["amount"], "amount", line_number, positive=True),
        impact_amount=optional_decimal(
            raw["impact_amount"], "impact_amount", line_number
        ),
        currency=currency,
        applied_at=parse_timestamp(raw["applied_at"], "applied_at", line_number),
        approved_at=parse_timestamp(raw["approved_at"], "approved_at", line_number),
        paid_at=parse_timestamp(raw["paid_at"], "paid_at", line_number),
        status_at=parse_timestamp(raw["status_at"], "status_at", line_number),
        source_refs=[parse_source_ref(raw["source_ref"], line_number)],
        input_line=line_number,
    )
    return record


def load_records(
    stream: TextIO,
    dedup_mode: str,
) -> tuple[list[ExpenseRecord], LoadSummary]:
    records: list[ExpenseRecord] = []
    keyed: dict[str, ExpenseRecord] = {}
    key_counts: dict[str, int] = {}
    record_ids: set[str] = set()
    input_rows = 0
    keyed_rows = 0
    unkeyed_rows = 0

    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        input_rows += 1
        try:
            raw = json.loads(
                line,
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid numeric constant {value}")
                ),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ExpenseValidationError(
                f"line {line_number}: invalid JSON: {exc}"
            ) from None
        record = parse_record(raw, line_number)
        if record.record_id in record_ids:
            raise ExpenseValidationError(f"line {line_number}: duplicate record_id")
        record_ids.add(record.record_id)

        if record.business_key is None:
            unkeyed_rows += 1
            records.append(record)
            continue

        keyed_rows += 1
        key_counts[record.business_key] = key_counts.get(record.business_key, 0) + 1
        if dedup_mode == "none":
            records.append(record)
            continue
        existing = keyed.get(record.business_key)
        if existing is None:
            keyed[record.business_key] = record
            records.append(record)
        elif existing.duplicate_signature() != record.duplicate_signature():
            raise ExpenseValidationError(
                f"lines {existing.input_line} and {line_number}: business_key conflicts"
            )
        else:
            existing.record_ids.extend(record.record_ids)
            existing.source_refs.extend(record.source_refs)

    if not records:
        raise ExpenseValidationError("input contains no expense rows")
    units = {record.statistical_unit for record in records}
    if len(units) != 1:
        raise ExpenseValidationError("input mixes statistical_unit values")
    merged_groups = (
        sum(1 for count in key_counts.values() if count > 1)
        if dedup_mode == "business-key"
        else 0
    )
    return records, LoadSummary(
        input_rows=input_rows,
        canonical_rows=len(records),
        keyed_rows=keyed_rows,
        unkeyed_rows=unkeyed_rows,
        merged_groups=merged_groups,
        merged_rows=input_rows - len(records),
    )


def parse_boundary(text: str, field_name: str) -> Boundary:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return Boundary(date.fromisoformat(text), True)
        normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        return Boundary(datetime.fromisoformat(normalized), False)
    except ValueError:
        raise ExpenseValidationError(
            f"{field_name} must be an ISO date or datetime"
        ) from None


def boundary_contains(timestamp: datetime, start: Boundary, end: Boundary) -> bool:
    if start.date_only:
        assert isinstance(start.value, date) and isinstance(end.value, date)
        return start.value <= timestamp.date() <= end.value
    assert isinstance(start.value, datetime) and isinstance(end.value, datetime)
    if (timestamp.tzinfo is None) != (start.value.tzinfo is None):
        raise ExpenseValidationError(
            "period datetime timezone awareness must match selected record timestamps"
        )
    return start.value <= timestamp <= end.value


def validate_boundaries(start: Boundary, end: Boundary) -> None:
    if start.date_only != end.date_only:
        raise ExpenseValidationError(
            "period-start and period-end must both be dates or both be datetimes"
        )
    if start.value > end.value:
        raise ExpenseValidationError("period-start must not be after period-end")


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def datetime_text(value: datetime) -> str:
    return value.isoformat()


def source_refs(records: Iterable[ExpenseRecord]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, int | str], SourceRef] = {}
    ordered_records = sorted(
        records,
        key=lambda record: (-record.amount, record.input_line, record.record_id),
    )
    for record in ordered_records:
        for ref in record.source_refs:
            key = (ref.source_file, ref.source_sheet, ref.source_row)
            unique.setdefault(key, ref)
            if len(unique) >= MAX_REFS_PER_CANDIDATE:
                return [item.as_dict() for item in unique.values()]
    return [item.as_dict() for item in unique.values()]


def summarize_records(records: Sequence[ExpenseRecord]) -> dict[str, Any]:
    gross = sum((record.amount for record in records), Decimal(0))
    known = sum(
        (
            record.impact_amount
            for record in records
            if record.impact_amount is not None
        ),
        Decimal(0),
    )
    unknown = sum(record.impact_amount is None for record in records)
    return {
        "record_count": len(records),
        "gross_amount": decimal_text(gross),
        "known_impact_amount": decimal_text(known),
        "unknown_impact_count": unknown,
    }


def bounded_section(items: list[dict[str, Any]], total: int, top: int) -> dict[str, Any]:
    return {
        "candidate_count": total,
        "returned_count": min(len(items), top),
        "top_candidates": items[:top],
    }


def time_bucket(timestamp: datetime, mode: str) -> str:
    if mode == "day":
        return timestamp.date().isoformat()
    if mode == "week":
        year, week, _ = timestamp.isocalendar()
        return f"{year}-W{week:02d}"
    return timestamp.strftime("%Y-%m")


def aggregate_by(
    records: Sequence[ExpenseRecord],
    dimension: str,
    key_fn: Callable[[ExpenseRecord], str | None],
    top: int,
) -> dict[str, Any]:
    groups: dict[str, Aggregate] = {}
    missing_count = 0
    for record in records:
        key = key_fn(record)
        if key is None:
            missing_count += 1
            continue
        aggregate = groups.setdefault(key, Aggregate())
        aggregate.add(record)
    ordered = sorted(
        groups.items(),
        key=lambda item: (-item[1].count, -item[1].gross_amount, item[0]),
    )
    return {
        "dimension": dimension,
        "group_count": len(groups),
        "missing_key_record_count": missing_count,
        "returned_count": min(len(ordered), top),
        "top_groups": [
            {
                "key": key,
                "record_count": aggregate.count,
                "gross_amount": decimal_text(aggregate.gross_amount),
                "known_impact_amount": decimal_text(aggregate.known_impact_amount),
                "unknown_impact_count": aggregate.unknown_impact_count,
                "source_refs": source_refs(aggregate.records),
            }
            for key, aggregate in ordered[:top]
        ],
    }


def record_candidate(record: ExpenseRecord, subject_field: str) -> dict[str, Any]:
    assert record.analysis_at is not None
    return {
        "date": record.analysis_at.date().isoformat(),
        "amount": decimal_text(record.amount),
        "subject_id": getattr(record, subject_field),
        "project_id": record.project_id,
        "category": record.category,
        "status": record.status,
        "source_refs": source_refs([record]),
    }


def largest_amounts(
    records: Sequence[ExpenseRecord], subject_field: str, top: int
) -> dict[str, Any]:
    ordered = sorted(
        records,
        key=lambda record: (-record.amount, record.analysis_at, record.input_line),
    )
    return bounded_section(
        [record_candidate(record, subject_field) for record in ordered[:top]],
        len(records),
        top,
    )


def repeated_amounts(
    records: Sequence[ExpenseRecord],
    subject_field: str,
    top: int,
) -> dict[str, Any]:
    groups: dict[tuple[str, Decimal], list[ExpenseRecord]] = {}
    for record in records:
        subject = getattr(record, subject_field)
        if subject is not None:
            groups.setdefault((subject, record.amount), []).append(record)
    candidates = [
        {
            "subject_id": subject,
            "amount": decimal_text(amount),
            "record_count": len(group),
            "total_amount": decimal_text(sum((item.amount for item in group), Decimal(0))),
            "source_refs": source_refs(group),
        }
        for (subject, amount), group in groups.items()
        if len(group) >= 2
    ]
    candidates.sort(
        key=lambda item: (
            -item["record_count"],
            -Decimal(item["total_amount"]),
            item["subject_id"],
        )
    )
    return bounded_section(candidates, len(candidates), top)


def find_best_window(
    records: Sequence[ExpenseRecord], window_minutes: int
) -> list[ExpenseRecord]:
    ordered = sorted(records, key=lambda record: (record.analysis_at, record.input_line))
    best: list[ExpenseRecord] = []
    left = 0
    for right, record in enumerate(ordered):
        assert record.analysis_at is not None
        while left <= right:
            assert ordered[left].analysis_at is not None
            elapsed = record.analysis_at - ordered[left].analysis_at
            if elapsed <= timedelta(minutes=window_minutes):
                break
            left += 1
        current = ordered[left : right + 1]
        current_total = sum((item.amount for item in current), Decimal(0))
        best_total = sum((item.amount for item in best), Decimal(0))
        if (len(current), current_total) > (len(best), best_total):
            best = current
    return best


def same_day_clusters(
    records: Sequence[ExpenseRecord],
    subject_field: str,
    window_minutes: int,
    threshold: Decimal | None,
    top: int,
) -> dict[str, Any]:
    groups: dict[tuple[str, date], list[ExpenseRecord]] = {}
    for record in records:
        subject = getattr(record, subject_field)
        if subject is None or record.analysis_at is None:
            continue
        groups.setdefault((subject, record.analysis_at.date()), []).append(record)
    candidates: list[dict[str, Any]] = []
    for (subject, day), group in groups.items():
        best = find_best_window(group, window_minutes)
        if len(best) < 2:
            continue
        total = sum((item.amount for item in best), Decimal(0))
        item: dict[str, Any] = {
            "subject_id": subject,
            "date": day.isoformat(),
            "window_minutes": window_minutes,
            "record_count": len(best),
            "total_amount": decimal_text(total),
            "distinct_payee_count": len(
                {record.payee_id for record in best if record.payee_id is not None}
            ),
            "distinct_project_count": len(
                {record.project_id for record in best if record.project_id is not None}
            ),
            "source_refs": source_refs(best),
        }
        if threshold is not None:
            item["all_individual_amounts_below_threshold"] = all(
                record.amount < threshold for record in best
            )
            item["combined_amount_reaches_threshold"] = total >= threshold
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            -item["record_count"],
            -Decimal(item["total_amount"]),
            item["subject_id"],
            item["date"],
        )
    )
    return bounded_section(candidates, len(candidates), top)


def threshold_clusters(
    records: Sequence[ExpenseRecord],
    subject_field: str,
    threshold: Decimal | None,
    threshold_window: Decimal | None,
    top: int,
) -> dict[str, Any]:
    if threshold is None or threshold_window is None:
        return {
            "enabled": False,
            "reason": "No user-approved threshold and threshold window were supplied.",
            "candidate_count": 0,
            "returned_count": 0,
            "top_candidates": [],
        }
    lower = threshold - threshold_window
    upper = threshold + threshold_window
    matching = [record for record in records if lower <= record.amount <= upper]
    groups: dict[str, list[ExpenseRecord]] = {}
    for record in matching:
        subject = getattr(record, subject_field)
        if subject is not None:
            groups.setdefault(subject, []).append(record)
    candidates = [
        {
            "subject_id": subject,
            "matching_record_count": len(group),
            "matching_gross_amount": decimal_text(
                sum((record.amount for record in group), Decimal(0))
            ),
            "source_refs": source_refs(group),
        }
        for subject, group in groups.items()
        if len(group) >= 2
    ]
    candidates.sort(
        key=lambda item: (
            -item["matching_record_count"],
            -Decimal(item["matching_gross_amount"]),
            item["subject_id"],
        )
    )
    section = bounded_section(candidates, len(candidates), top)
    section.update(
        {
            "enabled": True,
            "threshold": decimal_text(threshold),
            "threshold_window": decimal_text(threshold_window),
            "matching_record_count": len(matching),
        }
    )
    return section


def baseline_spikes(
    records: Sequence[ExpenseRecord],
    subject_field: str,
    window_days: int,
    min_active_days: int,
    spike_multiple: Decimal,
    top: int,
) -> dict[str, Any]:
    groups: dict[tuple[str, date], list[ExpenseRecord]] = {}
    for record in records:
        subject = getattr(record, subject_field)
        if subject is None or record.analysis_at is None:
            continue
        groups.setdefault((subject, record.analysis_at.date()), []).append(record)
    daily: dict[str, list[tuple[date, Decimal, list[ExpenseRecord]]]] = {}
    for (subject, day), group in groups.items():
        total = sum((record.amount for record in group), Decimal(0))
        daily.setdefault(subject, []).append((day, total, group))

    candidates: list[dict[str, Any]] = []
    for subject, values in daily.items():
        values.sort(key=lambda item: item[0])
        for index, (day, current, current_records) in enumerate(values):
            baselines = [
                amount
                for prior_day, amount, _ in values[:index]
                if 0 < (day - prior_day).days <= window_days
            ]
            if len(baselines) < min_active_days:
                continue
            median = statistics.median(baselines)
            if median <= 0:
                continue
            multiple = current / median
            if multiple < spike_multiple:
                continue
            candidates.append(
                {
                    "subject_id": subject,
                    "date": day.isoformat(),
                    "current_amount": decimal_text(current),
                    "baseline_active_day_count": len(baselines),
                    "baseline_median_amount": decimal_text(median),
                    "multiple_of_baseline": decimal_text(multiple),
                    "source_refs": source_refs(current_records),
                }
            )
    candidates.sort(
        key=lambda item: (
            -Decimal(item["multiple_of_baseline"]),
            -Decimal(item["current_amount"]),
            item["subject_id"],
            item["date"],
        )
    )
    section = bounded_section(candidates, len(candidates), top)
    section["baseline_rule"] = (
        f"Median of prior active days within {window_days} calendar days; "
        f"minimum {min_active_days} active days; candidate at or above "
        f"{decimal_text(spike_multiple)} times baseline."
    )
    return section


def timing_candidates(
    records: Sequence[ExpenseRecord],
    night_start_hour: int,
    night_end_hour: int,
    subject_field: str,
    top: int,
) -> dict[str, Any]:
    weekend = [
        record
        for record in records
        if record.analysis_at is not None and record.analysis_at.weekday() >= 5
    ]

    def is_night(record: ExpenseRecord) -> bool:
        assert record.analysis_at is not None
        hour = record.analysis_at.hour
        if night_start_hour > night_end_hour:
            return hour >= night_start_hour or hour < night_end_hour
        return night_start_hour <= hour < night_end_hour

    night = [record for record in records if is_night(record)]
    return {
        "weekend": {
            **summarize_records(weekend),
            "top_candidates": [
                record_candidate(record, subject_field)
                for record in sorted(weekend, key=lambda item: -item.amount)[:top]
            ],
        },
        "night": {
            "window": {
                "start_hour_inclusive": night_start_hour,
                "end_hour_exclusive": night_end_hour,
            },
            **summarize_records(night),
            "top_candidates": [
                record_candidate(record, subject_field)
                for record in sorted(night, key=lambda item: -item.amount)[:top]
            ],
        },
    }


def refund_reversal_impact(
    scoped_before_status: Sequence[ExpenseRecord],
    selected_statuses: set[str],
    subject_field: str,
    top: int,
) -> dict[str, Any]:
    impact_records = [
        record
        for record in scoped_before_status
        if record.status in {"REFUNDED", "REVERSED"}
    ]
    included = [record for record in impact_records if record.status in selected_statuses]
    excluded = [record for record in impact_records if record.status not in selected_statuses]
    ordered = sorted(
        impact_records,
        key=lambda record: (-record.amount, record.analysis_at, record.input_line),
    )
    return {
        "all_refund_reversal_records": summarize_records(impact_records),
        "included_in_main_status_scope": summarize_records(included),
        "excluded_from_main_status_scope": summarize_records(excluded),
        "top_candidates": [
            record_candidate(record, subject_field) for record in ordered[:top]
        ],
        "impact_rule": (
            "Only source-established impact_amount values affect known impact; "
            "status alone never determines the sign."
        ),
    }


def prepare_scope(
    records: Sequence[ExpenseRecord],
    *,
    start: Boundary,
    end: Boundary,
    date_field: str,
    statuses: set[str],
    category_kinds: set[str],
    currency: str,
    statistical_unit: str,
) -> tuple[list[ExpenseRecord], list[ExpenseRecord], dict[str, int]]:
    excluded = {
        "statistical_unit": 0,
        "category_kind": 0,
        "currency": 0,
        "missing_selected_date": 0,
        "outside_period": 0,
        "status": 0,
    }
    before_status: list[ExpenseRecord] = []
    selected: list[ExpenseRecord] = []
    awareness: bool | None = None
    for record in records:
        if record.statistical_unit != statistical_unit:
            excluded["statistical_unit"] += 1
            continue
        if record.category_kind not in category_kinds:
            excluded["category_kind"] += 1
            continue
        if record.currency != currency:
            excluded["currency"] += 1
            continue
        timestamp = getattr(record, date_field)
        if timestamp is None:
            excluded["missing_selected_date"] += 1
            continue
        is_aware = timestamp.tzinfo is not None
        if awareness is None:
            awareness = is_aware
        elif awareness != is_aware:
            raise ExpenseValidationError(
                "selected timestamps mix timezone-aware and naive values"
            )
        if not boundary_contains(timestamp, start, end):
            excluded["outside_period"] += 1
            continue
        record.analysis_at = timestamp
        before_status.append(record)
        if record.status not in statuses:
            excluded["status"] += 1
            continue
        selected.append(record)
    if not selected:
        raise ExpenseValidationError("scope contains no selected expense rows")
    return selected, before_status, excluded


def build_report(
    records: Sequence[ExpenseRecord],
    load_summary: LoadSummary,
    args: argparse.Namespace,
) -> dict[str, Any]:
    start = parse_boundary(args.period_start, "period-start")
    end = parse_boundary(args.period_end, "period-end")
    validate_boundaries(start, end)
    selected, before_status, excluded = prepare_scope(
        records,
        start=start,
        end=end,
        date_field=args.date_field,
        statuses=args.statuses,
        category_kinds=args.category_kinds,
        currency=args.currency,
        statistical_unit=args.statistical_unit,
    )

    subject_missing = sum(
        getattr(record, args.subject_field) is None for record in selected
    )
    aggregates = {
        "overall": summarize_records(selected),
        "concentration": {
            "time": aggregate_by(
                selected,
                f"time:{args.time_bucket}",
                lambda record: time_bucket(record.analysis_at, args.time_bucket)
                if record.analysis_at is not None
                else None,
                args.top,
            ),
            "project": aggregate_by(
                selected,
                "project_id",
                lambda record: record.project_id,
                args.top,
            ),
            "applicant": aggregate_by(
                selected,
                "applicant_id",
                lambda record: record.applicant_id,
                args.top,
            ),
            "payee": aggregate_by(
                selected,
                "payee_id",
                lambda record: record.payee_id,
                args.top,
            ),
        },
    }
    candidates = {
        "largest_amounts": largest_amounts(selected, args.subject_field, args.top),
        "relative_baseline_spikes": baseline_spikes(
            selected,
            args.subject_field,
            args.baseline_window_days,
            args.baseline_min_active_days,
            args.spike_multiple,
            args.top,
        ),
        "repeated_amounts": repeated_amounts(selected, args.subject_field, args.top),
        "same_day_same_subject_clusters": same_day_clusters(
            selected,
            args.subject_field,
            args.split_window_minutes,
            args.threshold,
            args.top,
        ),
        "near_threshold_clusters": threshold_clusters(
            selected,
            args.subject_field,
            args.threshold,
            args.threshold_window,
            args.top,
        ),
        "weekend_and_night": timing_candidates(
            selected,
            args.night_start_hour,
            args.night_end_hour,
            args.subject_field,
            args.top,
        ),
        "refund_reversal_impact": refund_reversal_impact(
            before_status, args.statuses, args.subject_field, args.top
        ),
    }
    return {
        "status": "ok",
        "interpretation": NOTICE,
        "scope": {
            "period_start": args.period_start,
            "period_end": args.period_end,
            "period_boundaries_inclusive": True,
            "date_field": args.date_field,
            "statuses": sorted(args.statuses),
            "category_kinds": sorted(args.category_kinds),
            "currency": args.currency,
            "statistical_unit": args.statistical_unit,
            "subject_field": args.subject_field,
            "dedup_mode": args.dedup_mode,
            "threshold": decimal_text(args.threshold) if args.threshold is not None else None,
            "threshold_window": decimal_text(args.threshold_window)
            if args.threshold_window is not None
            else None,
            "baseline_window_days": args.baseline_window_days,
            "baseline_min_active_days": args.baseline_min_active_days,
            "spike_multiple": decimal_text(args.spike_multiple),
            "split_window_minutes": args.split_window_minutes,
            "night_window": [args.night_start_hour, args.night_end_hour],
            "time_bucket": args.time_bucket,
            "top": args.top,
        },
        "input_summary": {
            "input_rows": load_summary.input_rows,
            "canonical_rows": load_summary.canonical_rows,
            "selected_rows": len(selected),
            "keyed_rows": load_summary.keyed_rows,
            "unkeyed_rows": load_summary.unkeyed_rows,
            "business_key_merged_groups": load_summary.merged_groups,
            "merged_rows": load_summary.merged_rows,
            "excluded_rows_by_reason": excluded,
            "selected_subject_missing_count": subject_missing,
            "dedup_rule": (
                "Only identical normalized records sharing a non-empty business_key "
                "are merged when dedup-mode is business-key."
            ),
        },
        "aggregates": aggregates,
        "candidate_sections": candidates,
        "limitations": [
            "Baseline uses prior active days inside the selected period; earlier source history is not inferred.",
            "Candidate source references retain original file, Sheet, and row locations for review.",
            "Unknown impact_amount values are counted but excluded from known impact totals.",
        ],
    }


def csv_choices(value: str, choices: set[str], label: str) -> set[str]:
    parsed = {part.strip().upper() for part in value.split(",") if part.strip()}
    if not parsed or not parsed <= choices:
        allowed = ",".join(sorted(choices))
        raise argparse.ArgumentTypeError(f"{label} must use: {allowed}")
    return parsed


def positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("value must be a positive integer") from None
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return result


def top_value(value: str) -> int:
    result = positive_int(value)
    if result > MAX_TOP:
        raise argparse.ArgumentTypeError(f"top must not exceed {MAX_TOP}")
    return result


def hour_value(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("hour must be an integer from 0 to 23") from None
    if not 0 <= result <= 23:
        raise argparse.ArgumentTypeError("hour must be an integer from 0 to 23")
    return result


def positive_decimal_arg(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("value must be a positive decimal") from None
    if not result.is_finite() or result <= 0:
        raise argparse.ArgumentTypeError("value must be a positive decimal")
    return result


def nonnegative_decimal_arg(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("value must be a non-negative decimal") from None
    if not result.is_finite() or result < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative decimal")
    return result


def synthetic_row(
    number: int,
    *,
    timestamp: str,
    amount: str,
    applicant: str = "subject-a",
    payee: str = "payee-a",
    project: str = "project-a",
    status: str = "PAID",
    impact: str | None = None,
    currency: str = "CNY",
    business_key: str | None = None,
    source_file: str = "synthetic.xlsx",
) -> dict[str, Any]:
    return {
        "record_id": f"rec-{number}",
        "statistical_unit": "CLAIM",
        "expense_id": f"expense-{number}",
        "business_key": business_key,
        "applicant_id": applicant,
        "payee_id": payee,
        "project_id": project,
        "category_kind": "REIMBURSEMENT",
        "category": "TRAVEL",
        "status": status,
        "amount": amount,
        "impact_amount": amount if impact is None else impact,
        "currency": currency,
        "applied_at": timestamp,
        "approved_at": timestamp,
        "paid_at": timestamp,
        "status_at": timestamp,
        "source_ref": {
            "source_file": source_file,
            "source_sheet": "Synthetic",
            "source_row": number + 1,
        },
    }


def self_test_args() -> argparse.Namespace:
    return argparse.Namespace(
        period_start="2026-01-01",
        period_end="2026-02-28",
        date_field="paid_at",
        statuses={"PAID", "REFUNDED", "REVERSED"},
        category_kinds={"REIMBURSEMENT"},
        currency="CNY",
        statistical_unit="CLAIM",
        subject_field="applicant_id",
        dedup_mode="business-key",
        threshold=Decimal("1000"),
        threshold_window=Decimal("25"),
        baseline_window_days=30,
        baseline_min_active_days=3,
        spike_multiple=Decimal("2"),
        split_window_minutes=180,
        night_start_hour=22,
        night_end_hour=6,
        time_bucket="day",
        top=2,
    )


def run_self_test() -> dict[str, Any]:
    rows = [
        synthetic_row(1, timestamp="2026-01-05T10:00:00+08:00", amount="100"),
        synthetic_row(2, timestamp="2026-01-10T10:00:00+08:00", amount="100"),
        synthetic_row(3, timestamp="2026-01-15T10:00:00+08:00", amount="100"),
        synthetic_row(4, timestamp="2026-01-20T22:30:00+08:00", amount="990"),
        synthetic_row(5, timestamp="2026-01-20T23:00:00+08:00", amount="1010"),
        synthetic_row(6, timestamp="2026-01-24T11:00:00+08:00", amount="100"),
        synthetic_row(7, timestamp="2026-02-01T09:00:00+08:00", amount="600", business_key="dup-1"),
        synthetic_row(8, timestamp="2026-02-01T09:00:00+08:00", amount="600", business_key="dup-1", source_file="synthetic-copy.xlsx"),
        synthetic_row(9, timestamp="2026-02-02T09:00:00+08:00", amount="500", status="REFUNDED", impact="-500"),
        synthetic_row(10, timestamp="2026-02-03T09:00:00+08:00", amount="400", status="REVERSED", impact=None),
        synthetic_row(11, timestamp="2026-02-04T09:00:00+08:00", amount="777", currency="USD"),
    ]
    rows[7]["expense_id"] = rows[6]["expense_id"]
    rows[9]["impact_amount"] = None
    stream = StringIO("\n".join(json.dumps(row) for row in rows))
    records, summary = load_records(stream, "business-key")
    args = self_test_args()
    report = build_report(records, summary, args)

    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    check(summary.input_rows == 11, "input rows")
    check(summary.canonical_rows == 10, "business-key duplicate merged")
    check(summary.merged_rows == 1, "merge count")
    merged = next(record for record in records if record.business_key == "dup-1")
    check(len(merged.source_refs) == 2, "merged source refs retained")
    check(report["status"] == "ok", "report status")
    check(report["input_summary"]["selected_rows"] == 9, "currency filter")
    check(
        report["candidate_sections"]["near_threshold_clusters"]["candidate_count"] == 1,
        "threshold cluster",
    )
    check(
        report["candidate_sections"]["same_day_same_subject_clusters"]["candidate_count"] >= 1,
        "same-day cluster",
    )
    check(
        report["candidate_sections"]["relative_baseline_spikes"]["candidate_count"] >= 1,
        "baseline spike",
    )
    check(
        report["candidate_sections"]["weekend_and_night"]["night"]["record_count"] == 2,
        "night window",
    )
    check(
        report["candidate_sections"]["weekend_and_night"]["weekend"]["record_count"] >= 1,
        "weekend",
    )
    refund = report["candidate_sections"]["refund_reversal_impact"]
    check(refund["all_refund_reversal_records"]["record_count"] == 2, "refund count")
    check(
        refund["all_refund_reversal_records"]["unknown_impact_count"] == 1,
        "unknown reversal impact",
    )
    largest_refs = report["candidate_sections"]["largest_amounts"]["top_candidates"][0]["source_refs"]
    check(
        all(
            {"source_file", "source_sheet", "source_row"} == set(ref)
            for ref in largest_refs
        ),
        "original source locations retained",
    )

    repeated = report["candidate_sections"]["repeated_amounts"]
    check(repeated["returned_count"] <= args.top, "top bound")

    conflict_rows = [
        synthetic_row(20, timestamp="2026-01-01T09:00:00+08:00", amount="10", business_key="conflict"),
        synthetic_row(21, timestamp="2026-01-01T09:00:00+08:00", amount="11", business_key="conflict"),
    ]
    try:
        load_records(
            StringIO("\n".join(json.dumps(row) for row in conflict_rows)),
            "business-key",
        )
    except ExpenseValidationError:
        checks += 1
    else:
        raise AssertionError("conflicting business keys must fail")

    return {"status": "ok", "self_test": {"checks_passed": checks}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze bounded expense-pattern candidates from contract JSONL."
    )
    parser.add_argument("--input", help="normalized expense JSONL path")
    parser.add_argument("--output", help="bounded result JSON path; omit for stdout")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--date-field", choices=sorted(DATE_FIELDS))
    parser.add_argument(
        "--statuses",
        type=lambda value: csv_choices(value, STATUSES, "statuses"),
    )
    parser.add_argument(
        "--category-kinds",
        type=lambda value: csv_choices(value, CATEGORY_KINDS, "category-kinds"),
    )
    parser.add_argument("--currency", type=str.upper)
    parser.add_argument("--statistical-unit", choices=sorted(STATISTICAL_UNITS))
    parser.add_argument("--subject-field", choices=sorted(SUBJECT_FIELDS))
    parser.add_argument("--dedup-mode", choices=("none", "business-key"))
    parser.add_argument("--threshold", type=positive_decimal_arg)
    parser.add_argument("--threshold-window", type=nonnegative_decimal_arg)
    parser.add_argument("--baseline-window-days", type=positive_int, default=30)
    parser.add_argument("--baseline-min-active-days", type=positive_int, default=3)
    parser.add_argument("--spike-multiple", type=positive_decimal_arg, default=Decimal("2"))
    parser.add_argument("--split-window-minutes", type=positive_int, default=1440)
    parser.add_argument("--night-start-hour", type=hour_value, default=22)
    parser.add_argument("--night-end-hour", type=hour_value, default=6)
    parser.add_argument("--time-bucket", choices=("day", "week", "month"), default="day")
    parser.add_argument("--top", type=top_value, default=10)
    parser.add_argument("--self-test", action="store_true")
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    required = {
        "input": args.input,
        "period-start": args.period_start,
        "period-end": args.period_end,
        "date-field": args.date_field,
        "statuses": args.statuses,
        "category-kinds": args.category_kinds,
        "currency": args.currency,
        "statistical-unit": args.statistical_unit,
        "subject-field": args.subject_field,
        "dedup-mode": args.dedup_mode,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error("required arguments missing: " + ", ".join(missing))
    if (args.threshold is None) != (args.threshold_window is None):
        parser.error("--threshold and --threshold-window must be supplied together")
    if args.night_start_hour == args.night_end_hour:
        parser.error("night start and end hours must differ")


def write_report(path: Path, report: dict[str, Any]) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        try:
            result = run_self_test()
        except Exception as exc:
            print(
                json.dumps(
                    {"status": "error", "self_test": {"message": str(exc)}},
                    ensure_ascii=False,
                )
            )
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    validate_args(parser, args)
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None
    try:
        if output_path is not None and input_path.resolve() == output_path.resolve():
            raise ExpenseValidationError("input and output paths must differ")
        with input_path.open("r", encoding="utf-8-sig") as stream:
            records, summary = load_records(stream, args.dedup_mode)
        report = build_report(records, summary, args)
        if output_path is None:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            write_report(output_path, report)
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "output": str(output_path),
                    },
                    ensure_ascii=False,
                )
            )
    except (ExpenseValidationError, OSError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": "invalid_input", "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
