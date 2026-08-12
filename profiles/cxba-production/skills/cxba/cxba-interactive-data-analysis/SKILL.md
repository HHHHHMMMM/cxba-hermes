---
name: cxba-interactive-data-analysis
description: Answer user-directed questions over case Excel, XLS, CSV, RTF, Word, PDF, and text materials. Use when the user names a file, Sheet, person, account, period, expense type, transaction direction, or metric and expects a focused calculation, comparison, ranking, or follow-up clarification instead of a full autonomous case investigation.
---

# CXBA Interactive Data Analysis

Work only on the question the user asked. Do not start a full-case inventory or investigation unless the user explicitly requests it.

## 1. Resolve the requested scope

Extract the dimensions that matter to the requested result:

- source file and Sheet;
- target person, account, organization, or reimbursement applicant;
- time range and time grouping;
- transaction direction and whose perspective defines inflow/outflow;
- business category, status, currency, amount unit, duplicate handling, and requested metrics.

Do not require every dimension for every question. A missing dimension requires clarification only when two reasonable interpretations would materially change the result and the file cannot resolve the choice.

Use the named file directly. If the user gives a unique partial filename, first perform one location step to resolve its full path; do not call a profiler or attempt to read the unresolved short path. Once that step returns one unique match, use it and stop further `search`, `ls`, or `find` calls for that file. Inspect only enough structure and relevant distinct field values to identify real alternatives; do not calculate all alternatives before the user chooses.

## 2. Decide whether to answer or clarify

Answer directly when the file, target, period, direction, category, and metric are already sufficient. Do not ask confirmation merely to be cautious.

Clarify before calculating when any material choice remains, including:

- `工资收入` while the data separately contains salary, bonus, allowance, or reimbursement;
- `报销金额` while submitted, approved, settled, rejected, refunded, or reversed records could differ;
- `转入/转出` without a target account or entity perspective;
- `最集中` without a meaningful time unit when daily, monthly, or quarterly results differ;
- multiple accounts, Sheets, currencies, people with the same or similar name, overlapping files, or possible duplicate records;
- an unspecified time range when the source covers multiple materially different periods.

Ask only the smallest set of concrete questions needed to make the calculation unique, normally one question and never more than three. Offer choices grounded in actual source fields when known. Start the response with `需要确认：` and stop after the questions; do not provide guessed totals.

After the user answers, retain that choice for the rest of the conversation. Restate the accepted scope in one short sentence, then calculate. Do not ask the same question again.

Once the user-provided scope is sufficient, continue through reading, calculation, source-row verification, and the final answer without pausing for confirmation.

## 3. Read and calculate

Load `cxba-material-profiling` for unfamiliar file structures and `cxba-safe-tabular-analysis` for tabular calculations.

Create `/workspace/scripts` and `/workspace/results` before analysis. Run environment bootstrap only when a required runtime, library, or tool is actually unavailable.

Read the real file and confirm the relevant Sheet, header row, field meanings, data types, and representative source rows before writing a calculation script. First-row headers must not be assumed. Use whichever installed read-only library fits the format, including pandas, openpyxl, xlrd, or document extractors.

For a workbook, do not run `inspect` until an exact Sheet name is verified. Unless the user supplied an exact Sheet name that has already been verified, first read workbook metadata or the library's real `sheet_names`. Reuse the returned name in every later read and script; never pass a guessed or reconstructed Sheet name at any stage.

Save reusable scripts under `/workspace/scripts`. The calculation script must write a structured result file under `/workspace/results`. Record the calculation contract:

- input file and Sheet;
- filters and accepted user definitions;
- direction rule and target perspective;
- status, currency, null, reversal, refund, and duplicate treatment;
- grouping unit and formulas.

Every result file must be generated directly by the saved script that reads the original material and performs the calculation. Never copy values from prior tool output into constants to manufacture a result file. If the script fails, fix that same script and rerun it; never bypass the failure with hard-coded results.

By default, the structured result must contain only the aggregates requested by the user, the calculation contract, anomaly counts, and a bounded sample of source evidence. Unless the user explicitly requests transaction-level or row-level detail, do not save all amounts, all source rows, or a complete match list. When reading a result, select only its summary fields or a bounded range; never load an entire large result file back into the conversation context.

Compute counts, sums, net amounts, rankings, and time distributions with code or exact decimal arithmetic. Do not calculate totals mentally. Reopen representative matching rows and any maximum/minimum rows from the original material before answering.

When one deterministic script completes the requested calculation and its result is internally consistent, do not repeat the full calculation with a second library. Cross-check with another implementation only when the data is abnormal, results conflict, or the user requests independent verification.

After a tool failure, read the actual error and fix its root cause before continuing. Do not repeat the unchanged failing command.

Before answering, confirm that both the saved calculation script and its structured result file actually exist. In `证据`, cite only the existing result file as the result artifact; never describe the calculation script path as a result file.

## 4. Answer with evidence

For a completed analysis, use this compact order:

1. `结论：` requested numbers or ranking;
2. `口径：` the accepted filters, direction, period, grouping, and duplicate/status treatment;
3. `证据：` file, Sheet, real fields, source row or cell locations, and result file path;
4. `限制：` only unresolved source limitations that could affect the result.

State the requested figures or candidates in the answer itself. Scripts and result paths are supporting artifacts, not the answer; never finish with only a completion notice or file locations.

Never infer business nature from amount, periodicity, filename, blank summary, same surname, or adjacent files. If the source field does not establish a category, write `性质待核实`. Keep facts, calculations, and interpretations separate.
