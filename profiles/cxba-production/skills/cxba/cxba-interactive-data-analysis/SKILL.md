---
name: cxba-interactive-data-analysis
description: 对案件Excel、XLS、CSV、RTF、Word、PDF和文本材料回答有界问题。用户指定文件、Sheet、人员、账户、期间、费用类型、交易方向或指标，并要求计算、比较、排名、核对或追问时默认使用；即使用户称其为“调查”或“核查”，只要没有明确要求遍历全部材料并形成整案报告，也应使用本Skill而不是 cxba-case-investigator。
---

# CXBA Interactive Data Analysis

只处理用户提出的问题。不得创建全目录薄清单、逐文件案件台账、独立复核文件或运行完整案件终检。用户后续明确要求遍历全部材料并形成整案报告时，才切换到`cxba-case-investigation`；用户要求审查已有报告或claims时，切换到`cxba-evidence-review`。

第一次读取材料内容前加载`cxba-analysis-notebook`，用`/workspace/analysis-notebook.md`记录本轮实际处理的每个文件；处理完一个文件必须先落笔记再读下一个。不要为专项问题盘点或补读无关文件。

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

Load `cxba-material-profiling` for unfamiliar file structures and `cxba-safe-tabular-analysis` for tabular calculations. 多个来源可能重复、包含、补充或冲突时加载`cxba-source-reconciliation`；只有费用记录确需模式分析时才加载`cxba-expense-pattern-analysis`。

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

进入最终答案的每个数字必须由该保存脚本直接生成。探索阶段另行运行的内联代码可以帮助定位，但不得把它的结果手工补入正式结果JSON；需要采用时，把逻辑合并进保存脚本并重新运行。

## 4. Answer with evidence

For a completed analysis, use this compact order:

1. `结论：` requested numbers or ranking;
2. `口径：` the accepted filters, direction, period, grouping, and duplicate/status treatment;
3. `证据：` file, Sheet, real fields, source row or cell locations, and result file path;
4. `限制：` only unresolved source limitations that could affect the result.

State the requested figures or candidates in the answer itself. Scripts and result paths are supporting artifacts, not the answer; never finish with only a completion notice or file locations.

Never infer business nature from amount, periodicity, filename, blank summary, same surname, or adjacent files. If the source field does not establish a category, write `性质待核实`. Keep facts, calculations, and interpretations separate.

## 5. Deliver traceable claims

本轮最终回答包含材料事实、计算、关系、规律候选、业务判断、假设或缺口时，加载`cxba-claim-delivery`并完整执行其统一合同。只交付本轮回答实际使用的claims；不得转入完整案件清单或完整案件终检。
