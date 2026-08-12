# Unified Expense JSONL Contract

本契约供来源专用适配器和`analyze_expense_patterns.py`之间交换数据。JSONL留在离线工作区，不直接作为报告。

## 1. 文件级规则

- UTF-8 JSONL，每个非空行一个对象。
- 所有记录使用同一`statistical_unit`：`CLAIM`、`LINE_ITEM`或`PAYMENT`。
- 金额用十进制定点字符串，不用浮点数。`amount`必须大于零。
- `impact_amount`为已由来源语义确认的费用影响：正数增加费用，负数表示已确认退款/冲正减少费用，`null`表示影响未知。不得只凭状态猜正负号。
- 不跨币种相加。多币种时分币种运行；只有用户给定汇率和换算日后才可在适配器中生成明确标注的换算记录。
- 日期使用ISO-8601。适配器把时间统一为口径指定时区并保留偏移；原件无时区时在normalization summary中写明按哪个本地时区解释。
- 原始文件名、Sheet和行号必须原样保留，供离线复核。

## 2. 每行字段

| 字段 | 类型 | 规则 |
|---|---|---|
| `record_id` | string | 适配器内唯一记录ID |
| `statistical_unit` | string | `CLAIM`、`LINE_ITEM`或`PAYMENT` |
| `expense_id` | string/null | 来源能确定时填写费用对象ID |
| `business_key` | string/null | 仅在来源能证明是同一业务记录时填写；用于批准后的去重 |
| `applicant_id` | string/null | 申请人标识；保留来源中用于稳定关联的值 |
| `payee_id` | string/null | 收款方标识；保留来源中用于稳定关联的值 |
| `project_id` | string/null | 项目标识；保留来源中用于稳定关联的值 |
| `category_kind` | string | `REIMBURSEMENT`、`BONUS`、`ALLOWANCE`、`OTHER`或`UNKNOWN` |
| `category` | string | 规范化类别代码 |
| `status` | string | 规范化状态代码，见下节 |
| `amount` | string | 正十进制金额，保留来源单位 |
| `impact_amount` | string/null | 已确认费用影响，可正、负或零 |
| `currency` | string | ISO币种代码或`UNKNOWN` |
| `applied_at` | string/null | 申请时间 |
| `approved_at` | string/null | 审批通过/决定时间 |
| `paid_at` | string/null | 支付时间 |
| `status_at` | string/null | 当前状态发生时间，主要供退款/冲正定位 |
| `source_ref` | object | 原始文件名、Sheet和行号，结构见下节 |

来源特有字段的映射规则写入适配器和normalization summary，不随意增加通用字段。

## 3. 状态代码

优先映射为：

- `SUBMITTED`：已提交、未证明通过或支付；
- `APPROVED`：已通过、未证明支付；
- `REJECTED`：已驳回；
- `PAID`：已支付；
- `REFUNDED`：来源明确为退款；
- `REVERSED`：来源明确为冲正/撤销入账；
- `CANCELLED`：已取消；
- `UNKNOWN`：无法从来源唯一确定。

不要用申请日期存在推断`SUBMITTED`，不要用审批日期存在推断`APPROVED`，不要用金额为负推断退款或冲正。把来源字段和值到代码的映射写入normalization summary。

## 4. Source ref

`source_ref`必须包含：

```json
{
  "source_file": "原始相对路径/报销明细.xlsx",
  "source_sheet": "费用明细",
  "source_row": 42
}
```

- `source_file`保留原始文件名或原始相对路径。
- `source_sheet`保留原Sheet名；CSV或无Sheet来源可为空字符串。
- `source_row`保留原始行号；文档类来源可使用可复核的页码/段落定位字符串。
- 分析器在聚合和候选中直接输出这些字段，供复核原件。

## 5. 去重和对象粒度

- `record_id`标识一个规范化观察，不代表业务上唯一。
- 只有稳定业务编号、来源明确的重复标记或用户批准的等价规则才能生成相同`business_key`。
- 相同`business_key`的记录只有在统计对象、主体、项目、类别、状态、日期、金额、影响金额和币种均一致时才可合并；合并后保留全部原始source refs。字段冲突时停止去重并报告冲突行。
- `business_key=null`的记录永不自动去重。相同姓名、金额、日期或相邻行只生成重复金额候选，不用于去重。
- 一个报销单含多条费用明细时，不得同时把报销单总额和明细金额放进同一次金额汇总。先按用户确认的`statistical_unit`统一粒度。

## 6. Normalization summary

适配器另写`normalization-summary.json`，至少包含：

- 来源文件、格式、Sheet/区段和行数；
- 总读取数、接收数、按原因排除数，且计数守恒；
- 统计对象、日期映射、时区、状态映射、类别映射、金额和影响金额规则；
- 状态、类别、币种、日期范围、缺失日期和未解决主体计数；
- 去重键生成规则、重复观察数和字段冲突数。

## 7. 最小合成示例

示例只演示结构，不对应任何案件：

```json
{"record_id":"rec-001","statistical_unit":"CLAIM","expense_id":"exp-001","business_key":"biz-001","applicant_id":"subject-a","payee_id":"subject-b","project_id":"project-a","category_kind":"REIMBURSEMENT","category":"TRAVEL","status":"PAID","amount":"100.00","impact_amount":"100.00","currency":"CNY","applied_at":"2026-01-02T09:00:00+08:00","approved_at":"2026-01-03T10:00:00+08:00","paid_at":"2026-01-04T11:00:00+08:00","status_at":"2026-01-04T11:00:00+08:00","source_ref":{"source_file":"input.xlsx","source_sheet":"Sheet1","source_row":2}}
```
