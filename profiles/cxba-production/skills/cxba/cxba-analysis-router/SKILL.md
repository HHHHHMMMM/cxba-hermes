---
name: cxba-analysis-router
description: CXBA案件分析的强制总入口。收到任何材料读取、数据分析、调查、核查、汇总、技战法或证据复核请求时先使用，先判断任务边界，再选择最小专项Skill组合；Router本身不读取材料、不计算、不写结论。
---

# CXBA 分析路由

每次开始或恢复案件分析时，先完成本路由，再读取材料。只选择解决当前问题所需的最小 Skill 组合，不因出现“调查”“核查”字样自动启动完整案件调查。

## 路由表

按下列优先级选择主流程：

1. 用户要求审查已有报告、claims、脚本或调查结论：加载`cxba-evidence-review`。
2. 用户明确要求遍历全部案件材料、覆盖整案并形成完整调查报告：加载`cxba-case-investigation`，再加载`cxba-case-investigator`。
3. 用户只问指定文件、Sheet、人员、账户、期间、费用类型、交易方向或指标：加载`cxba-interactive-data-analysis`。
4. 用户只要求查看材料目录、格式、Sheet、页数或结构：加载`cxba-material-profiling`。

在第3项专项分析中，再按实际问题追加：

- 跨文件汇总，或多来源可能重复、包含、补充、冲突后再求合计：`cxba-source-reconciliation`并配合`cxba-safe-tabular-analysis`；
- 报销或费用的集中、突增、重复金额、拆分、阈值、非工作时段：`cxba-expense-pattern-analysis`；
- 夜间交易、快进快出、汇聚、发散、回转、多跳链路：`cxba-temporal-graph-analysis`；
- 不规则混合材料的有界原件调查：`cxba-raw-material-investigation`；
- 表格读取、确定性聚合或复算：`cxba-safe-tabular-analysis`；
- 文件结构未知：`cxba-material-profiling`。

一个问题同时命中多项时，以`cxba-interactive-data-analysis`维持用户口径，只追加真正需要的专项 Skill。若“专项”与“完整案件”均有合理解释，默认专项；只有两种解释会显著改变读取范围时才向用户提出一个最小澄清问题。

## 两项公共合同

- 任何将读取案件材料内容的流程，在第一次内容读取前必须加载`cxba-analysis-notebook`；每处理完一个实际文件，先立即落笔记，再读取下一个文件。专项只记录实际处理的文件，完整案件才要求覆盖全部物理文件。
- 任何最终回答只要包含材料事实、计算、关系、规律候选、业务判断、假设或材料缺口，发送前必须加载`cxba-claim-delivery`，按统一格式生成并引用claims。复核流程还要同时保留`cxba-evidence-review`自己的逐条verdict合同。

不得创建`task-scope.json`，不得为单个案件问题发明专项门禁。确定性检查只使用公共 Skill 已提供的通用检查；完整案件额外执行其完整终检。

## 扩展新技战法

新增技战法时只做三件事：在本路由表增加清晰且不重叠的触发条件；新增一个只包含该技战法方法与边界的专项 Skill；补路由和专项契约测试。不得复制公共笔记或 Claim JSON 合同，也不得把案件特定人数、金额、文件名或答案写进 Skill。
