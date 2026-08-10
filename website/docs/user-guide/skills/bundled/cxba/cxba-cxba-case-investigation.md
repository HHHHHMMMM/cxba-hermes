---
title: "Cxba Case Investigation — Guide evidence-led investigation of CXBA case materials"
sidebar_label: "Cxba Case Investigation"
description: "Guide evidence-led investigation of CXBA case materials"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Cxba Case Investigation

Guide evidence-led investigation of CXBA case materials.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/cxba/cxba-case-investigation` |
| Version | `0.1.0` |
| Author | CXBA Project Team, Hermes Agent |
| License | MIT |
| Platforms | linux |
| Tags | `cxba`, `investigation`, `evidence` |
| Related skills | [`cxba-material-profiling`](/docs/user-guide/skills/bundled/cxba/cxba-cxba-material-profiling), [`cxba-raw-material-investigation`](/docs/user-guide/skills/bundled/cxba/cxba-cxba-raw-material-investigation), [`cxba-safe-tabular-analysis`](/docs/user-guide/skills/bundled/cxba/cxba-cxba-safe-tabular-analysis) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# CXBA Case Investigation Skill

Investigate a case question with traceable evidence, reproducible calculations, and explicit counter-evidence. This skill guides the investigation without prescribing a fixed tool chain, a mandatory full-case read, or a mandatory reviewer.

## When to Use

- The user asks an open-ended or multi-source case question.
- The answer may require combining documents, tables, images, or existing Workspace results.
- A result must distinguish source facts, calculations, interpretations, and unresolved gaps.
- Do not use it for a single, already well-scoped table calculation; use `cxba-safe-tabular-analysis` directly.

## Prerequisites

- The trusted initial context provides the current case and a Spring material catalog.
- Original materials are mounted read-only under `/data`.
- The current Session Workspace is mounted read-write under `/workspace`.
- Spring MCP tools provide case-scoped reads and semantic write proposals.
- Sandbox dependencies are preinstalled. Never install packages during a Run.

## How to Run

Use `skills_list` and `skill_view` to load only the supporting CXBA skill needed for the current step. Use `terminal`, `read_file`, `write_file`, `patch`, and other available native tools in whatever sequence the evidence requires; multiple tool calls may be combined within a Run.

If the material catalog has not been normalized, run:

```text
terminal(command="python /opt/cxba/scripts/material_catalog.py --catalog /workspace/input/materials.json --data-root /data --output /workspace/catalog/materials.json")
```

## Quick Reference

| Need | Capability |
|---|---|
| Material paths and stable references | Spring `materialId` catalog |
| Mixed-format structure | `cxba-material-profiling` |
| Evidence and counter-evidence | `cxba-raw-material-investigation` |
| Large table calculations | `cxba-safe-tabular-analysis` |
| Case-scoped business data | Spring MCP read tools |
| Proposed business changes | Spring MCP proposal tools |
| Optional independent check | `delegate_task` with a focused review brief |

## Procedure

1. State the case question, target subjects, period, and material ambiguity. Continue once the question is answerable or the remaining ambiguity is explicitly recorded.
2. Read the Spring material catalog. Select relevant materials first. Expand to other materials only when the question, a discovered link, or an evidence gap justifies it; a full-case read is optional.
3. Create the Workspace directories from `references/workspace-layout.md`. Do not write into `/data`, `/case-sessions`, or `/shared`.
4. Profile unfamiliar formats and record actual Sheets, pages, encodings, and readable ranges. A profile is navigation metadata, not evidence.
5. Form one or more testable hypotheses. For each, identify supporting evidence, plausible normal explanations, counter-evidence, and missing material.
6. Read source locations and run reproducible calculations. Large tables must be streamed or queried in bounded batches; money must use exact decimal arithmetic.
7. Save scripts under `/workspace/scripts`, intermediate data under `/workspace/intermediate`, notes under `/workspace/notes`, and results under `/workspace/results`.
8. Cite each source by Spring `materialId`, relative path, and page, Sheet, row, or cell range. Never replace `materialId` with a scan-order label.
9. Separate confirmed source facts, computed results, interpretations, and open gaps in the answer or report.
10. Decide whether independent review is useful. Use it when the user requests review or when stakes, complexity, disputed assumptions, or fragile calculations justify it. Otherwise finish directly.

## Business Dictionaries

Account types are exactly `BANK_ACCOUNT`, `BANK_CARD`, `WECHAT`, and `ALIPAY`.

Account-party relations are exactly `OWNS`, `USES`, `CONTROLS`, and `OPERATES`. A transaction counterparty does not establish any of these relations. `OWNS` has one final owner and no time range.

Person-enterprise relations are exactly `NATURAL_SHAREHOLDER`, `KEY_PERSON`, `LEGAL_REP`, `FINANCIAL_OFFICER`, `WORKS_FOR`, and `ACTS_FOR`.

People and enterprises require their specified strong identifiers before global registration. Do not merge people by name or enterprises by name.

## Pitfalls

- A filename, directory name, high amount, repeated pattern, same name, or algorithm hit is not a finding.
- Zero matches may mean missing material, wrong scope, parsing failure, or a genuine absence; report which was tested.
- Never write PostgreSQL directly. Business writes are Spring proposals and remain pending until human approval.
- Do not create content hashes, file versions, payload versions, idempotency keys, or approval gates.
- Do not send case content to public services or place original values in production logs.
- Do not use `pip`, `npm`, `apt`, or another installer during a Run. Report a missing image capability.

## Verification

- Every cited source resolves to the current Spring `materialId` and current relative path.
- Every important calculation names its script, inputs, filters, exact amount rule, and output.
- Supporting evidence and counter-evidence are both addressed.
- Unread or failed material is not represented as read.
- Optional review, when used, independently reopens sources rather than reviewing only the draft.
