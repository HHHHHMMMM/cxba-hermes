#!/usr/bin/env python3
"""Search bounded temporal motifs in a normalized JSONL event table.

The script is read-only: it reads JSONL from a file or stdin and writes one JSON
document to stdout. Results describe temporal associations only. They do not
assert that value transferred by one event is identical to value in another.
"""

from __future__ import annotations

import argparse
import bisect
import heapq
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence, TextIO


REQUIRED_FIELDS = {
    "event_id",
    "timestamp",
    "payer_id",
    "receiver_id",
    "amount",
    "currency",
    "source_file",
    "source_sheet",
    "source_row",
}
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
NOTICE = (
    "Temporal association only; candidates do not establish identity, "
    "continuity, or tracing of funds."
)
RAPID_PASS_THROUGH_MIN_CONTINUITY = Decimal("0.99")


class EventValidationError(ValueError):
    """Raised when the input is not a valid normalized event table."""


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
class Event:
    event_id: str
    timestamp: datetime
    payer_id: str
    receiver_id: str
    amount: Decimal
    currency: str
    source_refs: list[SourceRef]
    input_line: int
    business_key: str | None = None
    event_ids: list[str] = field(default_factory=list)
    payer_names: list[str] = field(default_factory=list)
    receiver_names: list[str] = field(default_factory=list)

    def merge_signature(self) -> tuple[Any, ...]:
        return (
            self.timestamp,
            self.payer_id,
            self.receiver_id,
            self.amount,
            self.currency,
        )


@dataclass(frozen=True)
class LoadSummary:
    input_rows: int
    canonical_events: int
    keyed_rows: int
    unkeyed_rows: int
    merged_groups: int
    merged_rows: int


class BoundedTop:
    """Retain only the highest-scoring candidates while counting all matches."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self._sequence = 0
        self._heap: list[tuple[tuple[Any, ...], int, dict[str, Any]]] = []

    def add(self, score: tuple[Any, ...], candidate: dict[str, Any]) -> None:
        self.total += 1
        self._sequence += 1
        item = (score, -self._sequence, candidate)
        if len(self._heap) < self.limit:
            heapq.heappush(self._heap, item)
        elif item[:2] > self._heap[0][:2]:
            heapq.heapreplace(self._heap, item)

    def values(self) -> list[dict[str, Any]]:
        ordered = sorted(self._heap, key=lambda item: item[:2], reverse=True)
        return [candidate for _, _, candidate in ordered]

    def result(self, **extra: Any) -> dict[str, Any]:
        values = self.values()
        return {
            "candidate_count": self.total,
            "returned_count": len(values),
            "top_candidates": values,
            **extra,
        }


class BestRouteTop:
    """Keep the strongest candidate per graph route before applying the top bound."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self._best: dict[tuple[str, ...], tuple[tuple[Any, ...], dict[str, Any]]] = {}

    def add(
        self,
        route: tuple[str, ...],
        score: tuple[Any, ...],
        candidate: dict[str, Any],
    ) -> None:
        self.total += 1
        existing = self._best.get(route)
        if existing is None or score > existing[0]:
            self._best[route] = (score, candidate)

    def result(self, **extra: Any) -> dict[str, Any]:
        ordered = sorted(self._best.values(), key=lambda item: item[0], reverse=True)
        values = [candidate for _, candidate in ordered[: self.limit]]
        return {
            "candidate_count": self.total,
            "unique_route_count": len(self._best),
            "returned_count": len(values),
            "top_candidates": values,
            **extra,
        }


class TimedIndex:
    def __init__(self, events: Iterable[Event], key: str) -> None:
        grouped: dict[str, list[Event]] = {}
        for event in events:
            grouped.setdefault(getattr(event, key), []).append(event)
        self.events: dict[str, list[Event]] = {}
        self.times: dict[str, list[datetime]] = {}
        for identity, identity_events in grouped.items():
            identity_events.sort(key=event_order)
            self.events[identity] = identity_events
            self.times[identity] = [event.timestamp for event in identity_events]

    def between(
        self,
        identity: str,
        after: datetime,
        through: datetime,
    ) -> Sequence[Event]:
        events = self.events.get(identity, ())
        if not events:
            return ()
        times = self.times[identity]
        start = bisect.bisect_right(times, after)
        end = bisect.bisect_right(times, through)
        return events[start:end]

    def before(
        self,
        identity: str,
        since: datetime,
        before: datetime,
    ) -> Sequence[Event]:
        events = self.events.get(identity, ())
        if not events:
            return ()
        times = self.times[identity]
        start = bisect.bisect_left(times, since)
        end = bisect.bisect_left(times, before)
        return events[start:end]


def event_order(event: Event) -> tuple[Any, ...]:
    return (event.timestamp, event.input_line, event.event_id)


def require_text(value: Any, field_name: str, line_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(
            f"line {line_number}: {field_name} must be a non-empty string"
        )
    return value.strip()


def require_string(
    value: Any,
    field_name: str,
    line_number: int,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise EventValidationError(f"line {line_number}: {field_name} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise EventValidationError(
            f"line {line_number}: {field_name} must be a non-empty string"
        )
    return normalized


def optional_text(value: Any, field_name: str, line_number: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return require_text(value, field_name, line_number)


def require_identifier(value: Any, field_name: str, line_number: int) -> str:
    if isinstance(value, bool):
        raise EventValidationError(
            f"line {line_number}: {field_name} must be a string or integer"
        )
    if isinstance(value, str):
        identifier = require_text(value, field_name, line_number)
        if (
            field_name in {"payer_id", "receiver_id"}
            and identifier.upper() in SHARED_PARTY_PLACEHOLDERS
        ):
            raise EventValidationError(
                f"line {line_number}: {field_name} must not use a shared missing-party placeholder; use an observation-scoped unresolved ID or exclude the event"
            )
        return identifier
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral():
        return str(value.quantize(Decimal(1)))
    raise EventValidationError(
        f"line {line_number}: {field_name} must be a string or integer"
    )


def optional_identifier(value: Any, field_name: str, line_number: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return require_identifier(value, field_name, line_number)


def require_decimal(value: Any, line_number: int) -> Decimal:
    if isinstance(value, bool):
        raise EventValidationError(f"line {line_number}: amount must be a positive decimal")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EventValidationError(
            f"line {line_number}: amount must be a positive decimal"
        ) from None
    if not amount.is_finite() or amount <= 0:
        raise EventValidationError(f"line {line_number}: amount must be a positive decimal")
    return amount


def require_source_row(value: Any, line_number: int) -> int | str:
    if isinstance(value, str):
        locator = value.strip()
        if not locator:
            raise EventValidationError(
                f"line {line_number}: source_row must be a positive integer or locator"
            )
        return locator
    if isinstance(value, bool):
        raise EventValidationError(
            f"line {line_number}: source_row must be a positive integer or locator"
        )
    try:
        row = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise EventValidationError(
            f"line {line_number}: source_row must be a positive integer or locator"
        ) from None
    if not row.is_finite() or row != row.to_integral() or row <= 0:
        raise EventValidationError(
            f"line {line_number}: source_row must be a positive integer or locator"
        )
    return int(row)


def require_timestamp(value: Any, line_number: int) -> datetime:
    text = require_text(value, "timestamp", line_number)
    if "T" not in text and " " not in text:
        raise EventValidationError(
            f"line {line_number}: timestamp must include date and time"
        )
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        raise EventValidationError(
            f"line {line_number}: timestamp must be ISO 8601"
        ) from None
    if timestamp.tzinfo is not None:
        offset = timestamp.utcoffset()
        if offset is None:
            raise EventValidationError(
                f"line {line_number}: timestamp has an invalid UTC offset"
            )
        timestamp = timestamp.astimezone(timezone.utc)
    return timestamp


def parse_event(raw: Any, line_number: int) -> Event:
    if not isinstance(raw, dict):
        raise EventValidationError(f"line {line_number}: each JSON value must be an object")
    missing = sorted(REQUIRED_FIELDS - raw.keys())
    if missing:
        raise EventValidationError(
            f"line {line_number}: missing required fields: {', '.join(missing)}"
        )

    event_id = require_identifier(raw["event_id"], "event_id", line_number)
    payer_name = optional_text(raw.get("payer_name"), "payer_name", line_number)
    receiver_name = optional_text(raw.get("receiver_name"), "receiver_name", line_number)
    event = Event(
        event_id=event_id,
        event_ids=[event_id],
        timestamp=require_timestamp(raw["timestamp"], line_number),
        payer_id=require_identifier(raw["payer_id"], "payer_id", line_number),
        receiver_id=require_identifier(raw["receiver_id"], "receiver_id", line_number),
        amount=require_decimal(raw["amount"], line_number),
        currency=require_text(raw["currency"], "currency", line_number).upper(),
        source_refs=[
            SourceRef(
                source_file=require_text(raw["source_file"], "source_file", line_number),
                source_sheet=require_string(
                    raw["source_sheet"],
                    "source_sheet",
                    line_number,
                    allow_empty=True,
                ),
                source_row=require_source_row(raw["source_row"], line_number),
            )
        ],
        input_line=line_number,
        business_key=optional_identifier(raw.get("business_key"), "business_key", line_number),
    )
    if payer_name:
        event.payer_names.append(payer_name)
    if receiver_name:
        event.receiver_names.append(receiver_name)
    return event


def merge_event(existing: Event, incoming: Event) -> None:
    if existing.merge_signature() != incoming.merge_signature():
        raise EventValidationError(
            "lines "
            f"{existing.input_line} and {incoming.input_line}: business_key "
            "matches but timestamp, parties, amount, or currency conflicts"
        )
    existing.event_ids.extend(incoming.event_ids)
    existing.source_refs.extend(incoming.source_refs)
    for name in incoming.payer_names:
        if name not in existing.payer_names:
            existing.payer_names.append(name)
    for name in incoming.receiver_names:
        if name not in existing.receiver_names:
            existing.receiver_names.append(name)


def load_events(stream: TextIO) -> tuple[list[Event], LoadSummary]:
    canonical: list[Event] = []
    keyed: dict[str, Event] = {}
    input_rows = 0
    keyed_rows = 0
    unkeyed_rows = 0
    key_counts: dict[str, int] = {}
    timezone_mode: bool | None = None

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
            raise EventValidationError(f"line {line_number}: invalid JSON: {exc}") from None
        event = parse_event(raw, line_number)
        event_is_aware = event.timestamp.tzinfo is not None
        if timezone_mode is None:
            timezone_mode = event_is_aware
        elif timezone_mode != event_is_aware:
            raise EventValidationError(
                f"line {line_number}: timestamps must be consistently timezone-aware or naive"
            )

        if event.business_key is None:
            unkeyed_rows += 1
            canonical.append(event)
            continue

        keyed_rows += 1
        key_counts[event.business_key] = key_counts.get(event.business_key, 0) + 1
        existing = keyed.get(event.business_key)
        if existing is None:
            keyed[event.business_key] = event
            canonical.append(event)
        else:
            merge_event(existing, event)

    if not canonical:
        raise EventValidationError("input contains no event rows")
    canonical.sort(key=event_order)
    merged_groups = sum(1 for count in key_counts.values() if count > 1)
    return canonical, LoadSummary(
        input_rows=input_rows,
        canonical_events=len(canonical),
        keyed_rows=keyed_rows,
        unkeyed_rows=unkeyed_rows,
        merged_groups=merged_groups,
        merged_rows=input_rows - len(canonical),
    )


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def timestamp_text(value: datetime) -> str:
    text = value.isoformat()
    return text[:-6] + "Z" if value.tzinfo is not None and text.endswith("+00:00") else text


def event_output(event: Event, sample_limit: int = 5) -> dict[str, Any]:
    output: dict[str, Any] = {
        "event_id": event.event_id,
        "timestamp": timestamp_text(event.timestamp),
        "payer_id": event.payer_id,
        "receiver_id": event.receiver_id,
        "amount": decimal_text(event.amount),
        "currency": event.currency,
        "source_refs": [source.as_dict() for source in event.source_refs[:sample_limit]],
        "source_refs_omitted": max(0, len(event.source_refs) - sample_limit),
    }
    if len(event.event_ids) > 1:
        output["event_ids"] = event.event_ids[:sample_limit]
        output["event_ids_omitted"] = max(0, len(event.event_ids) - sample_limit)
    if event.business_key is not None:
        output["business_key"] = event.business_key
    if event.payer_names:
        output["payer_names"] = event.payer_names[:sample_limit]
        output["payer_names_omitted"] = max(0, len(event.payer_names) - sample_limit)
    if event.receiver_names:
        output["receiver_names"] = event.receiver_names[:sample_limit]
        output["receiver_names_omitted"] = max(
            0, len(event.receiver_names) - sample_limit
        )
    return output


def elapsed_seconds(first: Event, last: Event) -> int | float:
    seconds = (last.timestamp - first.timestamp).total_seconds()
    return int(seconds) if seconds.is_integer() else seconds


def totals_by_currency(events: Iterable[Event]) -> dict[str, str]:
    totals: dict[str, Decimal] = {}
    for event in events:
        totals[event.currency] = totals.get(event.currency, Decimal(0)) + event.amount
    return {currency: decimal_text(totals[currency]) for currency in sorted(totals)}


def amount_continuity(first: Event, second: Event) -> dict[str, Any]:
    difference = second.amount - first.amount
    absolute_difference = abs(difference)
    larger = max(first.amount, second.amount)
    ratio = min(first.amount, second.amount) / larger
    return {
        "same_currency": first.currency == second.currency,
        "amount_difference": decimal_text(difference),
        "absolute_amount_difference": decimal_text(absolute_difference),
        "continuity_ratio": decimal_text(ratio),
        "retained_percentage": decimal_text(second.amount / first.amount * Decimal(100)),
        "exact_amount": absolute_difference == 0,
    }


def bounded_evidence(events: Sequence[Event], limit: int) -> tuple[list[Event], int]:
    ordered = sorted(events, key=lambda event: (-event.amount, event.timestamp, event.input_line))
    returned = ordered[:limit]
    return returned, len(ordered) - len(returned)


def search_convergence(
    events: Sequence[Event],
    windows: Sequence[int],
    min_payers: int,
    top: int,
    max_evidence_events: int,
    sample_limit: int = 5,
) -> list[dict[str, Any]]:
    incoming = TimedIndex(events, "receiver_id")
    results: list[dict[str, Any]] = []
    for window in windows:
        collector = BoundedTop(top)
        delta = timedelta(minutes=window)
        for outgoing in events:
            eligible = list(
                incoming.before(
                    outgoing.payer_id,
                    outgoing.timestamp - delta,
                    outgoing.timestamp,
                )
            )
            payers = {event.payer_id for event in eligible}
            if len(payers) < min_payers:
                continue
            evidence, omitted = bounded_evidence(eligible, max_evidence_events)
            first_time = min(event.timestamp for event in eligible)
            candidate = {
                "hub_id": outgoing.payer_id,
                "distinct_payer_count": len(payers),
                "inbound_event_count": len(eligible),
                "inbound_totals_by_currency": totals_by_currency(eligible),
                "elapsed_seconds": (outgoing.timestamp - first_time).total_seconds(),
                "inbound_events": [
                    event_output(event, sample_limit) for event in evidence
                ],
                "inbound_events_omitted": omitted,
                "outgoing_event": event_output(outgoing, sample_limit),
            }
            score = (len(payers), len(eligible), -candidate["elapsed_seconds"])
            collector.add(score, candidate)
        results.append(collector.result(window_minutes=window, search_truncated=False))
    return results


def search_returns(
    events: Sequence[Event],
    windows: Sequence[int],
    top: int,
    max_expansions: int,
    sample_limit: int = 5,
) -> list[dict[str, Any]]:
    outgoing = TimedIndex(events, "payer_id")
    results: list[dict[str, Any]] = []
    for window in windows:
        collector = BoundedTop(top)
        delta = timedelta(minutes=window)
        expansions = 0
        truncated = False
        for payment in events:
            possible_returns = outgoing.between(
                payment.receiver_id,
                payment.timestamp,
                payment.timestamp + delta,
            )
            for returned in possible_returns:
                if expansions >= max_expansions:
                    truncated = True
                    break
                expansions += 1
                if returned.receiver_id != payment.payer_id:
                    continue
                elapsed = elapsed_seconds(payment, returned)
                candidate = {
                    "original_payer_id": payment.payer_id,
                    "intermediate_id": payment.receiver_id,
                    "hop_count": 2,
                    "elapsed_seconds": elapsed,
                    "hops": [
                        event_output(payment, sample_limit),
                        event_output(returned, sample_limit),
                    ],
                }
                collector.add((-float(elapsed),), candidate)
            if truncated:
                break
        results.append(
            collector.result(
                window_minutes=window,
                expansions=expansions,
                expansion_limit=max_expansions,
                search_truncated=truncated,
            )
        )
    return results


def search_rapid_pass_through(
    events: Sequence[Event],
    windows: Sequence[int],
    top: int,
    max_expansions: int,
    sample_limit: int = 5,
) -> list[dict[str, Any]]:
    outgoing = TimedIndex(events, "payer_id")
    results: list[dict[str, Any]] = []
    for window in windows:
        amount_collector = BestRouteTop(top)
        amount_time_collector = BestRouteTop(top * 2)
        delta = timedelta(minutes=window)
        expansions = 0
        truncated = False
        for incoming in events:
            if incoming.payer_id == incoming.receiver_id:
                continue
            for forwarded in outgoing.between(
                incoming.receiver_id,
                incoming.timestamp,
                incoming.timestamp + delta,
            ):
                if expansions >= max_expansions:
                    truncated = True
                    break
                expansions += 1
                if (
                    forwarded.payer_id == forwarded.receiver_id
                    or forwarded.currency != incoming.currency
                ):
                    continue
                continuity = amount_continuity(incoming, forwarded)
                continuity_ratio = Decimal(continuity["continuity_ratio"])
                if continuity_ratio < RAPID_PASS_THROUGH_MIN_CONTINUITY:
                    continue
                elapsed = elapsed_seconds(incoming, forwarded)
                amount_per_elapsed_second = min(
                    incoming.amount, forwarded.amount
                ) / Decimal(max(elapsed, 1))
                candidate = {
                    "intermediate_id": incoming.receiver_id,
                    "elapsed_seconds": elapsed,
                    "amount_per_elapsed_second": decimal_text(
                        amount_per_elapsed_second
                    ),
                    **continuity,
                    "incoming_event": event_output(incoming, sample_limit),
                    "outgoing_event": event_output(forwarded, sample_limit),
                }
                route = (
                    incoming.payer_id,
                    incoming.receiver_id,
                    forwarded.receiver_id,
                    incoming.currency,
                )
                amount_collector.add(
                    route,
                    (
                        min(incoming.amount, forwarded.amount),
                        int(continuity["exact_amount"]),
                        continuity_ratio,
                        -float(elapsed),
                    ),
                    candidate,
                )
                amount_time_collector.add(
                    route,
                    (
                        amount_per_elapsed_second,
                        min(incoming.amount, forwarded.amount),
                        continuity_ratio,
                    ),
                    candidate,
                )
            if truncated:
                break
        amount_result = amount_collector.result(
            window_minutes=window,
            expansions=expansions,
            expansion_limit=max_expansions,
            search_truncated=truncated,
            minimum_continuity_ratio=decimal_text(
                RAPID_PASS_THROUGH_MIN_CONTINUITY
            ),
        )
        amount_time_result = amount_time_collector.result()
        amount_result["amount_time_returned_count"] = amount_time_result[
            "returned_count"
        ]
        amount_result["top_amount_time_candidates"] = amount_time_result[
            "top_candidates"
        ]
        results.append(amount_result)
    return results


def chain_candidate(path: Sequence[Event], sample_limit: int = 5) -> dict[str, Any]:
    transitions = [
        amount_continuity(first, second) for first, second in zip(path, path[1:])
    ]
    same_currency = len({event.currency for event in path}) == 1
    minimum_continuity = min(
        (Decimal(item["continuity_ratio"]) for item in transitions),
        default=Decimal(1),
    )
    minimum_hop_amount = min(event.amount for event in path)
    return {
        "start_id": path[0].payer_id,
        "end_id": path[-1].receiver_id,
        "hop_count": len(path),
        "elapsed_seconds": elapsed_seconds(path[0], path[-1]),
        "totals_by_currency": totals_by_currency(path),
        "same_currency": same_currency,
        "all_transitions_exact_amount": all(
            item["exact_amount"] for item in transitions
        ),
        "minimum_continuity_ratio": decimal_text(minimum_continuity),
        "minimum_hop_amount": decimal_text(minimum_hop_amount),
        "amount_transitions": transitions,
        "hops": [event_output(event, sample_limit) for event in path],
    }


def chain_score(candidate: dict[str, Any]) -> tuple[Any, ...]:
    continuity = Decimal(candidate["minimum_continuity_ratio"])
    return (
        int(candidate["same_currency"]),
        int(continuity >= RAPID_PASS_THROUGH_MIN_CONTINUITY),
        Decimal(candidate["minimum_hop_amount"]),
        int(candidate["all_transitions_exact_amount"]),
        continuity,
        -float(candidate["elapsed_seconds"]),
        candidate["hop_count"],
    )


def search_paths(
    events: Sequence[Event],
    windows: Sequence[int],
    top: int,
    max_expansions: int,
    max_hops: int,
    sample_limit: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outgoing = TimedIndex(events, "payer_id")
    path_results: list[dict[str, Any]] = []
    cycle_results: list[dict[str, Any]] = []

    for window in windows:
        paths = BestRouteTop(top)
        cycles = BestRouteTop(top)
        deadline_delta = timedelta(minutes=window)
        expansions = 0
        truncated = False

        def extend(path: list[Event], nodes: list[str], deadline: datetime) -> None:
            nonlocal expansions, truncated
            if truncated or len(path) >= max_hops:
                return
            for next_event in outgoing.between(
                path[-1].receiver_id,
                path[-1].timestamp,
                deadline,
            ):
                if expansions >= max_expansions:
                    truncated = True
                    return
                expansions += 1
                next_node = next_event.receiver_id
                if next_node == nodes[0]:
                    cycle_path = path + [next_event]
                    if len(cycle_path) >= 2:
                        candidate = chain_candidate(cycle_path, sample_limit)
                        cycles.add(tuple(nodes + [next_node]), chain_score(candidate), candidate)
                    continue
                if next_node in nodes:
                    continue
                next_path = path + [next_event]
                candidate = chain_candidate(next_path, sample_limit)
                paths.add(
                    tuple(nodes + [next_node]),
                    chain_score(candidate),
                    candidate,
                )
                extend(next_path, nodes + [next_node], deadline)
                if truncated:
                    return

        for first in events:
            extend(
                [first],
                [first.payer_id, first.receiver_id],
                first.timestamp + deadline_delta,
            )
            if truncated:
                break

        common = {
            "window_minutes": window,
            "expansions": expansions,
            "expansion_limit": max_expansions,
            "search_truncated": truncated,
        }
        path_results.append(paths.result(**common))
        cycle_results.append(cycles.result(**common))
    return path_results, cycle_results


def parse_windows(value: str) -> list[int]:
    try:
        windows = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("windows must be comma-separated positive integers")
    if not windows or any(window <= 0 for window in windows):
        raise argparse.ArgumentTypeError("windows must be comma-separated positive integers")
    return sorted(set(windows))


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("value must be a positive integer") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def build_report(
    events: Sequence[Event],
    summary: LoadSummary,
    windows: Sequence[int],
    min_payers: int,
    top: int,
    max_expansions: int,
    max_evidence_events: int,
    max_hops: int,
    sample_limit: int = 5,
) -> dict[str, Any]:
    paths, cycles = search_paths(
        events, windows, top, max_expansions, max_hops, sample_limit
    )
    return {
        "status": "ok",
        "interpretation": NOTICE,
        "parameters": {
            "windows_minutes": list(windows),
            "min_payers": min_payers,
            "top": top,
            "path_hops": {"minimum": 2, "maximum": max_hops},
            "max_expansions_per_window": max_expansions,
            "max_inbound_events_per_candidate": max_evidence_events,
            "source_name_sample_limit_per_event": sample_limit,
        },
        "input_summary": {
            "input_rows": summary.input_rows,
            "canonical_events": summary.canonical_events,
            "keyed_rows": summary.keyed_rows,
            "unkeyed_rows": summary.unkeyed_rows,
            "business_key_merged_groups": summary.merged_groups,
            "merged_rows": summary.merged_rows,
            "merge_rule": (
                "Only rows sharing the same non-empty business_key are merged; "
                "time and amount are never deduplication keys."
            ),
        },
        "results": {
            "rapid_pass_through": search_rapid_pass_through(
                events,
                windows,
                top,
                max_expansions,
                sample_limit,
            ),
            "convergence_then_transfer": search_convergence(
                events,
                windows,
                min_payers,
                top,
                max_evidence_events,
                sample_limit,
            ),
            "return_to_original_payer": search_returns(
                events,
                windows,
                top,
                max_expansions,
                sample_limit,
            ),
            "temporal_paths": paths,
            "temporal_cycles": cycles,
        },
    }


def run_self_test() -> dict[str, Any]:
    from io import StringIO

    rows = [
        {"event_id": "e1", "timestamp": "2026-01-01T10:00:00Z", "payer_id": "A", "receiver_id": "H", "amount": "100.00", "currency": "usd", "source_file": "one.xlsx", "source_sheet": "s1", "source_row": 2, "business_key": "bk-1", "payer_name": "Alpha", "summary": "optional summary", "transaction_code": "optional code", "adapter_extension": {"ignored": True}},
        {"event_id": "e1-copy", "timestamp": "2026-01-01T10:00:00+00:00", "payer_id": "A", "receiver_id": "H", "amount": 100, "currency": "USD", "source_file": "two.csv", "source_sheet": "data", "source_row": 9, "business_key": "bk-1"},
        {"event_id": "e2", "timestamp": "2026-01-01T10:01:00Z", "payer_id": "B", "receiver_id": "H", "amount": "50", "currency": "USD", "source_file": "one.xlsx", "source_sheet": "s1", "source_row": 3},
        {"event_id": "e3", "timestamp": "2026-01-01T10:03:00Z", "payer_id": "H", "receiver_id": "C", "amount": "100", "currency": "USD", "source_file": "three.json", "source_sheet": "events", "source_row": 1},
        {"event_id": "e4", "timestamp": "2026-01-01T10:04:00Z", "payer_id": "H", "receiver_id": "A", "amount": "10", "currency": "USD", "source_file": "three.json", "source_sheet": "events", "source_row": 2},
        {"event_id": "e5", "timestamp": "2026-01-01T10:05:00Z", "payer_id": "C", "receiver_id": "A", "amount": "110", "currency": "USD", "source_file": "four.csv", "source_sheet": "ledger", "source_row": 4},
        {"event_id": "e6", "timestamp": "2026-01-01T10:06:00Z", "payer_id": "X", "receiver_id": "Y", "amount": "7", "currency": "EUR", "source_file": "five.csv", "source_sheet": "ledger", "source_row": 1},
        {"event_id": "e7", "timestamp": "2026-01-01T10:06:00Z", "payer_id": "X", "receiver_id": "Y", "amount": "7", "currency": "EUR", "source_file": "six.csv", "source_sheet": "ledger", "source_row": 1},
    ]
    stream = StringIO("\n".join(json.dumps(row) for row in rows))
    events, summary = load_events(stream)

    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)

    check(summary.input_rows == 8, "input row count")
    check(summary.canonical_events == 7, "only keyed duplicates may merge")
    check(summary.merged_groups == 1 and summary.merged_rows == 1, "merge summary")
    merged = next(event for event in events if event.business_key == "bk-1")
    check(len(merged.source_refs) == 2, "all merged source references are retained")
    check(
        event_output(merged)["payer_names"] == ["Alpha"],
        "known names are retained while unknown extensions are ignored",
    )
    bounded_merged = event_output(merged, 1)
    check(
        len(bounded_merged["source_refs"]) == 1
        and bounded_merged["source_refs_omitted"] == 1
        and len(bounded_merged["event_ids"]) == 1
        and bounded_merged["event_ids_omitted"] == 1,
        "merged event evidence is sample bounded",
    )
    check(sum(event.payer_id == "X" for event in events) == 2, "unkeyed duplicates remain")

    report = build_report(events, summary, [10], 2, 1, 10_000, 10, 4)
    rapid = report["results"]["rapid_pass_through"][0]
    convergence = report["results"]["convergence_then_transfer"][0]
    returns = report["results"]["return_to_original_payer"][0]
    cycles = report["results"]["temporal_cycles"][0]
    check(rapid["candidate_count"] >= 1, "rapid pass-through candidate")
    check(
        "continuity_ratio" in rapid["top_candidates"][0],
        "rapid pass-through reports amount continuity",
    )
    check(convergence["candidate_count"] >= 1, "convergence candidate")
    check(convergence["returned_count"] == 1, "top bounds convergence output")
    check(returns["candidate_count"] >= 1, "return candidate")
    check(cycles["candidate_count"] >= 1, "cycle candidate")
    check(
        all(
            later["timestamp"] > earlier["timestamp"]
            for candidate in cycles["top_candidates"]
            for earlier, later in zip(candidate["hops"], candidate["hops"][1:])
        ),
        "hop timestamps strictly increase",
    )
    check(
        all("source_refs" in hop for candidate in cycles["top_candidates"] for hop in candidate["hops"]),
        "each hop carries source references",
    )

    _, limited_cycles = search_paths(events, [10], 5, 1, 4)
    check(limited_cycles[0]["search_truncated"], "expansion budget is reported")

    two_hop_paths, _ = search_paths(events, [10], 10, 10_000, 2)
    check(
        all(
            candidate["hop_count"] <= 2
            for candidate in two_hop_paths[0]["top_candidates"]
        ),
        "maximum hop count is enforced",
    )

    simultaneous_rows = [
        {**rows[0], "business_key": None, "event_id": "same-1", "receiver_id": "B"},
        {**rows[0], "business_key": None, "event_id": "same-2", "payer_id": "B", "receiver_id": "C", "source_row": 3},
    ]
    simultaneous, _ = load_events(
        StringIO("\n".join(json.dumps(row) for row in simultaneous_rows))
    )
    simultaneous_paths, simultaneous_cycles = search_paths(
        simultaneous,
        [10],
        10,
        10_000,
        4,
    )
    check(
        simultaneous_paths[0]["candidate_count"] == 0
        and simultaneous_cycles[0]["candidate_count"] == 0,
        "equal timestamps cannot form a path",
    )

    shared_placeholder_rows = [
        {**rows[0], "business_key": None, "event_id": "placeholder-in", "payer_id": "stable-a", "receiver_id": "cust-", "source_row": 20},
        {**rows[0], "business_key": None, "event_id": "placeholder-out", "timestamp": "2026-01-01T10:00:01Z", "payer_id": "cust-", "receiver_id": "stable-b", "source_row": 21},
    ]
    placeholder_rejected = False
    try:
        placeholder_events, _ = load_events(
            StringIO("\n".join(json.dumps(row) for row in shared_placeholder_rows))
        )
        placeholder_paths, _ = search_paths(
            placeholder_events, [10], 5, 10_000, 4
        )
        placeholder_candidate_count = placeholder_paths[0]["candidate_count"]
    except EventValidationError:
        placeholder_rejected = True
        placeholder_candidate_count = 0
    check(
        placeholder_rejected and placeholder_candidate_count == 0,
        "shared missing-party placeholder cannot create a path",
    )

    scoped_unresolved_rows = [
        {**rows[0], "business_key": None, "event_id": "scoped-in", "payer_id": "stable-a", "receiver_id": "unresolved:scoped-in:receiver", "source_row": 22},
        {**rows[0], "business_key": None, "event_id": "scoped-out", "timestamp": "2026-01-01T10:00:01Z", "payer_id": "unresolved:scoped-out:payer", "receiver_id": "stable-b", "source_row": 23},
    ]
    scoped_events, _ = load_events(
        StringIO("\n".join(json.dumps(row) for row in scoped_unresolved_rows))
    )
    scoped_paths, scoped_cycles = search_paths(scoped_events, [10], 5, 10_000, 4)
    check(
        scoped_paths[0]["candidate_count"] == 0
        and scoped_cycles[0]["candidate_count"] == 0,
        "observation-scoped unresolved ids do not connect across events",
    )

    conflict = dict(rows[1])
    conflict["amount"] = "101"
    try:
        load_events(StringIO("\n".join(json.dumps(row) for row in [rows[0], conflict])))
    except EventValidationError:
        checks += 1
    else:
        raise AssertionError("conflicting business key must fail validation")

    invalid = dict(rows[0])
    invalid["amount"] = "0"
    try:
        load_events(StringIO(json.dumps(invalid)))
    except EventValidationError:
        checks += 1
    else:
        raise AssertionError("non-positive amount must fail validation")

    return {"status": "ok", "self_test": {"checks_passed": checks}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Search bounded temporal associations in a normalized JSONL event table."
        )
    )
    parser.add_argument("input_path", nargs="?", help="JSONL file, or - for stdin")
    parser.add_argument("--input", dest="input_option", help="JSONL file, or - for stdin")
    parser.add_argument("--output", help="result JSON file, or - for stdout")
    parser.add_argument(
        "--windows-minutes",
        "--windows",
        dest="windows",
        type=parse_windows,
        default=parse_windows("30,60,180"),
        metavar="MINUTES",
        help="comma-separated positive window sizes (default: 30,60,180)",
    )
    parser.add_argument("--min-payers", type=positive_int, default=2)
    parser.add_argument("--top", type=positive_int, default=20)
    parser.add_argument(
        "--sample",
        type=positive_int,
        default=5,
        help="maximum source refs, merged event IDs, and names per output event",
    )
    parser.add_argument("--max-hops", type=int, choices=range(2, 5), default=4)
    parser.add_argument(
        "--max-expansions",
        type=positive_int,
        default=200_000,
        help="maximum path or return expansions per window",
    )
    parser.add_argument(
        "--max-inbound-events",
        type=positive_int,
        default=50,
        help="maximum inbound evidence events returned per convergence candidate",
    )
    parser.add_argument("--self-test", action="store_true")
    return parser


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
    if args.input_option and args.input_path:
        parser.error("use either --input or positional input, not both")
    input_path = args.input_option or args.input_path
    if not input_path:
        parser.error("input is required unless --self-test is used")

    try:
        if args.output and args.output != "-" and input_path != "-":
            if Path(args.output).resolve() == Path(input_path).resolve():
                raise EventValidationError("input and output paths must differ")
        if input_path == "-":
            events, summary = load_events(sys.stdin)
        else:
            with Path(input_path).open("r", encoding="utf-8-sig") as stream:
                events, summary = load_events(stream)
        report = build_report(
            events,
            summary,
            args.windows,
            args.min_payers,
            args.top,
            args.max_expansions,
            args.max_inbound_events,
            args.max_hops,
            args.sample,
        )
    except (EventValidationError, OSError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": "invalid_input", "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output and args.output != "-":
        try:
            with Path(args.output).open("w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.write("\n")
        except OSError as exc:
            print(
                json.dumps(
                    {"status": "error", "error": "output_failed", "message": str(exc)},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
