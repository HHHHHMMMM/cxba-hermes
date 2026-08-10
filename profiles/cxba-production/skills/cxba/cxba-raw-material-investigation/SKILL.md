---
name: cxba-raw-material-investigation
description: Test case hypotheses against sources and counter-evidence.
author: CXBA Project Team, Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cxba, evidence, verification]
    related_skills: [cxba-case-investigation, cxba-material-profiling, cxba-safe-tabular-analysis]
---

# CXBA Raw Material Investigation Skill

Use original materials to test a focused case question through corroboration and counter-evidence. The method supports selective reading and autonomous tool choice; it does not impose a fixed sequence, one-tool-per-turn rule, or compulsory second Agent.

## When to Use

- A claim must be traced to original documents, tables, images, or archived files.
- Multiple sources may support or contradict the same hypothesis.
- A calculated pattern needs source-row verification and alternative explanations.

## Prerequisites

- Load the Spring material catalog and resolve sources by `materialId`.
- Keep `/data` read-only and write all notes, scripts, conversions, and results to `/workspace`.
- Use only preinstalled Sandbox capabilities; never send case content to external services.

## How to Run

Use `skill_view` for material profiling or safe table analysis only when the current source requires it. Use native Hermes tools directly and combine them as needed within the same Run.

## Quick Reference

| Record | Required content |
|---|---|
| Source fact | `materialId`, path, location, observed value |
| Calculation | script, input location, filters, exact arithmetic, output |
| Interpretation | reasoning linking facts to the case question |
| Counter-evidence | normal explanation, conflicting source, missing scope |
| Gap | material or verification needed to decide |

## Procedure

1. Express the question as a testable hypothesis and at least one plausible alternative explanation.
2. Select the smallest relevant source set. Expand it when a discovered link or unresolved contradiction requires more material.
3. Confirm source structure and role fields before calculation. Distinguish account owner, user, controller, operator, payer, payee, applicant, approver, and counterparty.
4. Read cited source locations. For long files, record the exact ranges inspected and the uninspected remainder.
5. Use reproducible scripts for aggregation, matching, and ranking. Stream large tables, keep money as decimal strings, and write large results to `/workspace/results`.
6. Reopen representative matched rows, all decisive exceptions, and boundary values in the source.
7. Seek disconfirming evidence: reversal, refund, internal transfer, duplicate source, date mismatch, role mismatch, missing coverage, or an independent normal explanation.
8. Save an evidence item only after the source location and calculation can be reproduced.
9. State whether the result supports, weakens, excludes, or cannot decide the hypothesis. Human confirmation remains required for formal findings and leads.

## Optional Independent Review

Use `delegate_task` only when the user requests independent review or the task's risk and complexity justify it. Give the reviewer the case question, draft claim, source `materialId` values, locations, scripts, and result paths. The reviewer must reopen the sources and rerun decisive calculations. A review of the draft alone is not independent.

## Pitfalls

- High value, concentration, frequency, same surname, same organization, or a model match is an observation, not a conclusion.
- A counterparty in a transaction does not establish `OWNS`, `USES`, `CONTROLS`, or `OPERATES`.
- Only byte-identical duplicates may be treated as duplicate files; do not merge content by names or headers.
- A failed parser or incomplete catalog does not prove absence.
- Do not write formal findings, leads, or PostgreSQL records directly.

## Verification

- Each claim has a resolvable `materialId` and precise source location.
- Each calculation is reproducible and uses explicit roles, direction, filters, and deduplication rules.
- Counter-evidence and coverage limits are stated next to the conclusion they qualify.
- Optional review, if performed, records what sources and calculations were independently checked.
