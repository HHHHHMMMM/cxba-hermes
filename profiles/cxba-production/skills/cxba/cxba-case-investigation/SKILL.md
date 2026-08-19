---
name: cxba-case-investigation
description: CXBA完整案件调查入口。仅当用户明确要求遍历案件全部材料、开展整案调查并形成完整调查报告时使用；指定文件、Sheet、人员、期间、费用类型或指标的有界问题必须改用 cxba-interactive-data-analysis。完整调查中允许受控委派机械处理，主调查Agent负责核证和成文，独立复核Agent最终回查。
---

# CXBA案件调查入口

先判断是否真的属于完整案件调查。用户只指定文件、Sheet、人员、账户、期间、费用类型、交易方向或指标时，即使使用了“调查”“核查”字样，也不得加载本Skill，不得建立全目录薄清单、逐文件台账或运行完整案件终检；改用`cxba-interactive-data-analysis`，并按材料类型加载表格、来源对账或其他专项Skill。只有用户明确要求遍历全部材料、覆盖整案并形成完整调查报告时，才继续以下流程。

开始读取材料内容前加载`cxba-analysis-notebook`；本完整案件以`/workspace/material-review-ledger.md`作为公共逐文件笔记，不重复创建`analysis-notebook.md`。形成最终报告和结案回复时加载`cxba-claim-delivery`，完整案件再额外执行下文完整终检。

本入口不展开全部专项Skill。主调查Agent先建立问题覆盖矩阵；每个问题开始执行时再按`cxba-analysis-router`选择并通过`skill_view`读取最小专项Skill组合，已经在当前上下文完整读取的Skill不重复加载。不得把所有专项Skill正文一次性塞入Prompt。

完整案件还必须执行公共技战法覆盖检查：先读取正式技战法的目录摘要，只对现有材料满足前提的技战法读取完整正文并转成调查问题；缺少材料的记录调证缺口，明确不适用的说明理由。技战法是最低公共检查集，不限制主调查继续自主发现新问题。

主调查Agent负责理解举报、确定口径、组织调查、核证证据并写报告。可以把以下机械性工作委派给材料处理子Agent：格式识别、OCR、RTF或Office解析、结构盘点、候选定位和确定性脚本计算。

加载入口后，先生成或读取`/workspace/thin-inventory.json`，把它作为`/data`物理文件覆盖的唯一母表；`/workspace/input/materials.json`只用于把已登记材料映射到`materialId`，不得用其条目数代替物理文件数。再创建或读取`/workspace/investigation-state.md`、`/workspace/material-review-ledger.md`和`/workspace/evidence-ledger.md`，然后开展内容分析。

薄清单中的每个物理文件都必须单独记录并对齐台账；`.DS_Store`保留在覆盖对账中但可标为`NON_MATERIAL`，ZIP必须记录并检查内部成员。单文件交互读取后在下一内容工具前回写；批量脚本或机械子Agent可以一次处理多个文件，但必须在处理过程中直接生成逐文件记录和有界批次汇总，模型不得在结束时凭记忆批量回填。主调查Agent读取汇总后回查全部候选、失败、冲突、身份歧义、关键证据和有限无命中抽样，不在对话中逐个复述无命中文件。发现线索时立即记录精确定位（原文件、Sheet或页码、Excel原始行或唯一流水、内容摘要）、字段角色、收付方向、口径和核验状态；不得只把发现保留在对话上下文或工具输出中。

材料处理子Agent只能生成解析结果和候选底稿，不得独立认定案件事实、疑点、关系或结论。主调查Agent必须亲自回查关键原件、Sheet或页码、原始行、金额和收付方向，亲自形成证据台账和报告；子Agent摘要不能替代原件证据。

委派任务必须按`PENDING → RECEIVED → VERIFIED | REJECTED`落账。子Agent只返回有上限的候选索引，不得判断关系、异常或结论，不得输出巨型JSON。全部委派达到终态并经主调查Agent抽核后，才能完成`draft-report.md`并调用未参与材料处理的独立复核Agent；材料处理子Agent不得充当最终复核Agent。延迟返回的子Agent结果不得触发第二次结案。

最终报告、`final-claims.json`和独立复核处理完成后，主调查Agent必须亲自运行唯一终检入口：

```bash
python3 /root/.hermes/skills/cxba/cxba-case-investigator/scripts/final_investigation_gate.py --workspace /workspace
```

只在本次运行明确输出`FINAL_GATE_PASS`后首次提交结案回复；输出`FINAL_GATE_FAIL`、未运行或运行失败时，均不得声称完成或使用`FULL`。门禁后如果报告、claims、台账、目录或计算结果发生任何修改，原PASS立即失效，必须重新运行；脚本不使用hash、版本或seal自动追踪后续变化。不得自行用目测替代、修改薄清单或删除失败记录绕过门禁。

`/data`只读，脚本、状态、子Agent底稿、结果和报告写入`/workspace`。相互独立的机械性任务可以批量或并行委派；依赖前一步结果的操作必须按顺序执行。过程与最终回复使用中文。授权办案人员使用的案件Workspace、调查笔记、证据台账、Claim、复核报告和办案页面可以保留完整身份证号、账号、卡号、手机号等原始标识，以便同一性、方向和原件回查；不得为了展示方便擅自掩码或改写证据值。生产日志、普通通知和非办案展示边界仍不得记录原始材料或个人敏感信息。详细流程见`cxba-case-investigator`。
