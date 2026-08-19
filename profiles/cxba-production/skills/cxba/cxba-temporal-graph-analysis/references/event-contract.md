# Normalized Transaction Event Contract

Write one JSON object per line. The graph scripts consume this contract and do not parse source workbooks themselves.

## Required fields

| Field | Meaning |
|---|---|
| `event_id` | Adapter-local unique event or observation id |
| `timestamp` | ISO-8601 transaction timestamp |
| `payer_id` | Payer analysis node: account, source-scoped customer/name, resolved subject, or observation-scoped id |
| `receiver_id` | Receiver analysis node using the same namespace rules |
| `amount` | Positive decimal string in source units |
| `currency` | Source currency code or explicit `UNKNOWN` |
| `source_file` | Source-relative file path |
| `source_sheet` | Sheet/table/section name; empty only when not applicable |
| `source_row` | Original Excel/CSV row or document locator |

Optional fields include `business_key`, `payer_name`, `receiver_name`, `payer_node_level`, `receiver_node_level`, `source_amount`, `source_direction`, `reversal_of`, `summary`, `transaction_code` and `source_observations`. Adapters may add other extension fields. Analysis scripts must strictly validate the required fields, allow and ignore unknown extensions, and retain available party names only in bounded candidate or source-sample output.

Example:

```json
{"event_id":"row-42","timestamp":"2025-01-03T14:05:09","payer_id":"account-a","receiver_id":"account-b","amount":"1250.00","currency":"CNY","source_file":"transactions.xlsx","source_sheet":"Account B","source_row":42,"business_key":"bank-reference-123"}
```

## Analysis node rules

- These node ids are for current-case statistics and graph calculation; they are not person-master ids and do not authorize master merging.
- Use the source account number when present. Otherwise use a source-scoped customer id, then a source-and-role-scoped normalized party name, and finally an observation-scoped unique id. Record the level as `ACCOUNT_LEVEL | CUSTOMER_LEVEL | SOURCE_NAME_LEVEL | OBSERVATION_LEVEL | RESOLVED_SUBJECT`.
- A `SOURCE_NAME_LEVEL` node may connect rows inside the same verified source layout, but the same normalized name in another file or source remains a separate node until materials or user-provided evidence supports the bridge.
- Use a resolved subject identifier only when materials or user-provided evidence supports the account/name-to-subject mapping and the adapter applies the same namespace consistently to both sides.
- A blank customer identifier does not make an endpoint unknown when its account number is present.
- Never put multiple missing parties under a shared placeholder such as `UNKNOWN`, `cust-` or another prefix with no real identifier. Analysis scripts reject these values because they create false cross-event edges.
- If one side lacks account, customer id and name, retain it with an observation-scoped unique id such as `unresolved:<observation-id>:payer` and record it as unresolved. Such ids must never be reused across events.
- If both sides are observation-level and no target or path continuity can be established, exclude the event from graph input and count the exclusion; keep its source observation available for gap reporting.
- Party names remain identity-resolution candidates. Same or similar names never merge nodes across sources by default.
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
- Distinguish exact duplicate files, repeated business events across files or Sheets, payer/receiver mirror observations, summary-versus-detail representations, reversals/refunds and genuinely separate similar transactions. They are not one category.
- A reversal, refund or correction is a new business event linked through `reversal_of` or equivalent source fields, not a duplicate to delete. Keep both original and reversal observations and report gross and known net effects separately.
- When payer and receiver views show the same event, merge only with a stable event key or verified paired representation; preserve both locations in `source_observations` and count `raw_observation_count` separately from deduplicated `event_count`.
- When no stable key or verified pairing exists, keep rows separate, disclose duplicate risk and do not claim a deduplicated total.

## Time and amount rules

- Combine date and time only after verifying their formats and timezone meaning.
- Preserve the original source row before sorting or dataframe index reset.
- Parse amounts with decimal arithmetic. Do not add different currencies without an explicit user-approved conversion rule.
- Exclude zero, missing or malformed amounts only under a declared rule and count every exclusion.
- The normalized `amount` is always a positive decimal magnitude after direction has been established. Preserve the original text/sign in `source_amount` and the interpreted source rule in `source_direction`.
- A negative source value does not by itself mean incoming, outgoing, refund or reversal. Determine its meaning from the exact income/expense columns, debit-credit flag and observed flag values, source documentation, balance movement, paired records or another verified source rule.
- If the source has separate income and expense columns, accept an ordinary event only when exactly one side is positive; zero/zero, both-positive or signed contradictions become `DIRECTION_UNKNOWN` or a separately verified correction rule.
- If the source uses one signed amount column, derive the sign convention from actual source semantics and verify representative rows against balance movement or paired records before normalizing. Never publish a negative amount as an incoming event merely because a summary contains words such as “收款”“汇入” or “贷记”.

## Direction and temporal matching rules

- First determine payer and receiver from verified source semantics, then define incoming and outgoing relative to the target analysis node. A populated name, keyword, Sheet title or amount sign alone never defines direction.
- Classify source rows as `IN | OUT | SELF | REVERSAL | DIRECTION_UNKNOWN` before graph calculation. `DIRECTION_UNKNOWN` rows remain counted and traceable but do not silently enter inflow/outflow totals or paths.
- Source Sheet membership is not a node whitelist. Retain identified counterparties outside the profiled files or Sheets so a supported path can continue to an external party.
- A pass-through match requires the same currency, strict time order and a stated time window. Prefer exact amount continuity; for split or aggregate matching, state the conservation rule, matched amount, difference and unmatched balance.
- Do not generate a Cartesian product of all incoming and later outgoing events. Select non-conflicting matches by source evidence, amount continuity and shortest elapsed time. If assignment is not unique, keep bounded alternatives and label them ambiguous.
- Do not consume one event in multiple selected chains unless the source establishes a split, merge or duplicate observation. Temporal proximity alone never proves that later money came from an earlier transfer.

## Output discipline

The event JSONL may be large and should stay on disk. Give the model only the normalization summary and bounded motif results. Reopen original source rows for final evidence.
