#!/usr/bin/env python3
"""Profile hourly activity and bounded temporal associations from event JSONL."""

from __future__ import annotations

import argparse
import bisect
import io
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Iterable, TextIO


REQUIRED_FIELDS = (
    "event_id",
    "timestamp",
    "payer_id",
    "receiver_id",
    "amount",
    "currency",
    "source_file",
    "source_sheet",
    "source_row",
)
SHARED_PARTY_PLACEHOLDERS = frozenset(
    {
        "UNKNOWN",
        "UNKNOWN-",
        "UNKNOWN_",
        "UNKNOWN:",
        "UNRESOLVED",
        "UNRESOLVED-",
        "UNRESOLVED_",
        "UNRESOLVED:",
        "MISSING",
        "MISSING-",
        "MISSING_",
        "MISSING:",
        "NULL",
        "NONE",
        "N/A",
        "NA",
        "CUST",
        "CUST-",
        "CUST_",
        "CUST:",
        "CUSTOMER",
        "CUSTOMER-",
        "CUSTOMER_",
        "CUSTOMER:",
        "ACCT",
        "ACCT-",
        "ACCT_",
        "ACCT:",
        "ACCOUNT",
        "ACCOUNT-",
        "ACCOUNT_",
        "ACCOUNT:",
        "PARTY",
        "PARTY-",
        "PARTY_",
        "PARTY:",
        "PAYER-",
        "PAYER_",
        "PAYER:",
        "RECEIVER-",
        "RECEIVER_",
        "RECEIVER:",
        "SUBJECT-",
        "SUBJECT_",
        "SUBJECT:",
    }
)
WINDOWS = ((1, "within_1h"), (6, "within_6h"), (24, "within_24h"))
RATIO_QUANTUM = Decimal("0.000001")
NODE_ORDER_DESCRIPTION = (
    "within_1h, within_6h, and within_24h association counts descending; "
    "event count descending; night count-share difference versus other nodes "
    "descending; night event count descending; node_id ascending"
)


class InputError(ValueError):
    """Raised when normalized event input is invalid."""


def exact_decimal_sum(left: Decimal, right: Decimal) -> Decimal:
    left_tuple = left.as_tuple()
    right_tuple = right.as_tuple()
    common_exponent = min(left_tuple.exponent, right_tuple.exponent)
    left_width = len(left_tuple.digits) + left_tuple.exponent - common_exponent
    right_width = len(right_tuple.digits) + right_tuple.exponent - common_exponent
    with localcontext() as context:
        context.prec = max(left_width, right_width) + 1
        return left + right


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: datetime
    payer_id: str
    receiver_id: str
    amount: Decimal
    currency: str
    source_file: str
    source_sheet: str
    source_row: int | str
    input_line: int
    payer_name: str | None = None
    receiver_name: str | None = None


@dataclass
class Stats:
    event_count: int = 0
    counts_by_currency: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    amounts_by_currency: dict[str, Decimal] = field(
        default_factory=lambda: defaultdict(lambda: Decimal("0"))
    )

    def add(self, event: Event) -> None:
        self.event_count += 1
        self.counts_by_currency[event.currency] += 1
        self.amounts_by_currency[event.currency] = exact_decimal_sum(
            self.amounts_by_currency[event.currency], event.amount
        )

    def merge(self, other: "Stats") -> None:
        self.event_count += other.event_count
        for currency, count in other.counts_by_currency.items():
            self.counts_by_currency[currency] += count
        for currency, amount in other.amounts_by_currency.items():
            self.amounts_by_currency[currency] = exact_decimal_sum(
                self.amounts_by_currency[currency], amount
            )

    def minus(self, other: "Stats") -> "Stats":
        result = Stats(event_count=self.event_count - other.event_count)
        for currency in set(self.counts_by_currency) | set(other.counts_by_currency):
            count = self.counts_by_currency.get(currency, 0) - other.counts_by_currency.get(
                currency, 0
            )
            if count:
                result.counts_by_currency[currency] = count
                result.amounts_by_currency[currency] = exact_decimal_sum(
                    self.amounts_by_currency.get(currency, Decimal("0")),
                    -other.amounts_by_currency.get(currency, Decimal("0")),
                )
        return result


@dataclass
class DirectionStats:
    total: Stats = field(default_factory=Stats)
    inflow: Stats = field(default_factory=Stats)
    outflow: Stats = field(default_factory=Stats)
    self_transfer: Stats = field(default_factory=Stats)

    def add(self, event: Event, direction: str) -> None:
        self.total.add(event)
        getattr(self, direction).add(event)


@dataclass
class NodeStats:
    totals: DirectionStats = field(default_factory=DirectionStats)
    hours: list[DirectionStats] = field(
        default_factory=lambda: [DirectionStats() for _ in range(24)]
    )
    night: DirectionStats = field(default_factory=DirectionStats)

    def add(self, event: Event, direction: str, is_night: bool) -> None:
        self.totals.add(event, direction)
        self.hours[event.timestamp.hour].add(event, direction)
        if is_night:
            self.night.add(event, direction)


@dataclass(frozen=True)
class Association:
    node_id: str
    inflow: Event
    outflow: Event
    delta: timedelta


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def ratio_value(numerator: int | Decimal, denominator: int | Decimal) -> Decimal | None:
    denominator_decimal = Decimal(denominator)
    if denominator_decimal == 0:
        return None
    return Decimal(numerator) / denominator_decimal


def ratio_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return decimal_text(value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP))


def amount_share_values(part: Stats, whole: Stats) -> dict[str, Decimal | None]:
    return {
        currency: ratio_value(
            part.amounts_by_currency.get(currency, Decimal("0")), total_amount
        )
        for currency, total_amount in sorted(whole.amounts_by_currency.items())
    }


def serialize_ratio_map(values: dict[str, Decimal | None]) -> dict[str, str | None]:
    return {currency: ratio_text(value) for currency, value in sorted(values.items())}


def serialize_stats(stats: Stats) -> dict[str, Any]:
    return {
        "event_count": stats.event_count,
        "counts_by_currency": {
            currency: stats.counts_by_currency[currency]
            for currency in sorted(stats.counts_by_currency)
        },
        "amounts_by_currency": {
            currency: decimal_text(stats.amounts_by_currency[currency])
            for currency in sorted(stats.amounts_by_currency)
        },
    }


def serialize_direction_stats(stats: DirectionStats) -> dict[str, Any]:
    return {
        "total": serialize_stats(stats.total),
        "inflow": serialize_stats(stats.inflow),
        "outflow": serialize_stats(stats.outflow),
        "self_transfer": serialize_stats(stats.self_transfer),
    }


def parse_clock(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise argparse.ArgumentTypeError("time must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("time must be between 00:00 and 23:59")
    return hour * 60 + minute


def clock_text(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def is_in_night(timestamp: datetime, start_minute: int, end_minute: int) -> bool:
    point = (
        ((timestamp.hour * 60 + timestamp.minute) * 60 + timestamp.second) * 1_000_000
        + timestamp.microsecond
    )
    start = start_minute * 60 * 1_000_000
    end = end_minute * 60 * 1_000_000
    if start < end:
        return start <= point < end
    return point >= start or point < end


def require_text(record: dict[str, Any], field_name: str, line_number: int) -> str:
    value = record[field_name]
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"line {line_number}: {field_name} must be a non-empty string")
    return value.strip()


def require_string(
    record: dict[str, Any], field_name: str, line_number: int, *, allow_empty: bool = False
) -> str:
    value = record[field_name]
    if not isinstance(value, str):
        raise InputError(f"line {line_number}: {field_name} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise InputError(f"line {line_number}: {field_name} must be a non-empty string")
    return value


def require_identifier(record: dict[str, Any], field_name: str, line_number: int) -> str:
    value = record[field_name]
    if isinstance(value, str):
        identifier = require_text(record, field_name, line_number)
        if (
            field_name in {"payer_id", "receiver_id"}
            and identifier.upper() in SHARED_PARTY_PLACEHOLDERS
        ):
            raise InputError(
                f"line {line_number}: {field_name} must not use a shared missing-party placeholder; use an observation-scoped unresolved ID or exclude the event"
            )
        return identifier
    if isinstance(value, bool):
        raise InputError(f"line {line_number}: {field_name} must be a string or integer")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral():
        return decimal_text(value.to_integral())
    raise InputError(f"line {line_number}: {field_name} must be a string or integer")


def optional_display_text(record: dict[str, Any], field_name: str) -> str | None:
    value = record.get(field_name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def parse_timestamp(value: str, line_number: int) -> datetime:
    if "T" not in value and " " not in value:
        raise InputError(f"line {line_number}: timestamp must include date and time")
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise InputError(
            f"line {line_number}: timestamp must be a valid ISO-8601 value"
        ) from exc
    return parsed


def parse_amount(value: Any, line_number: int) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise InputError(f"line {line_number}: amount must be a positive decimal")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise InputError(f"line {line_number}: amount must be a positive decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise InputError(f"line {line_number}: amount must be a positive decimal")
    return amount


def parse_source_row(value: Any, line_number: int) -> int | str:
    if isinstance(value, str):
        locator = value.strip()
        if locator:
            return locator
    if not isinstance(value, bool):
        try:
            row = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            row = Decimal("0")
        if row.is_finite() and row == row.to_integral() and row > 0:
            return int(row)
    raise InputError(
        f"line {line_number}: source_row must be a positive integer or locator"
    )


def reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant {value}")


def event_from_record(record: Any, line_number: int) -> Event:
    if not isinstance(record, dict):
        raise InputError(f"line {line_number}: each JSONL item must be an object")
    missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in record]
    if missing:
        raise InputError(
            f"line {line_number}: missing required fields: {', '.join(missing)}"
        )
    timestamp_text = require_text(record, "timestamp", line_number)
    return Event(
        event_id=require_identifier(record, "event_id", line_number),
        timestamp=parse_timestamp(timestamp_text, line_number),
        payer_id=require_identifier(record, "payer_id", line_number),
        receiver_id=require_identifier(record, "receiver_id", line_number),
        amount=parse_amount(record["amount"], line_number),
        currency=require_text(record, "currency", line_number).upper(),
        source_file=require_text(record, "source_file", line_number),
        source_sheet=require_string(
            record, "source_sheet", line_number, allow_empty=True
        ),
        source_row=parse_source_row(record["source_row"], line_number),
        input_line=line_number,
        payer_name=optional_display_text(record, "payer_name"),
        receiver_name=optional_display_text(record, "receiver_name"),
    )


def load_events(stream: TextIO) -> list[Event]:
    events: list[Event] = []
    event_lines: dict[str, int] = {}
    awareness: bool | None = None
    for line_number, line in enumerate(stream, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(
                line,
                parse_float=Decimal,
                parse_constant=reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise InputError(f"line {line_number}: invalid JSON: {exc}") from exc
        event = event_from_record(record, line_number)
        if event.event_id in event_lines:
            raise InputError(
                f"line {line_number}: duplicate event_id {event.event_id!r}; "
                f"first seen on line {event_lines[event.event_id]}"
            )
        event_lines[event.event_id] = line_number
        event_awareness = event.timestamp.utcoffset() is not None
        if awareness is None:
            awareness = event_awareness
        elif awareness != event_awareness:
            raise InputError(
                f"line {line_number}: timestamps cannot mix offset-aware and naive values"
            )
        events.append(event)
    return events


def event_sort_key(event: Event) -> tuple[Any, ...]:
    return (event.timestamp, event.input_line, event.event_id)


def event_ref(event: Event) -> dict[str, Any]:
    output = {
        "event_id": event.event_id,
        "timestamp": event.timestamp.isoformat(),
        "payer_id": event.payer_id,
        "receiver_id": event.receiver_id,
        "amount": decimal_text(event.amount),
        "currency": event.currency,
        "source_refs": [
            {
                "source_file": event.source_file,
                "source_sheet": event.source_sheet,
                "source_row": event.source_row,
            }
        ],
    }
    if event.payer_name is not None:
        output["payer_name"] = event.payer_name
    if event.receiver_name is not None:
        output["receiver_name"] = event.receiver_name
    return output


def timedelta_decimal_seconds(value: timedelta) -> Decimal:
    whole_seconds = value.days * 86_400 + value.seconds
    return Decimal(whole_seconds) + Decimal(value.microseconds) / Decimal(1_000_000)


def association_ref(rank: int, association: Association) -> dict[str, Any]:
    seconds = timedelta_decimal_seconds(association.delta)
    within_windows = [hours for hours, _ in WINDOWS if association.delta <= timedelta(hours=hours)]
    return {
        "rank": rank,
        "node_id": association.node_id,
        "delta_seconds": decimal_text(seconds),
        "delta_minutes": ratio_text(seconds / Decimal(60)),
        "within_windows_hours": within_windows,
        "inflow": event_ref(association.inflow),
        "outflow": event_ref(association.outflow),
    }


def association_rank(association: Association) -> tuple[Any, ...]:
    same_currency = association.inflow.currency == association.outflow.currency
    comparable_amount = (
        min(association.inflow.amount, association.outflow.amount)
        if same_currency
        else Decimal("0")
    )
    return (
        association.delta,
        association.inflow.currency,
        association.outflow.currency,
        0 if same_currency else 1,
        -comparable_amount,
        association.node_id,
        association.inflow.event_id,
        association.outflow.event_id,
    )


def find_associations(
    events: Iterable[Event], node_ids: Iterable[str], top_limit: int
) -> tuple[dict[str, dict[str, int]], dict[str, int], int, list[dict[str, Any]]]:
    inflows_by_node: dict[str, list[Event]] = defaultdict(list)
    outflows_by_node: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.payer_id == event.receiver_id:
            continue
        outflows_by_node[event.payer_id].append(event)
        inflows_by_node[event.receiver_id].append(event)

    global_counts = {label: 0 for _, label in WINDOWS}
    node_counts: dict[str, dict[str, int]] = {}
    candidates: list[Association] = []
    candidate_count = 0

    for node_id in sorted(node_ids):
        inflows = sorted(inflows_by_node.get(node_id, ()), key=event_sort_key)
        outflows = sorted(outflows_by_node.get(node_id, ()), key=event_sort_key)
        outflow_times = [event.timestamp for event in outflows]
        counts = {
            "explicit_inflow_count": len(inflows),
            "with_any_strictly_later_outflow_count": 0,
            **{label: 0 for _, label in WINDOWS},
        }
        for inflow in inflows:
            outflow_index = bisect.bisect_right(outflow_times, inflow.timestamp)
            if outflow_index >= len(outflows):
                continue
            counts["with_any_strictly_later_outflow_count"] += 1
            outflow = outflows[outflow_index]
            delta = outflow.timestamp - inflow.timestamp
            if delta > timedelta(hours=24):
                continue
            association = Association(node_id, inflow, outflow, delta)
            candidate_count += 1
            candidates.append(association)
            for hours, label in WINDOWS:
                if delta <= timedelta(hours=hours):
                    counts[label] += 1
                    global_counts[label] += 1
        node_counts[node_id] = counts

    candidates.sort(key=association_rank)
    selected = [
        association_ref(rank, association)
        for rank, association in enumerate(candidates[:top_limit], 1)
    ]
    return node_counts, global_counts, candidate_count, selected


def night_payload(night: Stats, total: Stats) -> dict[str, Any]:
    return {
        **serialize_stats(night),
        "count_share": ratio_text(ratio_value(night.event_count, total.event_count)),
        "amount_share_by_currency": serialize_ratio_map(
            amount_share_values(night, total)
        ),
    }


def node_night_payload(
    node: NodeStats, other_night: Stats, other_total: Stats
) -> dict[str, Any]:
    node_count_share = ratio_value(node.night.total.event_count, node.totals.total.event_count)
    other_count_share = ratio_value(other_night.event_count, other_total.event_count)
    node_amount_shares = amount_share_values(node.night.total, node.totals.total)
    other_amount_shares = amount_share_values(other_night, other_total)
    currencies = sorted(set(node_amount_shares) | set(other_amount_shares))
    differences = {
        currency: (
            node_amount_shares.get(currency) - other_amount_shares.get(currency)
            if node_amount_shares.get(currency) is not None
            and other_amount_shares.get(currency) is not None
            else None
        )
        for currency in currencies
    }
    count_difference = (
        node_count_share - other_count_share
        if node_count_share is not None and other_count_share is not None
        else None
    )
    return {
        "stats": serialize_direction_stats(node.night),
        "count_share": ratio_text(node_count_share),
        "amount_share_by_currency": serialize_ratio_map(node_amount_shares),
        "relative_to_other_nodes": {
            "other_nodes_event_count": other_total.event_count,
            "other_nodes_night_count_share": ratio_text(other_count_share),
            "count_share_difference": ratio_text(count_difference),
            "other_nodes_night_amount_share_by_currency": serialize_ratio_map(
                other_amount_shares
            ),
            "amount_share_difference_by_currency": serialize_ratio_map(differences),
        },
    }


def node_night_count_difference(
    node: NodeStats, all_node_night: Stats, all_node_total: Stats
) -> Decimal | None:
    other_night = all_node_night.minus(node.night.total)
    other_total = all_node_total.minus(node.totals.total)
    node_share = ratio_value(node.night.total.event_count, node.totals.total.event_count)
    other_share = ratio_value(other_night.event_count, other_total.event_count)
    if node_share is None or other_share is None:
        return None
    return node_share - other_share


def node_selection_rank(
    node_id: str,
    node: NodeStats,
    association_counts: dict[str, int],
    all_node_night: Stats,
    all_node_total: Stats,
) -> tuple[Any, ...]:
    night_difference = node_night_count_difference(
        node, all_node_night, all_node_total
    )
    return (
        -association_counts["within_1h"],
        -association_counts["within_6h"],
        -association_counts["within_24h"],
        -node.totals.total.event_count,
        0 if night_difference is not None else 1,
        -night_difference if night_difference is not None else Decimal("0"),
        -node.night.total.event_count,
        node_id,
    )


def node_source_samples(
    events: Iterable[Event], node_ids: Iterable[str], sample_limit: int
) -> dict[str, dict[str, Any]]:
    selected = set(node_ids)
    events_by_node: dict[str, list[tuple[Event, str]]] = defaultdict(list)
    for event in events:
        if event.payer_id == event.receiver_id:
            if event.payer_id in selected:
                events_by_node[event.payer_id].append((event, "self_transfer"))
            continue
        if event.payer_id in selected:
            events_by_node[event.payer_id].append((event, "outflow"))
        if event.receiver_id in selected:
            events_by_node[event.receiver_id].append((event, "inflow"))

    samples = {}
    for node_id in selected:
        node_events = sorted(
            events_by_node.get(node_id, ()),
            key=lambda item: (*event_sort_key(item[0]), item[1]),
        )
        selected_events = node_events[:sample_limit]
        samples[node_id] = {
            "order": "timestamp, input line, event_id",
            "total_event_count": len(node_events),
            "sample_limit": sample_limit,
            "returned_count": len(selected_events),
            "omitted_count": len(node_events) - len(selected_events),
            "items": [
                {"direction": direction, **event_ref(event)}
                for event, direction in selected_events
            ],
        }
    return samples


def analyze_events(
    events: list[Event],
    night_start: int,
    night_end: int,
    top_limit: int,
    sample_limit: int = 5,
) -> dict[str, Any]:
    if night_start == night_end:
        raise InputError("night-start and night-end must be different")
    if top_limit < 0:
        raise InputError("top must be zero or greater")
    if sample_limit < 0:
        raise InputError("sample must be zero or greater")

    global_total = Stats()
    global_hours = [Stats() for _ in range(24)]
    global_night = Stats()
    node_stats: dict[str, NodeStats] = {}
    currencies: set[str] = set()

    for event in events:
        currencies.add(event.currency)
        event_is_night = is_in_night(event.timestamp, night_start, night_end)
        global_total.add(event)
        global_hours[event.timestamp.hour].add(event)
        if event_is_night:
            global_night.add(event)

        if event.payer_id == event.receiver_id:
            node_stats.setdefault(event.payer_id, NodeStats()).add(
                event, "self_transfer", event_is_night
            )
        else:
            node_stats.setdefault(event.payer_id, NodeStats()).add(
                event, "outflow", event_is_night
            )
            node_stats.setdefault(event.receiver_id, NodeStats()).add(
                event, "inflow", event_is_night
            )

    all_node_total = Stats()
    all_node_night = Stats()
    for node in node_stats.values():
        all_node_total.merge(node.totals.total)
        all_node_night.merge(node.night.total)

    node_associations, global_associations, candidate_count, candidates = (
        find_associations(events, node_stats, top_limit)
    )
    selected_node_ids = sorted(
        node_stats,
        key=lambda node_id: node_selection_rank(
            node_id,
            node_stats[node_id],
            node_associations[node_id],
            all_node_night,
            all_node_total,
        ),
    )[:top_limit]
    source_samples = node_source_samples(events, selected_node_ids, sample_limit)
    nodes = []
    for selection_rank, node_id in enumerate(selected_node_ids, 1):
        node = node_stats[node_id]
        other_total = all_node_total.minus(node.totals.total)
        other_night = all_node_night.minus(node.night.total)
        nodes.append(
            {
                "selection_rank": selection_rank,
                "node_id": node_id,
                "totals": serialize_direction_stats(node.totals),
                "by_hour": [
                    {"hour": hour, **serialize_direction_stats(node.hours[hour])}
                    for hour in range(24)
                ],
                "night": node_night_payload(node, other_night, other_total),
                "temporal_association_counts": node_associations[node_id],
                "source_samples": source_samples[node_id],
            }
        )

    total_node_count = len(node_stats)
    output_node_count = len(nodes)
    nodes_with_associations = sum(
        counts["within_24h"] > 0 for counts in node_associations.values()
    )
    output_source_sample_count = sum(
        sample["returned_count"] for sample in source_samples.values()
    )

    return {
        "parameters": {
            "night_start": clock_text(night_start),
            "night_end": clock_text(night_end),
            "night_interval": "start-inclusive and end-exclusive in each event timestamp's local clock",
            "association_windows_hours": [hours for hours, _ in WINDOWS],
            "top_limit": top_limit,
            "source_sample_limit_per_node": sample_limit,
            "node_output_order": NODE_ORDER_DESCRIPTION,
            "other_nodes_baseline": "pooled node-event observations excluding the current node",
            "decimal_encoding": "amounts and ratios are decimal strings; counts are integers",
        },
        "scope": {
            "event_count": len(events),
            "node_count": total_node_count,
            "total_node_count": total_node_count,
            "output_node_count": output_node_count,
            "omitted_node_count": total_node_count - output_node_count,
            "nodes_with_within_24h_associations": nodes_with_associations,
            "output_source_sample_count": output_source_sample_count,
            "currencies": sorted(currencies),
        },
        "global": {
            "totals": serialize_stats(global_total),
            "by_hour": [
                {"hour": hour, **serialize_stats(global_hours[hour])}
                for hour in range(24)
            ],
            "night": night_payload(global_night, global_total),
        },
        "nodes": nodes,
        "temporal_associations": {
            "pairing_rule": "For each non-self inflow, pair only the first outflow from the same node whose timestamp is strictly later; input order breaks ties at the same earliest timestamp. Count the pair cumulatively within 1, 6, and 24 hours.",
            "interpretation": "Each result is a temporal association only. It does not establish that an outflow is the same funds as an inflow.",
            "counts": global_associations,
            "candidate_count_within_24h": candidate_count,
            "returned_candidate_count": len(candidates),
            "omitted_candidate_count": candidate_count - len(candidates),
            "output_limit": top_limit,
            "top_candidate_order": "Shortest time difference first. For equal gaps within the same currency pair, the larger smaller-leg amount comes first; currencies, node ID, and event IDs provide deterministic remaining order without comparing amounts across currencies.",
            "top_candidates": candidates,
        },
    }


def make_test_record(
    event_id: str,
    timestamp: str,
    payer_id: str,
    receiver_id: str,
    amount: str,
    currency: str,
    source_row: int,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "payer_id": payer_id,
        "receiver_id": receiver_id,
        "amount": amount,
        "currency": currency,
        "source_file": "events.jsonl",
        "source_sheet": "Sheet1",
        "source_row": source_row,
    }


def records_to_events(records: list[dict[str, Any]]) -> list[Event]:
    content = "\n".join(json.dumps(record) for record in records)
    return load_events(io.StringIO(content))


def run_self_test() -> int:
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    records = [
        make_test_record("e1", "2024-01-01T19:30:00", "A", "B", "0.1", "CNY", 1),
        make_test_record("e0", "2024-01-01T19:30:00", "B", "C", "999", "CNY", 2),
        make_test_record("e2", "2024-01-01T19:45:00", "B", "D", "0.2", "CNY", 3),
        make_test_record("e3", "2024-01-01T20:00:00", "E", "B", "100", "CNY", 4),
        make_test_record("e4", "2024-01-01T22:00:00", "B", "F", "200", "CNY", 5),
        make_test_record("e5", "2024-01-02T05:00:00", "G", "B", "300", "USD", 6),
        make_test_record("e6", "2024-01-02T12:00:00", "B", "H", "400", "USD", 7),
        make_test_record("e7", "2024-01-02T10:00:00", "X", "Y", "0.3", "CNY", 8),
    ]
    events = records_to_events(records)
    report = analyze_events(events, parse_clock("20:00"), parse_clock("06:00"), 2)
    check(report["scope"]["event_count"] == 8, "event count")
    check(len(report["global"]["by_hour"]) == 24, "global 24-hour buckets")
    check(report["global"]["by_hour"][19]["event_count"] == 3, "hour count")
    check(
        report["global"]["totals"]["amounts_by_currency"]["CNY"] == "1299.6",
        "exact Decimal aggregation",
    )
    check(report["global"]["night"]["event_count"] == 3, "wrapping night window")
    check(
        report["scope"]["total_node_count"] > report["scope"]["output_node_count"]
        == 2,
        "node output top bound",
    )

    node_b = next(node for node in report["nodes"] if node["node_id"] == "B")
    counts = node_b["temporal_association_counts"]
    check(counts["within_1h"] == 1, "one-hour association count")
    check(counts["within_6h"] == 2, "six-hour association count")
    check(counts["within_24h"] == 3, "24-hour association count")
    check(
        node_b["night"]["relative_to_other_nodes"]["count_share_difference"]
        == "0.095238",
        "night share difference",
    )
    candidates = report["temporal_associations"]["top_candidates"]
    check(len(candidates) == 2, "bounded top candidates")
    check(candidates[0]["inflow"]["event_id"] == "e1", "strict-later pairing")
    check(candidates[0]["outflow"]["event_id"] == "e2", "first later outflow")
    check(
        candidates[0]["outflow"]["source_refs"][0]["source_row"] == 3,
        "source location preservation",
    )

    custom = analyze_events(events, parse_clock("22:00"), parse_clock("05:00"), 1)
    check(custom["global"]["night"]["event_count"] == 1, "custom night boundary")

    ranking_records = [
        make_test_record("r1", "2024-02-01T10:00:00", "P", "N", "10", "CNY", 1),
        make_test_record("r2", "2024-02-01T10:10:00", "N", "Q", "9", "CNY", 2),
        make_test_record("r3", "2024-02-01T11:00:00", "R", "M", "100", "CNY", 3),
        make_test_record("r4", "2024-02-01T11:10:00", "M", "S", "80", "CNY", 4),
    ]
    ranking_report = analyze_events(
        records_to_events(ranking_records), parse_clock("20:00"), parse_clock("06:00"), 1
    )
    check(
        ranking_report["temporal_associations"]["top_candidates"][0]["node_id"] == "M",
        "larger amount tie-break",
    )

    large_amount_records = [
        make_test_record(
            "l1",
            "2024-03-01T09:00:00",
            "U",
            "V",
            "100000000000000000000000000000",
            "CNY",
            1,
        ),
        make_test_record("l2", "2024-03-01T10:00:00", "U", "V", "0.1", "CNY", 2),
    ]
    large_amount_report = analyze_events(
        records_to_events(large_amount_records),
        parse_clock("20:00"),
        parse_clock("06:00"),
        1,
    )
    check(
        large_amount_report["global"]["totals"]["amounts_by_currency"]["CNY"]
        == "100000000000000000000000000000.1",
        "large Decimal aggregation",
    )

    flexible_record = make_test_record(
        "unused", "2024-04-01T09:00:00", "unused", "unused", "1", "usd", 1
    )
    flexible_record.update(
        {
            "event_id": 101,
            "payer_id": 201,
            "receiver_id": 202,
            "source_sheet": "",
            "source_row": "record-7",
            "payer_name": "Payer name",
            "receiver_name": "Receiver name",
            "summary": "optional summary",
            "transaction_code": "optional code",
            "adapter_extension": {"ignored": True},
        }
    )
    flexible_event = records_to_events([flexible_record])[0]
    check(
        flexible_event.event_id == "101"
        and flexible_event.currency == "USD"
        and flexible_event.source_row == "record-7",
        "shared normalized input forms",
    )
    flexible_report = analyze_events(
        [flexible_event], parse_clock("20:00"), parse_clock("06:00"), 1, 1
    )
    flexible_sample = flexible_report["nodes"][0]["source_samples"]["items"][0]
    check(
        flexible_sample["payer_name"] == "Payer name"
        and flexible_sample["receiver_name"] == "Receiver name",
        "known names retained while unknown extensions are ignored",
    )

    many_node_records = []
    start = datetime(2024, 5, 1, 0, 0)
    for index in range(120):
        many_node_records.append(
            make_test_record(
                f"many-{index:03d}",
                (start + timedelta(minutes=index)).isoformat(),
                f"leaf-{index:03d}",
                "hub",
                "1",
                "CNY",
                index + 1,
            )
        )
    many_events = records_to_events(many_node_records)
    many_report = analyze_events(
        many_events, parse_clock("20:00"), parse_clock("06:00"), 3, 2
    )
    check(many_report["scope"]["total_node_count"] == 121, "100+ total nodes")
    check(
        many_report["scope"]["output_node_count"] == len(many_report["nodes"]) == 3,
        "top three nodes only",
    )
    check(
        many_report["scope"]["omitted_node_count"] == 118,
        "omitted node coverage count",
    )
    check(
        all(
            node["source_samples"]["returned_count"] <= 2
            for node in many_report["nodes"]
        ),
        "per-node source sample bound",
    )
    check(
        sum(bucket["event_count"] for bucket in many_report["global"]["by_hour"])
        == 120,
        "global hourly coverage retained",
    )
    repeated_report = analyze_events(
        many_events, parse_clock("20:00"), parse_clock("06:00"), 3, 2
    )
    check(
        [node["node_id"] for node in many_report["nodes"]]
        == [node["node_id"] for node in repeated_report["nodes"]],
        "stable node ordering",
    )
    rendered_size = len(
        json.dumps(many_report, ensure_ascii=False, indent=2).encode("utf-8")
    )
    check(rendered_size < 200_000, "bounded output size for 100+ nodes")

    shared_placeholder_records = [
        make_test_record(
            "placeholder-in",
            "2024-06-01T10:00:00",
            "stable-a",
            "cust-",
            "10",
            "CNY",
            1,
        ),
        make_test_record(
            "placeholder-out",
            "2024-06-01T10:00:01",
            "cust-",
            "stable-b",
            "9",
            "CNY",
            2,
        ),
    ]
    placeholder_rejected = False
    try:
        placeholder_events = records_to_events(shared_placeholder_records)
        placeholder_report = analyze_events(
            placeholder_events, parse_clock("20:00"), parse_clock("06:00"), 3
        )
        placeholder_candidate_count = placeholder_report["temporal_associations"][
            "candidate_count_within_24h"
        ]
    except InputError:
        placeholder_rejected = True
        placeholder_candidate_count = 0
    check(
        placeholder_rejected and placeholder_candidate_count == 0,
        "shared missing-party placeholder cannot create an association",
    )

    scoped_unresolved_records = [
        make_test_record(
            "scoped-in",
            "2024-06-01T11:00:00",
            "stable-a",
            "unresolved:scoped-in:receiver",
            "10",
            "CNY",
            3,
        ),
        make_test_record(
            "scoped-out",
            "2024-06-01T11:00:01",
            "unresolved:scoped-out:payer",
            "stable-b",
            "9",
            "CNY",
            4,
        ),
    ]
    scoped_report = analyze_events(
        records_to_events(scoped_unresolved_records),
        parse_clock("20:00"),
        parse_clock("06:00"),
        3,
    )
    check(
        scoped_report["temporal_associations"]["candidate_count_within_24h"] == 0,
        "observation-scoped unresolved ids do not connect across events",
    )

    duplicate_records = records[:1] + records[:1]
    duplicate_failed = False
    try:
        records_to_events(duplicate_records)
    except InputError:
        duplicate_failed = True
    check(duplicate_failed, "duplicate event IDs must fail")
    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile normalized event JSONL by hour, night share, and temporal association."
    )
    parser.add_argument("input", nargs="?", help="input JSONL path, or - for stdin")
    parser.add_argument("--input", dest="input_option", help="input JSONL path, or - for stdin")
    parser.add_argument("-o", "--output", default="-", help="output JSON path, or - for stdout")
    parser.add_argument("--night-start", type=parse_clock, default=parse_clock("20:00"))
    parser.add_argument("--night-end", type=parse_clock, default=parse_clock("06:00"))
    parser.add_argument(
        "--top",
        "--top-candidates",
        dest="top",
        type=int,
        default=20,
        help="maximum focus nodes and temporal candidates to emit",
    )
    parser.add_argument(
        "--sample",
        "--source-sample",
        dest="sample",
        type=int,
        default=5,
        help="maximum chronological source event samples per emitted node",
    )
    parser.add_argument("--self-test", action="store_true", help="run built-in checks and exit")
    return parser


def open_input(path: str) -> tuple[TextIO, bool]:
    if path == "-":
        return sys.stdin, False
    return Path(path).open("r", encoding="utf-8-sig"), True


def write_report(report: dict[str, Any], output: str) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output == "-":
        sys.stdout.write(rendered)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        try:
            checks = run_self_test()
        except AssertionError as exc:
            print(json.dumps({"self_test": "failed", "reason": str(exc)}), file=sys.stderr)
            return 1
        print(json.dumps({"self_test": "passed", "checks": checks}))
        return 0

    if args.input and args.input_option:
        parser.error("provide input either positionally or with --input, not both")
    input_path = args.input_option or args.input
    if not input_path:
        parser.error("an input JSONL path is required unless --self-test is used")

    stream: TextIO | None = None
    should_close = False
    try:
        stream, should_close = open_input(input_path)
        events = load_events(stream)
        report = analyze_events(
            events, args.night_start, args.night_end, args.top, args.sample
        )
        write_report(report, args.output)
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if should_close and stream is not None:
            stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
