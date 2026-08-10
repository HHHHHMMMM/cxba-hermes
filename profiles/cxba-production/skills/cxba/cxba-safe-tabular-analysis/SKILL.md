---
name: cxba-safe-tabular-analysis
description: Analyze large case tables with exact, bounded computation.
author: CXBA Project Team, Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cxba, tabular, decimal]
    related_skills: [cxba-case-investigation, cxba-material-profiling, cxba-raw-material-investigation]
---

# CXBA Safe Tabular Analysis Skill

Inspect and calculate over CSV, Excel, Parquet, and DuckDB materials without loading unbounded data or losing monetary precision. The Agent chooses suitable tools and may run multiple related operations in one Run.

## When to Use

- A case question depends on table rows, Sheets, account lists, amounts, dates, rankings, or joins.
- A file is too large for complete model-context output.
- Account enumeration must cover the full valid data range.

## Prerequisites

- Resolve the input through a Spring `materialId` catalog.
- The Sandbox image already contains Python, Pandas, NumPy, DuckDB, PyArrow, OpenPyXL, xlrd, and the CXBA scripts.
- `/data` is read-only and `/workspace` is writable.

## How to Run

Profile structure before choosing fields:

```text
terminal(command="python /opt/cxba/scripts/tabular_profile.py --catalog /workspace/catalog/materials.json --material-id <materialId> --output /workspace/catalog/table.json")
```

Enumerate accounts from explicit columns:

```text
terminal(command="python /opt/cxba/scripts/enumerate_accounts.py --catalog /workspace/catalog/materials.json --material-id <materialId> --sheet <sheet> --account BANK_ACCOUNT=<column> --name-column <column> --bank-column <column> --output /workspace/results/accounts.jsonl")
```

Compute an exact amount summary:

```text
terminal(command="python /opt/cxba/scripts/decimal_summary.py --catalog /workspace/catalog/materials.json --material-id <materialId> --sheet <sheet> --amount-column <column> --group-by <column> --output /workspace/results/amount-summary.json")
```

## Quick Reference

- Account types: `BANK_ACCOUNT`, `BANK_CARD`, `WECHAT`, `ALIPAY`.
- Account relations: `OWNS`, `USES`, `CONTROLS`, `OPERATES`.
- Account identity: trimmed complete account ID plus account type; preserve the original display value.
- Account IDs compare case-insensitively. Account and bank names compare after
  trimming only; preserve every distinct non-empty candidate and flag conflicts.
- Money: parse as `Decimal` from source text and serialize as a decimal string.
- Large inputs: CSV row iterator, Excel read-only iterator, PyArrow record batches, or DuckDB read-only query.

## Procedure

1. Inspect all relevant Sheets or tables and verify the actual header row, data range, field names, roles, and representative records.
2. Define the calculation contract: input, Sheet/table, target perspective, filters, dates, currency, status, reversals, nulls, duplicates, group keys, and formulas.
3. Write or select a reusable script under `/workspace/scripts`. Do not put full rows in the tool response.
4. Stream or query the complete valid data range. Use bounded samples only for structure inspection, never to estimate totals.
5. Parse amounts from their source representation with exact decimal arithmetic. Reject invalid values and count them separately.
6. Write complete results to `/workspace/results`, with short counts and paths in the tool response.
7. Reopen source rows for decisive matches, maximum/minimum entries, and exceptions before reporting.
8. When proposing accounts to Spring, submit the complete enumerated list and cite the source `materialId`; do not submit only selected or suspicious accounts.
9. Surface `accountNameCandidates` and `bankNameCandidates` conflicts for human
   review; never silently select one conflicting candidate.

## Pitfalls

- Never assume row one is the header or infer role from a column position.
- Do not use binary floating point for money.
- Do not infer account ownership from transaction participation, account name, or a counterparty field.
- Do not infer account type when it is absent or ambiguous.
- Never install a missing library during a Run; report the missing image dependency.
- Do not add content hashes, result versions, or idempotency fields to analysis outputs.

## Verification

- Row counts cover the complete declared range and parse failures are counted.
- Monetary outputs are strings derived from `Decimal`, with currency and sign rules stated.
- Account output includes only the four allowed types, complete IDs, and the Spring `materialId`.
- A moved material still resolves through the same `materialId` after catalog refresh.
- Intentional analysis artifacts are stored in `/workspace`. Oversized raw tool
  output is exposed read-only under `/run-diagnostics`; use the bounded tool
  response path to inspect it, then publish any retained result to Workspace.
