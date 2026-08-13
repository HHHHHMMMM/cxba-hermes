# 最终 Claim 交付合同

## 交付文件

每轮最终回答前重新写入`/workspace/evidence-items/final-claims.json`。顶层只包含`claims`数组；只收录本轮最终回答实际出现的材料事实、计算、关系、规律候选、业务判断、假设或缺口。

```json
{
  "claims": [
    {
      "claimCode": "C001",
      "statement": "目标账户在核查期间有2笔符合口径的收入，共2笔，合计200,000.00元",
      "claimType": "CALCULATION",
      "coverage": "FULL",
      "metricCodes": ["METRIC-001"],
      "userBasis": "期间、对象、方向、状态、币种、空值、冲正和去重口径",
      "supportSummary": "两笔原始记录满足口径",
      "counterSummary": "未发现已标识退款或冲正",
      "limitations": [],
      "sourceRefs": [
        {
          "materialId": "materials.json中的真实标识",
          "relativePath": "与该materialId精确对应的完整相对路径",
          "role": "SUPPORT",
          "locatorType": "EXCEL_RANGE",
          "locator": {"sheet": "交易流水", "rows": [2, 5]},
          "description": "参与计算的两笔原始记录"
        }
      ],
      "calculationRefs": [
        {
          "scriptPath": "scripts/calculate.py",
          "resultPath": "results/calculate.json",
          "artifactPaths": ["results/交易汇总.xlsx"],
          "purpose": "按已确认口径筛选并汇总",
          "calculationBasis": "字段、筛选、方向、期间、币种、状态、空值、冲正及去重"
        }
      ]
    }
  ]
}
```

`results/calculate.json`必须由同一脚本生成，并以机器可读值保存发布勾稽：

```json
{
  "publishedMetrics": [
    {
      "metricCode": "METRIC-001",
      "sourceValue": "200000.00",
      "artifactValue": "200000.00",
      "reportBlock": "共2笔，合计200,000.00元"
    }
  ]
}
```

## 通用规则

- `claimCode`在本轮唯一，使用`C001`、`C002`；最终回答在对应陈述旁原样引用`[C001]`。方括号标签只用于claimCode。
- `claimType`只能是`FACT | CALCULATION | RELATION | FINDING | HYPOTHESIS | GAP`；`coverage`只能是`FULL | PARTIAL | NONE`。
- `FACT`、`CALCULATION`、`RELATION`和`FINDING`至少有一个原始`sourceRef`。没有原始支持只能降为`HYPOTHESIS`或`GAP`，不得伪装为已证事实。
- `materialId`、`relativePath`和`sandboxPath`只能从当前`/workspace/input/materials.json`同一条记录逐字复制，严禁凭记忆、相似数字、文件名或同名副本推断。未编目文件不得借同名文件的materialId；可形成`HYPOTHESIS`或`GAP`并说明材料登记缺口。
- `role`使用`SUPPORT`或`COUNTER`。反证、正常解释和来源冲突也要定位到原件。
- Excel使用`EXCEL_RANGE`及真实Sheet；CSV使用`CSV_LINES`；Parquet/DuckDB使用`PARQUET_ROWS`或`DUCKDB_ROWS`及表名；PDF使用`PDF_PAGE`；Word、RTF、图片或扫描件使用`WORD_ANCHOR`、`IMAGE_REGION`或`NEAREST`并说明限制。
- 连续表格证据用`startRow/endRow`或`startLine/endLine`；不连续证据用升序、不重复的`rows`或`lines`。不得用首尾范围包住中间无关记录。
- `CALCULATION`必须有非空且不重复的`metricCodes`，以及实际存在的`scriptPath`和`resultPath`，均为`/workspace`下安全相对路径。`resultPath`只能指向UTF-8 JSON，且包含对应`publishedMetrics`；脚本和结果证明计算过程，不能替代原件定位。
- `.xlsx/.xls/.docx/.pptx/.pdf/.parquet/.duckdb`是二进制文件，禁止使用`read_text()`或字符集参数读取。二进制结果只列入`calculationRefs.artifactPaths`，由相应格式解析器或容器检查；CSV、TSV、JSON、Markdown等文本结果统一写UTF-8。
- 每个发布指标的`reportBlock`由计算脚本生成，必须逐字进入同一Claim的`statement`和最终回复，禁止模型重新心算、手抄或改写数字。存在`artifactPaths`时，脚本必须在文件落盘后重新打开交付物：`sourceValue`记录原始数据独立复算值，`artifactValue`记录交付物回读值；两值不一致立即停止交付。
- 最终回复、分析笔记、计算脚本和结果中只要同时写出文件路径与`materialId`，二者必须与`materials.json`同一条记录一致。计算脚本需要材料身份时运行时读取`materials.json`，不得硬编码标识；修改任一材料身份后必须同步全部交付物并重跑证据校验。
- `statement`必须与最终回答的口径和强度一致。抽样、部分Sheet或材料缺失不能写`FULL`；规律命中不得直接写成违规、利益输送或事实认定。
- `limitations`是字符串数组。授权案件Workspace、Claim及办案页面中的身份证号、账号、卡号、手机号等标识按原件保留，供同一性、方向和原件回查；不得写入生产日志、普通通知或非办案展示。

普通寒暄或纯操作说明可以交付`{"claims":[]}`。复核任务还要按`cxba-evidence-review`生成逐条verdict；复核最终回答中重新确认的材料事实、计算和缺口仍按本合同交付。

没有原始证据时，`HYPOTHESIS`或`GAP`可以使用空`sourceRefs`并正常结束任务，但最终回答必须明确“推测”或“材料缺口”。有原始证据的Claim校验失败时，不得声称证据已核验；可以修正后重跑，或按真实证据强度降级后完成。

校验脚本的完整输出和退出码必须由当前主Agent读取。首行是`CLAIM_DELIVERY_FAIL`时，后续每条错误均为待修正项；逐项修正并重跑，直到最新一次执行退出码为0且首行是`CLAIM_DELIVERY_PASS`。不得吞掉非0退出码或把Gateway事后`INVALID`警告当作已经完成回复前校验。
