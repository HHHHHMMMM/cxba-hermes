---
name: cxba-expense-pattern-analysis
description: 分析报销与费用记录中的可复核规律候选。适用于报销单、费用明细、审批记录和支付记录的集中度、突增、重复金额、拆分、阈值附近、非工作时段及退款冲正影响分析。
---

# CXBA费用规律分析

用代码扫描全部相关记录，只输出有限聚合和可回查候选。统计命中不是违规结论。

## 1. 先确定统计口径

先查看原件字段、Sheet、状态值、类别值、币种和日期覆盖，再确认下列口径：

1. 期间：起止边界是否包含、时区和跨日规则；
2. 日期：按申请日期、审批日期还是支付日期；
3. 状态：提交、通过、驳回、已支付、退款、冲正分别纳入、排除还是仅用于净额影响；
4. 类别：奖金、补贴和报销是否分开，哪些原始类别属于本次范围；
5. 币种：单币种分析，或采用用户明确提供的汇率及换算日；
6. 去重：不去重，或按来源中可证明同一业务记录的稳定键去重；
7. 统计对象：报销单、费用明细或支付笔次，以及集中度中的“主体”是申请人还是收款方。

原件能唯一确定的口径直接记录，不再询问。只有两种以上合理解释会实质改变结果时才提问；问题必须给出原件中的真实选项，最多三问。口径未唯一确定时，以`需要确认：`开头提问并停止计算，不并行计算多个猜测口径。

把已确定的期间、日期字段、状态、类别、币种、去重规则、统计对象、主体字段、阈值、窗口和top写入简短的`analysis-contract.json`，供后续脚本直接使用。

## 2. 查看真实结构并编写适配器

读取[expense-contract.md](references/expense-contract.md)。先检查真实结构，再在`/workspace/scripts`编写本来源专用、只读的适配器；禁止把陌生文件强塞进猜测的通用表头解析器。

适配器必须：

- 只读原件，扫描所有相关文件、Sheet和行；保留筛选、排序、重置索引前的原始文件名、Sheet和行号；
- 按已确认统计对象写`/workspace/results/expenses.jsonl`，一行一个统一expense对象；
- 同时写`normalization-summary.json`，记录输入/接收/排除计数、日期范围、状态/类别/币种分布、字段映射和未解决映射；
- 只在稳定业务键或用户批准规则能证明同一记录时填写`business_key`。相同姓名、金额、日期接近或相邻行不能证明重复；
- 不把本案值、候选值或预期答案硬编码进适配器。

逐行检查JSONL符合契约，确认记录粒度一致、计数守恒、日期/金额/币种/状态映射可解释，并且每条记录都能按原始文件名、Sheet和行号回查。发现问题时修复适配器后重跑，不得伪造结果。

## 3. 运行标准分析器

使用标准库脚本，不把JSONL内容复制进提示词：

```bash
python3 /root/.hermes/skills/cxba/cxba-expense-pattern-analysis/scripts/analyze_expense_patterns.py \
  --input /workspace/results/expenses.jsonl \
  --output /workspace/results/expense-patterns.json \
  --period-start 2026-01-01 \
  --period-end 2026-03-31 \
  --date-field paid_at \
  --statuses PAID,REFUNDED,REVERSED \
  --category-kinds REIMBURSEMENT \
  --currency CNY \
  --statistical-unit CLAIM \
  --subject-field applicant_id \
  --dedup-mode business-key \
  --threshold 5000 \
  --threshold-window 100 \
  --baseline-window-days 30 \
  --split-window-minutes 1440 \
  --top 10
```

示例参数只是格式，不是默认案件口径。把每个值替换为`analysis-contract.json`中的已确认值。用户未给阈值时省略`--threshold`和`--threshold-window`，不得自行选取一个“容易命中”的阈值。

脚本统一搜索：

- 按时间、项目、申请人、收款方的集中度；
- 相对同一主体历史活动日基线的突增；
- 同一主体的重复金额；
- 同日同主体、指定分钟窗口内的多笔聚集，并单列是否跨过用户阈值；
- 用户阈值附近金额的主体聚集；
- 周末和用户定义夜间窗口；
- 退款、冲正对毛额和已知净影响的差异。

金额靠前、频次靠前、相对基线突增、重复金额、疑似拆分、阈值附近、周末或夜间都只能标为`规律候选`。不得据此推断虚假报销、规避审批、利益输送、串通或其他违规。

脚本输出必须按`top`限制聚合组和候选数量，候选只附有限原始`source_file/source_sheet/source_row`，不输出全量expense记录。不同币种分别计算，不直接相加。

## 4. 回查候选并报告

只读取结果中的`scope`、`input_summary`、`aggregates`和有限`candidate_sections`。按每个候选的原始文件名、Sheet和行号重新打开原件，核对日期、主体角色、项目、状态、金额、币种、业务键和退款/冲正语义。

回查失败时修复适配器或口径并重跑；不得在结果文件中手改数值。离线测试按原件完整值和完整原始定位复核。

按以下顺序简要回答：

1. `口径：`期间、日期、状态、类别、币种、去重、统计对象、阈值和窗口；
2. `统计事实：`有限聚合、毛额、已知净影响及未知影响数；
3. `规律候选：`候选类型、计算条件、数值和有限原始source refs；
4. `反证与限制：`退款冲正、重复观察、缺失日期/身份/汇率、正常业务解释；
5. `复核文件：`适配器、规范化摘要和结果路径。

始终把“统计事实”“规律候选”“业务判断待核实”分开。证据不足时写`待核实`，绝不推断违规。

## 5. 验证

修改本Skill后执行：

```bash
python3 /root/.hermes/skills/cxba/cxba-expense-pattern-analysis/scripts/analyze_expense_patterns.py --self-test
```

自测数据必须是合成数据，不得把本案内容写入Skill、参考文件或脚本。
