# Record JSONL Contract

每行必须是一个独立 JSON 对象。适配器应输出规范化值，不要让对账脚本猜测列名、日期、金额单位或业务键。一次脚本运行只包含同一业务记录类别；不同业务对象分别输出和运行。

## 明细记录

```json
{
  "source_id": "detail-source-a",
  "record_type": "detail",
  "source_locator": {"sheet": "transactions", "row": 2},
  "business_key": {"transaction_id": "T-001"},
  "critical_fields": {
    "amount": "120.50",
    "currency": "CNY",
    "occurred_at": "2026-01-02T10:30:00"
  },
  "amount": "120.50",
  "dimensions": {"month": "2026-01", "category": "transfer"}
}
```

字段要求：

- `source_id`：同一原始来源使用同一非空字符串；不同布局不要误用同一 ID。
- `record_type`：固定为 `detail`。
- `source_locator`：非空对象，保存工作表、行号或原始记录号等定位信息。
- `business_key`：稳定业务键对象；复合键可包含多个字段。字段名和值必须是规范化后的非空字符串。没有可靠稳定键时必须写 `null`。
- `critical_fields`：用于确认同键记录是否一致的对象。将金额、币种、业务时间、收付方向、主体标识等实际关键字段映射为规范名称；未知值写 `null`，不要猜补。
- `amount`：规范化十进制字符串或 `null`。金额有业务意义时，也必须以同一值出现在 `critical_fields.amount` 中。
- `dimensions`：可选对象，用于匹配汇总范围；值使用规范化字符串。

稳定键必须来自来源中的正式业务标识，例如交易流水号、订单号或由业务规范明确规定的复合键。文件名、工作表名、表头、行号、姓名、金额和近似时间都不是稳定业务键。

## 汇总记录

```json
{
  "source_id": "summary-source-a",
  "record_type": "summary",
  "source_locator": {"sheet": "monthly-total", "row": 3},
  "summary": {"count": 25, "amount": "8300.00"},
  "scope": {"month": "2026-01", "category": "transfer"},
  "detail_source_ids": ["detail-source-a"]
}
```

字段要求：

- `record_type`：固定为 `summary`。
- `summary.count`：非负整数或 `null`；`summary.amount`：十进制字符串或 `null`；至少提供一个。
- `scope`：可为空对象。非空时，键值必须与明细 `dimensions` 精确匹配。
- `detail_source_ids`：该汇总明确覆盖的明细来源。只有从结构、公式、说明或可复核业务规则确认后才填写；无法确认时写空数组。

汇总记录是核对目标，不是明细。脚本不会把汇总金额加到明细金额中。

## 适配器要求

1. 先读取实际文件结构，再硬编码该布局的工作表、表头位置和字段映射。
2. 对日期、时区、金额单位、正负方向和公式值作显式转换；无法确定时保留 `null` 并报告限制。
3. 逐行输出，保留可回查的 `source_locator`；不要把多个原始行静默折成一行。
4. 使用相同规范字段名表达跨来源的同一含义。不同含义不得仅因列名相似而强行映射。
5. 只读输入文件，将 JSONL、适配器和报告写到新路径。

## 自动合并边界

- 同一稳定业务键只出现一次：作为一条已确认键记录保留。
- 同一稳定业务键出现多次且所有 `critical_fields` 完整、字段集合相同、值完全一致：合并为一次计数。
- 同一稳定业务键出现多次但关键字段缺失、字段集合不同或值不同：记为冲突，不自动合并。
- `business_key: null`：始终保留为未键控记录，不与任何记录自动合并。
- 文件级重复必须在规范化前通过逐字节比较确认；record contract 不根据内容推断文件完全相同。
