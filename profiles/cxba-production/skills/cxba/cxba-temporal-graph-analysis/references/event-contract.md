# Normalized Transaction Event Contract

Write one JSON object per line. The graph scripts consume this contract and do not parse source workbooks themselves.

## Required fields

| Field | Meaning |
|---|---|
| `event_id` | Adapter-local unique event or observation id |
| `timestamp` | ISO-8601 transaction timestamp |
| `payer_id` | Stable payer account or resolved subject id |
| `receiver_id` | Stable receiver account or resolved subject id |
| `amount` | Positive decimal string in source units |
| `currency` | Source currency code or explicit `UNKNOWN` |
| `source_file` | Source-relative file path |
| `source_sheet` | Sheet/table/section name; empty only when not applicable |
| `source_row` | Original Excel/CSV row or document locator |

Optional fields include `business_key`, `payer_name`, `receiver_name`, `summary`, `transaction_code` and `source_observations`. Adapters may add other extension fields. Analysis scripts must strictly validate the required fields, allow and ignore unknown extensions, and retain available party names only in bounded candidate or source-sample output.

Example:

```json
{"event_id":"row-42","timestamp":"2025-01-03T14:05:09","payer_id":"account-a","receiver_id":"account-b","amount":"1250.00","currency":"CNY","source_file":"transactions.xlsx","source_sheet":"Account B","source_row":42,"business_key":"bank-reference-123"}
```

## Identity rules

- Use the source account number as the default graph identifier whenever it is present. Use a subject or customer identifier instead only when source data proves the account-to-subject mapping and the adapter applies the same identifier namespace consistently to both sides of every event.
- A blank customer identifier does not make an endpoint unknown when its account number is present.
- Never put multiple missing parties under a shared placeholder such as `UNKNOWN`, `cust-` or another prefix with no real identifier. Analysis scripts reject these values because they create false cross-event edges.
- If one side has a stable identifier and the other side is missing, retain the event only when the unknown side receives an observation-scoped unique id such as `unresolved:<observation-id>:payer` and is recorded as unresolved in the normalization summary. Such ids must never be reused across events.
- If neither side can be identified stably, exclude the event from the graph input and count the exclusion in the normalization summary.
- Party names are display values or identity-resolution candidates only. A same or similar name is not a stable id and must not create or merge graph nodes by default.
- Record the source rule used to establish each Sheet's own subject and accounts in the normalization summary.

## Counterparty and internal-record rules

- Transaction code, transaction name and summary are classification clues only. No one of them is sufficient to exclude a row.
- Retain a transaction edge whenever payer/receiver account or subject fields establish an identifiable real counterparty, even if descriptive text appears bank-internal.
- Exclude or deduplicate only when source structure establishes a pure internal ledger mirror with no real counterparty, or a repeated observation of the same business event. Record the supporting fields, rule and row count in the normalization summary.
- When classification is uncertain, produce clearly separated inclusion scopes and compare results. Do not delete an entire category using a single text rule.

## Deduplication rules

- Give observations the same non-empty `business_key` only when stable source fields establish the same event, such as an exact bank reference or a verified paired representation.
- Never deduplicate solely by filename, same name, amount, timestamp proximity or adjacent rows.
- Preserve every source location when multiple observations are merged. If the adapter cannot represent multiple locations in one event row, keep the observations separate and explain the limitation.

## Time and amount rules

- Combine date and time only after verifying their formats and timezone meaning.
- Preserve the original source row before sorting or dataframe index reset.
- Parse amounts with decimal arithmetic. Do not add different currencies without an explicit user-approved conversion rule.
- Exclude zero, missing or malformed amounts only under a declared rule and count every exclusion.

## Direction and temporal matching rules

- Determine incoming and outgoing direction by comparing `receiver_id` or `payer_id` with the target account or subject id. A populated name field alone never defines direction.
- Source Sheet membership is not a node whitelist. Retain identified counterparties outside the profiled files or Sheets so a supported path can continue to an external party.
- A pass-through match requires the same currency, strict time order and a stated time window. Prefer exact amount continuity; for split or aggregate matching, state the conservation rule, matched amount, difference and unmatched balance.
- Do not generate a Cartesian product of all incoming and later outgoing events. Select non-conflicting matches by source evidence, amount continuity and shortest elapsed time. If assignment is not unique, keep bounded alternatives and label them ambiguous.
- Do not consume one event in multiple selected chains unless the source establishes a split, merge or duplicate observation. Temporal proximity alone never proves that later money came from an earlier transfer.

## Output discipline

The event JSONL may be large and should stay on disk. Give the model only the normalization summary and bounded motif results. Reopen original source rows for final evidence.
