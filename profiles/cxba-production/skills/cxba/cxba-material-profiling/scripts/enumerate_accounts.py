#!/usr/bin/env python3
"""Stream a complete account enumeration from explicit source columns."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

from cxba_material_common import (
    ALLOWED_ACCOUNT_TYPES,
    MaterialToolError,
    atomic_write_lines,
    iter_table_rows,
    load_material,
    require_columns,
)


def parse_assignment(raw: str) -> tuple[str, str]:
    account_type, separator, column = raw.partition("=")
    account_type = account_type.strip().upper()
    column = column.strip()
    if not separator or account_type not in ALLOWED_ACCOUNT_TYPES or not column:
        allowed = ", ".join(sorted(ALLOWED_ACCOUNT_TYPES))
        raise MaterialToolError(f"--account must be TYPE=column using one of: {allowed}")
    return account_type, column


def optional_text(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--header-row", type=int, default=1)
    parser.add_argument("--account", action="append", required=True)
    parser.add_argument("--name-column")
    parser.add_argument("--bank-column")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    assignments = [parse_assignment(raw) for raw in args.account]
    entry, path = load_material(args.catalog, args.material_id)
    headers, rows = iter_table_rows(path, table=args.sheet, header_row=args.header_row)
    required = [column for _, column in assignments]
    required.extend(column for column in (args.name_column, args.bank_column) if column)
    require_columns(headers, required)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, work_name = tempfile.mkstemp(
        dir=args.output.parent,
        prefix=".account-enumeration.",
        suffix=".sqlite",
    )
    os.close(descriptor)
    work_path = Path(work_name)
    source_row_count = 0
    skipped_empty_count = 0
    account_count = 0
    connection = sqlite3.connect(work_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute(
            """
            CREATE TABLE accounts (
                account_type TEXT NOT NULL,
                account_normalized TEXT NOT NULL,
                account_id TEXT NOT NULL,
                first_source_row INTEGER NOT NULL,
                occurrence_count INTEGER NOT NULL,
                PRIMARY KEY (account_type, account_normalized)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE candidates (
                account_type TEXT NOT NULL,
                account_normalized TEXT NOT NULL,
                candidate_kind TEXT NOT NULL,
                candidate_value TEXT NOT NULL,
                first_source_row INTEGER NOT NULL,
                PRIMARY KEY (
                    account_type, account_normalized, candidate_kind, candidate_value
                )
            )
            """
        )
        upsert = """
            INSERT INTO accounts (
                account_type, account_normalized, account_id,
                first_source_row, occurrence_count
            ) VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(account_type, account_normalized) DO UPDATE SET
                occurrence_count = accounts.occurrence_count + 1
        """
        insert_candidate = """
            INSERT OR IGNORE INTO candidates (
                account_type, account_normalized, candidate_kind,
                candidate_value, first_source_row
            ) VALUES (?, ?, ?, ?, ?)
        """
        for row_number, row in rows:
            source_row_count += 1
            for account_type, column in assignments:
                original = optional_text(row[column])
                if not original:
                    skipped_empty_count += 1
                    continue
                connection.execute(
                    upsert,
                    (
                        account_type,
                        original.casefold(),
                        original,
                        row_number,
                    ),
                )
                for kind, candidate in (
                    ("account_name", optional_text(row.get(args.name_column)) if args.name_column else None),
                    ("bank_name", optional_text(row.get(args.bank_column)) if args.bank_column else None),
                ):
                    if candidate is not None:
                        connection.execute(
                            insert_candidate,
                            (account_type, original.casefold(), kind, candidate, row_number),
                        )
        connection.commit()
        account_count = int(connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])

        def output_lines() -> Iterator[str]:
            cursor = connection.execute(
                """
                SELECT account_type, account_normalized, account_id,
                       first_source_row, occurrence_count
                  FROM accounts
                 ORDER BY first_source_row, account_type, account_normalized
                """
            )
            candidate_query = """
                SELECT candidate_value
                  FROM candidates
                 WHERE account_type = ?
                   AND account_normalized = ?
                   AND candidate_kind = ?
                 ORDER BY first_source_row, candidate_value
            """
            for account_type, normalized, account_id, first_row, count in cursor:
                account_names = [
                    item[0]
                    for item in connection.execute(
                        candidate_query, (account_type, normalized, "account_name")
                    )
                ]
                bank_names = [
                    item[0]
                    for item in connection.execute(
                        candidate_query, (account_type, normalized, "bank_name")
                    )
                ]
                yield json.dumps(
                    {
                        "materialId": args.material_id,
                        "accountType": account_type,
                        "accountId": account_id,
                        "accountNameCandidates": account_names,
                        "bankNameCandidates": bank_names,
                        "accountName": account_names[0] if len(account_names) == 1 else None,
                        "bankName": bank_names[0] if len(bank_names) == 1 else None,
                        "accountNameConflict": len(account_names) > 1,
                        "bankNameConflict": len(bank_names) > 1,
                        "firstSourceRow": first_row,
                        "occurrenceCount": count,
                    },
                    ensure_ascii=False,
                ) + "\n"

        atomic_write_lines(args.output, output_lines())
    finally:
        connection.close()
        work_path.unlink(missing_ok=True)
    print(
        "account_enumeration_written "
        f"materialId={args.material_id} sourceRows={source_row_count} "
        f"accountCount={account_count} skippedEmpty={skipped_empty_count} output={args.output}"
    )


if __name__ == "__main__":
    try:
        main()
    except MaterialToolError as exc:
        raise SystemExit(f"enumerate_accounts_failed: {exc}") from exc
