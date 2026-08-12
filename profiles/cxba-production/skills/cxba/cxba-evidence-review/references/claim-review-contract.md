# Claim Review Contract

本合同定义独立复核 Agent 的 `claim-review.json` 工作文件和 `review-result.md` 输出。合同用于统一结构和引用定位，不证明 claim 正确。

## 1. `claim-review.json`

文件必须是 UTF-8 JSON 对象：

```json
{
  "report_path": "draft-report.md",
  "claims": [
    {
      "id": "C001",
      "statement": "报告中的一个可独立验证的陈述",
      "type": "CALCULATION",
      "user_basis": "用户要求的对象、期间、方向、币种、范围和口径",
      "coverage": "FULL",
      "source_refs": [
        {
          "path": "materials/source-file.xlsx",
          "locator": "Sheet 名及原始行或单元格区域"
        }
      ],
      "calculation_refs": [
        {
          "script_path": "scripts/calculate.py",
          "result_path": "results/calculate.json",
          "purpose": "该脚本和结果支持的具体计算"
        }
      ],
      "chain_hops": [],
      "limitations": []
    }
  ]
}
```

相对路径以 `claim-review.json` 所在目录为基准。绝对路径按原路径检查。

### 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `report_path` | 是 | 待复核报告文件路径 |
| `claims` | 是 | 非空 claim 数组 |

### Claim 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 文件内唯一、稳定的 claim 编号 |
| `statement` | 是 | 报告原有陈述；不得由复核 Agent 新造结论 |
| `type` | 是 | `FACT`、`CALCULATION`、`TEMPORAL_ASSOCIATION`、`BUSINESS_INFERENCE` 之一 |
| `user_basis` | 是 | 用户原始口径，包括对象、角色、期间、方向、币种、范围、阈值和去重含义中适用的部分 |
| `coverage` | 是 | `FULL`、`SAMPLE` 或 `UNKNOWN` |
| `source_refs` | 是 | 非空原始材料引用数组 |
| `calculation_refs` | 是 | 计算引用数组；`CALCULATION` 至少一项，其他类型可为空 |
| `chain_hops` | 是 | 候选链逐跳数组；无链条时为空数组 |
| `limitations` | 是 | 限制数组；`SAMPLE`、`UNKNOWN` 和 `BUSINESS_INFERENCE` 不得为空 |

`source_refs` 每项包含：

- `path`：原始文件路径。
- `locator`：Sheet/表/页码/行号/段落/单元格区域等可复核定位。

`calculation_refs` 每项包含：

- `script_path`：调查 Agent 使用的计算脚本。
- `result_path`：该脚本对应的落盘结果。
- `purpose`：它支持本 claim 的哪项计算。

`chain_hops` 每项包含：

```json
{
  "from_node": "链条起点",
  "to_node": "链条终点",
  "basis": "该跳声称的身份、交易、审批、报销或事件关系",
  "source_refs": [
    {
      "path": "materials/source-file.xlsx",
      "locator": "Sheet 名及原始行或单元格区域"
    }
  ]
}
```

一条记录同时包含事实和推断时，必须拆成多个 claim。抽样、截断、部分范围或覆盖未知时使用 `SAMPLE`/`UNKNOWN`，并在 `limitations` 说明实际边界。

## 2. 结构初筛

执行：

```bash
python3 /root/.hermes/skills/cxba/cxba-evidence-review/scripts/check_evidence_package.py /path/to/claim-review.json
```

可选机器可读输出：

```bash
python3 /root/.hermes/skills/cxba/cxba-evidence-review/scripts/check_evidence_package.py --json /path/to/claim-review.json
```

退出码为 `0` 表示结构和引用存在性检查通过；`1` 表示发现错误。检查内容包括：

- JSON 可解析、必填字段和枚举值有效。
- claim 编号唯一。
- 报告、原始来源、计算脚本和结果路径存在且为文件。
- `CALCULATION` 含计算引用。
- `SAMPLE`、`UNKNOWN` 和 `BUSINESS_INFERENCE` 已声明限制。
- 每个已登记 hop 含独立原始来源定位。

脚本不读取或理解材料内容，不验证 locator 是否真实命中，不重跑计算，不判断身份、金额、时序、因果或业务结论。脚本成功只能作为人工/Agent 复核开始前的初筛结果。

## 3. `review-result.md`

第一行：

```text
VERDICT: PASS|RETURN_FOR_CORRECTION|NEEDS_HUMAN
```

每条 claim 使用同一结构：

```markdown
## C001
- 类型：FACT|CALCULATION|TEMPORAL_ASSOCIATION|BUSINESS_INFERENCE
- Verdict：PASS|RETURN_FOR_CORRECTION|NEEDS_HUMAN
- 用户口径：保持/偏移；说明
- 原始引用：已核对的位置及结果
- 计算复现：命令、结果和差异；不适用时写“不适用”
- 独立复算：方法、结果和差异；不适用时写“不适用”
- 证据强度：全量/抽样、身份、金额、时序及反证检查
- 限制：报告已披露和遗漏项
- 原因：支持 verdict 的具体原因
- 修正任务：可执行任务；无则写“无”
- 人工核实：必须由人判断的问题；无则写“无”
```

总体 verdict 优先级：`NEEDS_HUMAN` 高于 `RETURN_FOR_CORRECTION`，`PASS` 仅在全部 claim 均为 `PASS` 时使用。

复核 Agent 只能评估和退回报告已有 claim。新发现只能作为超出本轮范围的待调查事项，不得在复核结果中升级为新结论。
