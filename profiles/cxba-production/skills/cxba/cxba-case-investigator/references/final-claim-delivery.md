# 最终 Claim 交付合同

本文件只负责把本轮最终回答所依据的原始证据交给 Spring。它不是审批、疑点登记或文件固化动作。

## 交付位置

在最终回复前写入：

```text
/workspace/evidence-items/final-claims.json
```

每轮都重新写入该文件。只写本轮最终回复实际出现的 claim，不复制原始材料，不写案件、用户、Session 或 Run 身份。

## JSON 结构

```json
{
  "claims": [
    {
      "claimCode": "C001",
      "statement": "账户在核查期间发生三笔符合口径的转账",
      "claimType": "CALCULATION",
      "coverage": "FULL",
      "userBasis": "2026-01-01至2026-01-31，收入方向，人民币，排除冲正",
      "supportSummary": "三笔原始记录满足口径",
      "counterSummary": "未发现退款或冲正记录",
      "limitations": [],
      "sourceRefs": [
        {
          "materialId": "Spring材料目录中的真实标识",
          "role": "SUPPORT",
          "locatorType": "EXCEL_RANGE",
          "locator": {
            "sheet": "交易流水",
            "rows": [2, 5, 9]
          },
          "description": "参与计算的三笔原始流水"
        }
      ],
      "calculationRefs": [
        {
          "scriptPath": "scripts/calculate-transfers.py",
          "resultPath": "results/calculate-transfers.json",
          "purpose": "按用户口径筛选并汇总三笔转账",
          "calculationBasis": "字段、筛选、方向、期间、币种、状态、空值、退款冲正及去重处理"
        }
      ]
    }
  ]
}
```

## 规则

- `claimCode`只在本条回复内唯一，使用`C001`、`C002`等短标签；回复正文必须在对应陈述旁原样写出`[C001]`。
- `claimType`只能是`FACT`、`CALCULATION`、`RELATION`、`FINDING`、`HYPOTHESIS`、`GAP`。
- `coverage`只能是`FULL`、`PARTIAL`、`NONE`。部分读取、抽样或存在遗漏时不能写`FULL`。
- 除`HYPOTHESIS`和`GAP`外，每个claim至少有一项`sourceRefs`。计算claim还必须有实际存在的脚本和结果。
- `materialId`必须直接取自`/workspace/input/materials.json`，不能写文件名、扫描序号、自造路径或主机路径。
- `role`只能是`SUPPORT`或`COUNTER`。正常解释和反证也要回到真实材料位置；没有发现时在`counterSummary`如实说明。
- Excel用`EXCEL_RANGE`及Sheet；CSV用`CSV_LINES`；Parquet/DuckDB用`PARQUET_ROWS`/`DUCKDB_ROWS`及表名；PDF用`PDF_PAGE`及页码；Word、图片或扫描件使用`WORD_ANCHOR`、`IMAGE_REGION`或`NEAREST`并说明定位限制。
- 表格证据是连续区间时，Excel、Parquet、DuckDB写`startRow`和`endRow`，CSV写`startLine`和`endLine`。
- 表格证据是不连续的具体行时，Excel、Parquet、DuckDB必须写升序且不重复的`rows`数组，CSV必须写升序且不重复的`lines`数组。数组中只能包含本条claim实际使用的原始行，禁止用最小行至最大行包住中间无关数据。
- 离散行数组和连续起止范围互斥，同一定位不得同时出现。系统只认结构化定位，不会从`description`或回复正文猜测、补齐或扩大证据行。
- `scriptPath`和`resultPath`是`/workspace`下的相对路径，必须指向实际使用且存在的文件。脚本和结果不能替代原始材料。
- `limitations`是字符串数组；没有已知限制时为空数组。
- 普通寒暄或无案件事实的回复写`{"claims":[]}`。结构不完整时系统会保留真实回答，但标记为“证据不可核验”。
