---
name: cxba-investigation-knowledge
description: 查阅办案知识，并持续共创专题知识、全案经验或既有Skill更新。
---

# CXBA办案知识与共创

公共办案知识以只读`/knowledge`中的Markdown和双向链接为准。知识只提供方法参考，不能替代案件材料、证据验证、反证和人工判断。

## 查阅知识

1. 只有当前任务可能受益于已有方法时才查阅；不要把知识搜索变成每轮固定动作。
2. 使用文件搜索、目录和读取工具在`/knowledge`自主定位相关Markdown，可从题名、正文、frontmatter、标签和`[[双向链接]]`继续探索，不依赖RAG、向量库或固定关键词枚举。
3. 只读取与当前问题相关的有限笔记，区分`status: test-only`和正式维护内容。引用知识后仍须回到案件材料验证。

## 识别共创任务

只有系统明确要求知识共创，并提供`/workspace/.cxba-coauthor/source.json`时执行本节。必须先完整读取该文件：

- `sourceScope: SESSION`：`cutoffRunId`只是截止定位。文件仅保留调查空间开始到截止Run的用户问题、Steer和AI最终答复，不包含工具调用、内部推理或运行事件。普通专题知识共创只能形成`DATA_SOURCE`、`TECHNIQUE`、`MISCONDUCT_PATTERN`或`INTEGRITY_RISK`，不得生成或修改Skill。
- `sourceScope: CASE`：必须综合案件记忆、全部普通调查空间、疑点、线索、固化证据关系、已发布成果和执行过程。该入口只能形成`CASE_EXPERIENCE`，不得混入单个专题技战法或纯技术Skill。
- `skillMaintenance`存在：这是SuperAdmin单独发起的既有Skill维护入口，只能形成`SKILL`更新。以来源对话和文件中附带的当前Skill正文判断是否值得更新；只有确需核实可复用技术产物时，才读取只读来源Session中的脚本或结果文件。

共创不是一次性生成。首次完成来源分析后，即使仍有问题需要用户确认，也必须先写出一个可讨论的当前草稿和manifest，再在回复中提出问题；不得让右侧草稿面板一直空着等待用户先回答。之后每当观点、结构、边界或目标发生变化，都要更新同一组临时文件。不要创建互不关联的多份草稿。

## 调查空间入口：专题知识

一次只形成一个最适合当前调查过程的主候选，用户可以在共创对话中要求调整分类。不得为了填满目录而同时生成多份候选。

### 新型数据源

发现当前案件材料中尚未提供、但可跨案件复用并可能依法调取的材料来源时，整理为`DATA_SOURCE`，例如外卖及收货地址、打车、代驾、物流、通信、门禁或其他活动记录。正文写清：可以解决的问题、依法调取渠道与联系人角色、必要手续和前置条件、交付字段或文件形态、使用限制、时效、常见缺口、相关知识和维护依据。某个现有银行文件、系统导出或模板的表头、借贷标志、负金额和重复行解析方法属于技术Skill/reference，不是新型数据源。不得把未经核实的渠道或权限写成确定事实。

### 技战法

出现可跨案件复用的调查目标、信号组合、材料碰撞方式、判断依据和反证路径时，整理为技战法。例如以外卖地址、实际活动地址等多源信息核验疑似隐匿房产，属于办案技战法，不是表格技术教程。

技战法正文使用办案语言，写清：名称、适用场景、所需材料、办理步骤、判断依据、反证与排除、适用边界、相关知识。弱观察不得直接写成结论。

### 新型违规违纪手法

只有调查过程形成了可复核的新表现、新路径或易漏查环节时，才整理为`MISCONDUCT_PATTERN`。正文写清：经核实的表现、可观察信号、验证路径、反证条件、易误判情形、相关知识和维护依据。单一异常、未经核实的猜测或仅与本案个体有关的事实不得沉淀为新手法。

### 廉洁风险点

发现岗位、制度或流程中可复用的廉洁风险认识时，整理为`INTEGRITY_RISK`。正文写清：涉及岗位、制度或流程，风险表现，现有控制，可能缺口，核查材料，验证与反证，相关知识和维护依据。不得仅凭结果倒推制度存在漏洞，也不得把尚未核实的可能性写成确定风险。

### Hermes Skill（仅维护入口）

Excel、Python、文档解析、通用计算或工具使用等纯技术能力不进入普通专题知识共创。只有`source.json`明确包含`skillMaintenance`时，才维护其中指定的既有公共Skill；普通入口只提示用户改由SuperAdmin发起“维护分析能力”，不得自行生成Skill草稿。

维护时先完整读取`skillMaintenance.currentSkill`，目标以`skillMaintenance.targetRelativePath`为准。不得搜索后改投其他Skill，不得新建Skill或改变目标。只有来源对话确实表明存在可复用的技术缺口时才更新；需要核验稳定技术产物时，才读取来源Session中的相关脚本或结果文件。

Skill维护只输出完整`SKILL.md`，不新增references、scripts或assets。若来源没有形成稳定、跨案件复用的技术规则，应明确不建议修改，不生成ready草稿。

## 案件入口：全案经验

全案经验描述一个案件如何整体组织和推进，包括案件类型与目标、材料编排、调查空间划分、分析顺序、疑点到证据闭环、反证与复核、协同方式和适用边界。它不是多个边角技战法的简单拼接；相关技战法使用Obsidian链接引用，不复制正文。

## 临时文件合同

持续维护以下两份文件，`manifest.json`最后写入：

1. `/workspace/.cxba-coauthor/draft.md`
   - `DATA_SOURCE`：完整新型数据源Markdown；
   - `TECHNIQUE`：完整技战法Markdown；
   - `MISCONDUCT_PATTERN`：完整新型违规违纪手法Markdown；
   - `INTEGRITY_RISK`：完整廉洁风险点Markdown；
   - `CASE_EXPERIENCE`：完整全案经验Markdown；
   - `SKILL`：完整可发布`SKILL.md`，必须含有效的`name`和`description` frontmatter。
2. `/workspace/.cxba-coauthor/manifest.json`
	- 顶层必须是单个JSON对象，禁止写成数组，禁止一份草稿声明多个候选；
	- `artifactType`：`DATA_SOURCE`、`TECHNIQUE`、`MISCONDUCT_PATTERN`、`INTEGRITY_RISK`、`CASE_EXPERIENCE`或`SKILL`；
   - `title`：办案人员可理解的标题；
   - `operation`：`CREATE`或`UPDATE`；
   - `targetRelativePath`：正式目标相对路径；
	- `recommendation`：用自然中文说明为什么新建或补充。

格式必须严格类似：

```json
{
  "artifactType": "TECHNIQUE",
  "title": "关系事实分组核查",
  "operation": "CREATE",
  "targetRelativePath": "20-技战法/关系事实分组核查.md",
  "recommendation": "现有知识未覆盖该核查方法，建议新建"
}
```

目标约束：

- `TECHNIQUE`只能位于`20-技战法/*.md`；
- `DATA_SOURCE`只能位于`10-新型数据源/*.md`；
- `MISCONDUCT_PATTERN`只能位于`30-新型违规违纪手法/*.md`；
- `INTEGRITY_RISK`只能位于`40-廉洁风险点/*.md`；
- `CASE_EXPERIENCE`只能位于`50-案件经验/*.md`；
- `SKILL`只能使用`skill-name/SKILL.md`；
- 路径不得是绝对路径，不得包含`..`；发现真实相似目标时优先`UPDATE`。

## 边界

- `/knowledge`和公共Skill目录在共创运行中始终只读。不得直接修改、移动或删除正式目标；是否发布由Spring在人工确认后执行。
- 草稿只写泛化方法，不写姓名、企业名称、账号、完整证件号、手机号、原始流水或原始材料正文；即使来源是测试或虚构材料，也必须改写为“人员甲”“企业A”等通用角色。来源只使用受控的案件、调查空间和Run定位。
- 用户确认前不得声称已经写入正式知识或公共Skill。
- 最终回复说明当前建议、仍需用户决定的内容，以及右侧草稿已更新；不要向用户罗列内部控制字段。

## 验证

- 来源范围与入口一致，SESSION没有缩成单个Run，CASE没有混入专题或技术候选。
- 已查找真实相似知识；仅Skill维护入口完成指定Skill的完整正文核对。
- 两份临时文件存在且可解析，类型、目标根目录和CREATE/UPDATE状态一致。
- 草稿可供下一次案件直接复用，包含判断依据、反证和适用边界，不是空泛总结。
