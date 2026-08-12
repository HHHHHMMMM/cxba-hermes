---
name: cxba-safe-tabular-analysis
description: 在Hermes隔离容器中可靠盘点、定位、聚合和交叉核对不规则案件表格，避免猜字段、全量输出、结果截断和宿主环境污染。
---

# CXBA可靠表格分析

## 专项分析路由

先识别用户要求的分析类型，再进入计算：

- 用户询问夜间或非工作时段交易、时间集中、突增、快进快出、汇聚后转出、回转、多跳或连续资金链路时，必须先加载`cxba-temporal-graph-analysis`并遵循其事件标准化和算法流程。完成该加载前，不得编写交易规律分析脚本。
- 本Skill只负责可靠读取、结构确认和通用表格计算，不能用自写的原始Excel全排列脚本替代专项Skill。

## 通用流程

1. `/data`只读；依赖、临时文件、脚本和结果只写`/workspace`。
2. 先查看真实文件清单、工作簿Sheet、标题区、表头位置、合并单元格、公式和数据范围，再确定字段角色与计算口径。不得根据文件名或猜测的第一行表头直接计算。
3. 根据任务选择pandas、openpyxl、命令行工具或`execute_code`。可并行处理独立材料，可一次批量扫描同一工作簿全部Sheet；长时间盘点、转换和计算可后台运行，完成后检查退出状态和结果文件。
4. 探索性查看可以使用短命令或`execute_code`；形成正式数字时，把全量读取、筛选、去重、计算和结果写入一个可复跑脚本，保存到`/workspace/scripts`，结果写入`/workspace/results`。
5. 对话中只读取字段、计数、计算口径、有限样本和有限候选。大结果写JSON或其他结构化文件，只回读需要的聚合与候选，不打印完整DataFrame或全量明细。
6. 最终数字必须来自全部相关记录。抽样只用于识别结构、字段含义和异常格式，不得把抽样结果描述成全量结论。
7. 每个报告数字和候选都保留原始文件、Sheet、行号或页码；回答前回到原件核对关键命中、最大最小值、金额方向和过滤条件。

## 快速开始

Sandbox 镜像已预装本 Skill 所需的解析依赖。禁止在 Run 期间创建虚拟环境、执行`pip install`或从网络下载依赖；直接使用镜像内的`python3`。

盘点文件和Sheet：

```bash
python3 \
  /root/.hermes/skills/cxba/cxba-safe-tabular-analysis/scripts/case_file_profiler.py \
  inventory --root /data --output /workspace/inventory.json
```

一次查看指定工作簿全部Sheet的结构与有限样本：

```bash
python3 \
  /root/.hermes/skills/cxba/cxba-safe-tabular-analysis/scripts/case_file_profiler.py \
  workbook --root /data --file '<真实相对文件名>' \
  --output /workspace/current-workbook.json
```

需要定位内容或深查候选Sheet时使用：

```bash
python3 \
  /root/.hermes/skills/cxba/cxba-safe-tabular-analysis/scripts/case_file_profiler.py \
  search --root /data --term '<检索词>' \
  --output /workspace/locations.json --max-matches 500

python3 \
  /root/.hermes/skills/cxba/cxba-safe-tabular-analysis/scripts/case_file_profiler.py \
  inspect --root /data --file '<真实相对文件名>' --sheet '<真实Sheet名>' \
  --output /workspace/inspect.json
```

这些工具用于定位和理解结构，不能单独证明主体角色或业务含义。样本不足、表头不清或关键Sheet结构特殊时，直接读取对应原始区域核实。

## 计算与结果

- 金额、净额、笔数、排名和分组汇总使用`Decimal`或等价的确定性计算，不由模型心算。
- 结果记录输入文件、Sheet、字段、期间、筛选条件、状态、币种、金额正负、空值处理、去重键和统计对象。
- 日期失败、空值、负数、退款、冲正和重复键分别计数；不同币种不直接相加。
- 跨来源合计前先确认是否存在重复记录或汇总/明细重叠；无法确认时分别报告。
- 输出至少包含`metric`、`value`、`rowCount`、`filters`、`dedupKeys`、`sourceRefs`和`limitations`。
- 终端输出被截断时，以落盘结果为准并按字段或范围读取，不引用截断片段。

工具执行边界见[tool-safety.md](references/tool-safety.md)。
