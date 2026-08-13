---
name: cxba-source-reconciliation
description: Reconcile overlapping Excel workbooks, CSV exports, summary tables, and raw transaction files without unsafe deduplication. Use when multiple materials may repeat, contain, supplement, or conflict with one another and totals must be traced to source structure and stable business keys.
---

# Source Reconciliation

读取来源内容前加载`cxba-analysis-notebook`，逐文件即时记录来源结构、覆盖、主要内容、可能用途、冲突线索和精确定位。本Skill产生进入最终回答的事实、计算、来源关系或缺口时加载`cxba-claim-delivery`。

先识别来源关系，再规范化，再对账。不要直接把多份表拼接后求和，也不要修改原始文件。

## 工作流

1. **直接检查实际结构**
   - 逐个查看文件类型、工作表、表头层级、合并单元格、字段、数据区、公式、汇总行、空行、编码和分隔符。
   - 记录每个来源是原始明细、加工明细、汇总，还是无法确认。用字段和行级证据说明判断，不根据文件名猜测。
   - 对疑似完全相同的文件执行逐字节比较，例如使用 `filecmp.cmp(path_a, path_b, shallow=False)`。只有全部字节相同才能标记 `exact_file_duplicate`；相同文件名、大小、表头或内容观感都不够。

2. **为实际来源编写适配器**
   - 先理解结构，再为每一种实际布局编写 source-specific 适配器；不要先写一个依赖模糊列名匹配的万能解析器。
   - 让适配器只读原件，明确选择工作表、表头行、数据起止位置、字段映射、金额和日期解释，并把结果写到新的 JSONL 文件。
   - 将不同来源映射到相同的业务键名和关键字段名。保留 `source_id` 和 `source_locator`，使每条规范化记录可回到原位置。
   - 输出前读取 [record-contract.md](references/record-contract.md)，逐行校验 JSONL。

3. **按证据确定关系**
   - `exact_file_duplicate`：仅限逐字节相同的文件。登记副本关系后只选择一份进入数值对账，原件全部保留。
   - `same_business_record`：非空稳定业务键相同，且完整关键字段完全一致。只有这种记录可以自动合并计数。
   - `contains`：一个明细来源的稳定键集合严格包含另一个，全部共有键均一致。
   - `supplements`：来源各自存在独有稳定键，共有键均一致；无共有稳定键时也只能说明互相补充，不能说明无重复风险。
   - `conflict`：相同稳定键的关键字段不同或不足以核对。保留双方记录，交由人工判断。
   - 汇总记录只用于验证指定明细范围，不与明细记录合并，也不加入明细金额。

4. **运行确定性对账**

一次运行只放入同一业务记录类别；不同业务对象分别运行，避免把不同含义的业务键误作可比键。

```bash
python3 /root/.hermes/skills/cxba/cxba-source-reconciliation/scripts/reconcile_records.py normalized/*.jsonl --output reconciliation.json
```

报告包含各来源明细计数和金额、稳定键重叠、来源关系、冲突、仅某来源存在的记录、未提供稳定键的记录、汇总与明细核对结果，以及受 `--sample-limit` 限制的样本。

5. **给出结论与限制**
   - 分开报告文件级完全相同、业务记录重叠、汇总核对和未解决冲突。
   - 只从 `reconciled_details` 读取已安全合并的稳定键结果；把 `unkeyed` 和 `conflicts` 单独列出。
   - 说明适配器使用的稳定业务键、关键字段和汇总范围。无法证明时明确写“无法确认”，不要补推关系。

## 禁止规则

- 禁止按姓名相同、金额相同、时间临近、表头相同、文件名相似或行内容近似去重。
- 禁止把汇总行、汇总表与其明细再次相加。
- 禁止把空键、临时行号、工作表行号或自行拼接的模糊特征当作稳定业务键。
- 禁止因同一稳定键发生冲突而任选一条覆盖另一条。
- 禁止修改、覆盖、重排或清洗原始文件；适配器和报告必须写入新位置。

## 工具

- `references/record-contract.md`：规范化 JSONL 字段、适配器要求和关系语义。
- `scripts/reconcile_records.py`：仅使用 Python 标准库的对账脚本。运行 `python3 /root/.hermes/skills/cxba/cxba-source-reconciliation/scripts/reconcile_records.py --self-test` 检查核心规则。
