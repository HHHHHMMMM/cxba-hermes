# 证据台账

`/workspace/evidence-items/<evidenceId>.md`保存一条独立证据记录。形成一条证据后，用一次`write_file`写入对应文件；不得维护需要追加的共享JSONL，也不得为了选择追加方式进入工具讨论。

每个文件记录：`id`、`questionId`、`statement`、`status`、来源文件与Sheet、真实字段、原始位置、计算口径和`limitations`。

要求：

- `FACT`只记录原材料或可复现计算直接支持的事实。
- `CANDIDATE`必须同时记录推断链、正常解释和待补证内容。
- 零命中只能记录为当前范围的结果，不能写成事实不存在。
- 需要原始值支撑证据时保留真实值，不生成脱敏占位符；不要附带与核查无关的全量原始行。
