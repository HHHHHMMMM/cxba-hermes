---
name: cxba-temporal-graph-analysis
description: Discover reproducible time patterns and candidate transaction chains from case spreadsheets, CSV files, or normalized transaction events. Use when the user asks about peak or night activity, bursts, fast-in-fast-out behavior, many payers converging on one subject, subsequent fan-out, return loops, multi-hop transfers, or what happened after money reached an account or organization.
---

# CXBA Temporal Graph Analysis

读取交易材料内容前加载`cxba-analysis-notebook`，逐文件即时记录真实结构、覆盖、主要内容、可用字段、候选及反证定位。本Skill产生进入最终回答的统计事实、时序候选或缺口时加载`cxba-claim-delivery`。

## Mandatory execution path
1. On every start or resume, inspect `/workspace/scripts` and `/workspace/results`; reuse valid artifacts and continue at the next unfinished step.
2. Inspect the source and read [event-contract.md](references/event-contract.md), then create or reuse exactly one source adapter under `/workspace/scripts`; fix it in place.
3. The adapter must write `/workspace/results/events.jsonl` and `/workspace/results/normalization-summary.json` before temporal analysis.
4. Run the listed `profile_event_times.py` command directly; do not `skill_view` or read its source during normal use.
5. Choose several windows from observed distributions, source time precision and sensitivity; never invent one convenient long window.
6. Run the listed `search_temporal_motifs.py` command directly with those windows; do not replace it with custom all-pairs code.
7. Never emit unbounded pairs, paths, nodes or source evidence; every detailed output must use `top`, `sample` or an explicit bounded limit.
8. Only after both scripts succeed, read bounded JSON fields, reopen referenced source rows, then answer.
9. Input or contract errors must be fixed in the adapter; inspect or modify a bundled script only when it explicitly reports an internal defect.
10. Only the two bundled scripts listed below exist; after a missing-tool error, inspect files and never retry the same missing command unchanged.
11. Treat `events.jsonl` as the only calculation input after normalization. Reopen source files only to verify cited rows; never build a second workbook reader or a second conflicting event set.
12. If a requested metric is not present in the standard outputs, write at most one bounded extension script that reads `events.jsonl`, preserves this contract and writes one structured result. Do not return to raw rows, use floating-point money, or enumerate all possible pairs.

## Adapter contract

Required fields are `event_id`, `timestamp`, `payer_id`, `receiver_id`, `amount`, `currency`, `source_file`, `source_sheet`, `source_row`.

- Confirm headers, time format and precision, units, currency, payer/receiver fields, stable ids, own-account rules, duplicate observations and original row locations. Preserve source rows before filtering or sorting.
- Use a stable account id when an account field exists. Use a resolved subject id only when source data proves the account-to-subject mapping and the adapter applies that mapping consistently to both sides. Never use a party name as `payer_id` or `receiver_id`, and never discard an endpoint merely because customer id is blank when its account is present. A retained unknown endpoint must use an observation-scoped unique id; exclude and count rows with neither endpoint identifiable.
- Set `business_key` only from a stable event-level key. If keyed rows conflict on time, parties, amount or currency, repair the adapter; leave the key blank or use a source detail key. Never remove extension fields to bypass the conflict.
- Transaction code, name and summary are clues only. Keep an edge when account/subject fields identify a real counterparty. Exclude or deduplicate only when source structure proves a pure internal mirror with no real counterparty or a repeated observation; record basis and counts. Compare separate scopes when uncertain.
- Write one normalization summary with read/accepted/excluded counts, reasons, currencies, time range, identity rules, unresolved identities and duplicate handling. Keep full events on disk. Report keyed, unkeyed and merged counts; when no stable `business_key` exists, state that duplicate-looking observations remain separate and never claim they were treated as one event.

Do not write the adapter from memory or inference alone. The event contract is mandatory because identity and duplicate mistakes create false graph edges even when the source rows are parsed correctly.

## Transaction semantics

- Define direction from the target account or subject: an event is incoming only when `receiver_id` equals that target and outgoing only when `payer_id` equals it. A non-empty payer or receiver name does not establish direction. Report self-transfers separately; never treat one as pass-through evidence.
- Sheet names and source files locate observations; they do not define the graph's allowed nodes. Keep identified external counterparties and allow a path to enter or leave the set of profiled Sheets.
- For rapid pass-through or chain candidates, require same currency and strict timestamp order. Put exact or near-exact amount continuity in the first tier, then rank the amount that remains supported and elapsed time. Report the continuity threshold, amount difference and retained percentage; do not let a trivial amount win solely because it is a few seconds faster.
- Do not pair every incoming event with every later outgoing event. An event may support only one selected match unless the source proves a split, merge or duplicate observation. When several assignments remain plausible, report the ambiguity and unmatched balance instead of multiplying candidates.
- Night or off-hours analysis must report the declared time window, total-activity denominator, count share and amount share for comparable subjects or periods. Absolute night totals alone do not establish concentration.
- Temporal proximity and amount similarity produce a candidate only. Never state that two rows are the same money or that conduct is abnormal without corroborating evidence.

## Standard bounded commands

```bash
python /root/.hermes/skills/cxba/cxba-temporal-graph-analysis/scripts/profile_event_times.py \
  --input /workspace/results/events.jsonl \
  --output /workspace/results/time-profile.json \
  --top 10 \
  --sample 5

python /root/.hermes/skills/cxba/cxba-temporal-graph-analysis/scripts/search_temporal_motifs.py \
  --input /workspace/results/events.jsonl \
  --output /workspace/results/temporal-motifs.json \
  --windows-minutes 60,360,1440 \
  --min-payers 3 \
  --max-hops 4 \
  --top 10 \
  --sample 5
```

The profile keeps complete global 24-hour coverage while bounding nodes, candidates and per-node sources. The motif search bounds candidates per motif/window, rapid pass-through matches, convergence evidence, paths, source references, merged ids and names. Interpret returned and omitted counts together. For rapid pass-through, inspect both the amount-ranked and amount-time-ranked lists; the latter is a discovery priority, not a business metric. For paths, prefer the returned amount-continuity fields over hop count alone.

Use several source-grounded windows and compare sensitivity. The shown `60,360,1440` set is a comparison set, not permission to choose a threshold without first checking the distribution and source time precision.

## Verify and report

- After success, read only coverage summaries, global aggregates, bounded nodes and top candidates. Do not create repeated temporary formatting or extraction scripts.
- Reopen every reported source reference and verify direction, identities, timestamp, amount, currency, strict ordering, deduplication and missing events. Fix failed source evidence in the same adapter and rerun.
- Check `input_summary` before describing duplicate handling. If `keyed_rows` and `merged_rows` are zero, report each source row as a separate observation and do not say duplicate or mirror rows were combined.
- Separate `统计事实`, `时序关联候选` and `业务判断待核实`. A temporal path, amount similarity, rapid transfer or loop does not prove the same money or intent.
- Return the strongest verified candidates in the chat itself with amount, elapsed time, parties, Sheet and source row, followed by the compact method, limits and result paths. Artifact paths are supplementary; never finish with only `results saved` or file locations.
