# 调查笔记与首次结案合同

把`/workspace/thin-inventory.json`作为`/data`物理文件覆盖的唯一母表；把`investigation-state.md`、`material-review-ledger.md`和`evidence-ledger.md`作为唯一调查进度入口。`/workspace/input/materials.json`只提供精确`materialId`路径映射，不能替代物理薄清单。底稿和过程使用中文。

## 1. investigation-state.md

记录案件问题、对象、期间、口径、最多3个当前主线、下一步、失败项和总体进度。另写：

```text
结案覆盖：OPEN | PARTIAL | FULL
```

物理清点或结构盘点完成不等于内容审阅完成。只有全部业务材料内容完整审阅且没有未解决覆盖缺口时才能写`FULL`。

另写交易分析适用性，并在`## 问题覆盖矩阵`下记录全部候选问题：

```text
交易分析：REQUIRED | NOT_APPLICABLE
交易分析不适用理由：不适用时写明业务理由；REQUIRED时写“无”

### Q001
状态：OPEN | IN_PROGRESS | VERIFIED | EXCLUDED | GAP | NOT_APPLICABLE
来源：举报主问题、材料发现、主档关系、既有疑点或派生问题
分析维度：MAIN_QUESTION | SUBJECT | ACCOUNT_COVERAGE | DIRECT_FLOW | INDIRECT_FLOW | CROSS_SOURCE | TEMPORAL | BUSINESS_EVENT | RELATIONSHIP | COUNTER_EVIDENCE | MATERIAL_GAP
专项Skill：Skill名称；多个用逗号分隔；无需专项写NONE
选择理由：为什么该问题需要这些Skill，或为什么普通读取/临时脚本已经足够
问题：可核查的业务问题
对象与角色：调查对象、付款、收款、经办、审批、关联人员或未知
已检查范围：文件、Sheet/页、期间、账户、关系层级和字段
支持假设：当前问题成立时应看到什么
正常解释：需要排除的正常业务或生活解释
反向假设：问题不成立或方向相反时应看到什么
证据或缺口：E001等；没有材料时写GAP及原因
未覆盖与原因：无，或尚未覆盖的账户、期间、主体、来源及原因
下一步：继续核查、调证、人工确认或无
报告Claim：C001等；不进入报告写“无”及原因
```

`OPEN`和`IN_PROGRESS`不是结案状态。并行推进不超过3个主线只限制当前工作量，不限制问题总数。问题来自材料或碰撞结果时必须立即追加；不得在报告前只保留最早的三条主线。首次结案前全部问题必须达到`VERIFIED | EXCLUDED | GAP | NOT_APPLICABLE`。

在`## 技战法覆盖`下为目录中的每篇正式技战法建立记录；没有正式技战法时明确写“当前目录无正式技战法”，不得用README充数：

```text
技战法目录检查：COMPLETED | NONE

### T001
技战法路径：20-技战法/示例.md
标题：办案人员可理解的标题
状态：APPLICABLE | COMPLETED | DATA_MISSING | NOT_APPLICABLE
适用场景：目录摘要中的业务场景
所需材料：技战法要求的材料类别
现有材料：实际已核对的文件、字段或“无”
缺失材料：缺少的材料及调取方向；没有写“无”
适用判断：为什么适用、缺数据或不适用
转入问题：Q001等；未执行写“无”及原因
```

`APPLICABLE`不是结案状态。现有材料满足前提时必须读取完整技战法并转入问题覆盖矩阵；`DATA_MISSING`记录材料缺口和下一步调取动作；`NOT_APPLICABLE`必须有案件对象、行为、期间或业务范围上的明确理由。技战法中的弱信号仍只是候选，必须结合当前原件、反证和人工判断。

材料含账户、交易、报销、票据或资金事件时，`交易分析`必须为`REQUIRED`，问题覆盖矩阵至少分别记录`ACCOUNT_COVERAGE`、`DIRECT_FLOW`、`INDIRECT_FLOW`、`CROSS_SOURCE`、`TEMPORAL`、`COUNTER_EVIDENCE`和`MATERIAL_GAP`。其中`INDIRECT_FLOW`要检查与调查对象现任及历史同事、亲属、关联人员、账户和企业的交集及一跳/两跳候选路径；没有命中也记录实际范围和限制，不能把无命中写成不存在。确实没有交易事件时才可写`NOT_APPLICABLE`，并提供非空业务理由。

在`## 委派任务`下记录每项委派；没有委派时写“无”：

```text
### D001
状态：PENDING | RECEIVED | VERIFIED | REJECTED
任务：仅限机械性处理
输入范围：物理文件路径或有界范围
输出路径：/workspace下路径
定位粒度：文件、Sheet/页和原始行/唯一流水
候选上限：1至200的整数
主调查抽核：未抽核 | 已抽核
拒绝原因：无，或不可用原因
```

子Agent只返回不超过上限的候选索引及必要定位，禁止返回整表、全量正文或巨型JSON，禁止判断关系、异常、事实、疑点和结论。收到结果标`RECEIVED`；主调查回查关键原件后只能转为`VERIFIED`或`REJECTED`。首次结案前不得残留`PENDING`或`RECEIVED`，延迟结果不得触发第二次结案。

## 2. material-review-ledger.md

以物理薄清单为基准，为每个相对路径建立唯一`M001`记录。路径集合和文件数必须与物理薄清单一致，不能用`materials.json`登记数代替。`.DS_Store`参与对账但可标`NON_MATERIAL`；ZIP在母表中仍是一个物理文件，内部成员另记且不增加母表数量。

```text
## M001
路径：/data下相对路径
状态：UNREAD | PARTIAL | REVIEWED | FAILED | NON_MATERIAL
物理清点：INVENTORIED
结构盘点：UNREAD | PARTIAL | REVIEWED | FAILED | NON_MATERIAL
内容审阅：UNREAD | PARTIAL | REVIEWED | FAILED | NON_MATERIAL
格式与结构：真实格式、Sheet、页数或正文结构
实际覆盖：已查看的Sheet及行范围、页码、正文或图片
主要内容：业务、期间和主体
可能有用的点：与调查问题相关的内容
可疑线索：无，或证据编号E001
限制与失败：未覆盖范围、解析错误或字段歧义
下一步：回查、碰撞或人工核验动作
```

ZIP另加`ZIP成员数`、`ZIP成员检查：REVIEWED | PARTIAL | FAILED`和`成员清单路径`。

必须区分三个层次：进入薄清单只证明物理清点；识别格式、Sheet或页数只证明结构盘点；读取业务字段和真实内容后才记录内容审阅。不得虚称薄清单全部文件均已内容扫描。Excel全部Sheet和真实数据范围、PDF全部页、Word/RTF完整可读正文、图片实际查看或OCR后，内容审阅才能标`REVIEWED`。`NON_MATERIAL`只用于`.DS_Store`等系统元数据。

单文件交互读取完成后，在下一内容工具前回写该记录。批量脚本或机械子Agent可在一次调用中处理多个物理文件，但必须在处理过程中直接生成逐文件记录和有界`batch-summary.json`，至少汇总已处理、候选、失败、冲突、身份歧义和未覆盖数量及记录编号。模型不得在批次结束后凭记忆伪回填；主调查读取汇总后回查全部候选、失败、冲突、身份歧义、关键证据和有限无命中抽样。失败保留`FAILED`，部分覆盖保留`PARTIAL`。

## 3. evidence-ledger.md

每个发现使用稳定编号并记录：

```text
## E001
状态：CANDIDATE | VERIFIED | REFUTED | HYPOTHESIS | GAP
问题：对应调查问题
主体与角色：付款、收款、报销、审批或未知
事实或疑点：当前证据支持的内容
原始来源：由文件、Sheet/页和原始行或唯一定位共同组成
文件：薄清单中的相对路径；缺口写不适用及原因
materialId：精确路径对应的Spring标识；未编目写“无（未编目）”
Sheet/页：Sheet名、页码，或不适用及原因
Excel原始行/唯一流水：原始行号、流水号或其他唯一定位
字段角色：姓名、账号、摘要、交易码等字段的业务角色
收付方向：付款方到收款方，或不适用及原因
口径：期间、币种、金额、笔数、筛选、去重、冲正和方向
关键字段：日期、金额、交易对手、摘要和交易码
方法：脚本、结果、筛选和排除口径
支持证据：独立来源
反证与正常解释：已检查内容和结果
限制与缺口：材料或人工核验缺口
主调查回查：未回查 | 已回查，并记录位置
下一步：可执行动作
报告Claim：C001等；未进入报告写“无”
```

升级`VERIFIED`必须满足主调查亲自回查、角色方向明确、来源可精确定位、口径明确并记录支持、反证和限制。否则保持`CANDIDATE`、`HYPOTHESIS`或`GAP`。

只有文件级“可能有用”而没有具体记录定位时，保持为材料提示，不升级为案件事实。

`materialId`与物理相对路径必须精确对应`materials.json`同一条目录记录，不按文件名或末级名称匹配。根目录同名文件不能借用子目录条目；未编目文件即使有相关记录，也只能保持`CANDIDATE`或`GAP`且`报告Claim`写“无”。

有金额、频次、集中度、时序、方向、多跳路径、来源冲突或相对已声明基线偏离等客观触发依据的疑点，不得因身份未知、关系未知、存在正常解释或出现反证而从台账和报告中删除：

- `CANDIDATE | HYPOTHESIS | GAP`且尚无足以排除的反证：报告进入`待核疑点`；
- `REFUTED`或当前反证、正常解释较强：报告进入`暂拟排除的疑点`，同时写原疑点触发事实、反证来源、暂拟排除理由、剩余不确定性和重新打开条件；
- 纯粹想象、没有任何客观触发依据：不建立证据记录。

首次结案时，所有`CANDIDATE | HYPOTHESIS | REFUTED | GAP`证据记录都必须填写`报告Claim`并出现在最终报告；不得写“无”后静默丢弃。`REFUTED`记录映射为`HYPOTHESIS` Claim，并使用`COUNTER` sourceRefs和`counterSummary`呈现反证，不得伪装成已证事实。

人员关系和任职单位只接受原件明示关系、任职或单位字段。强标识只证明记录同一性，不单独证明亲属或任职。同事或共同任职只证明工作关系，不等于社会关系；同姓、同地址、单位名称或发生交易均不能推出亲属关系及任职事实，交易链也不能推出资金来源。冲突记为`REFUTED`或`GAP`。

进入`RELATION` claim的证据必须把`字段角色`写成“明确关系字段：原件字段名及取值角色”；交易对手、同事或任职字段不得冒充亲属关系字段。

同一经济事件可能同时出现在收付双方Sheet。优先用柜员流水号、CPC流水号或其他强流水ID碰撞并保留全部原始行；无法强匹配时披露重复风险，禁止把借贷两侧金额相加。

同一指标有不同金额、笔数或期间时，分别记录口径和原始行集合，解释镜像、重复、冲正、方向、期间或筛选差异；未解释前不得发布。

没有可比基线、正常解释排除和反证检查时，禁止用“规避监管”“掩饰”“通道”“远超正常”“异常”等词作事实定性，只能写为候选、假设或待核缺口。字段缺失、解析失败、未覆盖和未知不得转换为0或“未发生”。`REFUTED`保留排除依据；无命中不能证明不存在。

## 4. 计算结果合同

数量、金额和月度等分组由可复跑脚本输出到`calculationRefs.resultPath`，禁止人工抄表。结果JSON包含`publishedMetrics`数组，每个指标使用：

```json
{
  "metricCode": "METRIC-001",
  "currency": "CNY",
  "totalCount": 2,
  "totalAmount": "200000.00",
  "groups": [
    {"group": "2018-01", "eventIds": ["T1", "T2"], "count": 2, "amount": "200000.00"}
  ],
  "events": [
    {"eventId": "T1", "amount": "100000.00", "observations": [
      {"relativePath": "流水/甲.xlsx", "sheet": "付款", "row": 12, "strongEventId": "柜员流水号-A"}
    ]}
  ],
  "inputRows": [
    {"relativePath": "流水/甲.xlsx", "sheet": "付款", "row": 12, "disposition": "INCLUDED", "eventId": "T1", "reason": "满足口径"}
  ],
  "missingValuePolicy": "EXCLUDE_AND_DISCLOSE",
  "missingRowCount": 0,
  "transactionChecks": {
    "domain": "TRANSACTION",
    "strongIdFields": ["柜员流水号", "CPC流水号"],
    "mirrorCollisionChecked": true,
    "eaReimbursementPaymentChecked": true
  },
  "reportBlock": "2笔，合计20万元"
}
```

- `totalCount`等于去重后`events`数量，`totalAmount`等于事件金额之和；分组笔数和金额分别等于总数和总额，`eventIds`无重无漏覆盖全部事件。
- `FULL`列出全部输入源行及`INCLUDED | EXCLUDED`处置；排除行有原因，纳入行映射到事件。不能只列5个样本。
- 每个事件保留全部原始观测行。交易类观测有强流水ID，跨Sheet碰撞并完成镜像去重；明确核查`EA报销支付`。
- `reportBlock`由脚本生成并原样进入报告。`200000.00`显示为“20万元”，不写裸数`200000`。
- 缺失值只排除并披露，不能按0计算。终检只验证结构和算术勾稽，不判断筛选口径、关系或案件事实是否正确。

## 5. 强制检查点

第一次分析内容、单文件交互读取后、批量处理生成逐文件记录和批次汇总后、有意义发现后、连续3次工具调用仍未完成当前问题、接收子Agent结果后、切换主线和写报告前，先更新状态与台账。无发现也记录覆盖、失败、限制和下一步，但主Agent无需在对话中逐个复述无命中文件。

## 6. 首次结案门槛

- 报告事实、计算、关系和疑点紧邻引用证据编号；证据进一步映射到`claimCode`。
- 报告包含`待核疑点`和`暂拟排除的疑点`；所有有客观触发依据的候选均可从算法结果追到证据台账和报告，反证不能让原疑点静默消失。
- 最终调查报告和独立复核结果必须有实质正文，`final-claims.json`至少一项；没有已证事实时交付`GAP`或`HYPOTHESIS`，不得用0字节报告或空claims结案。
- 物理薄清单与材料台账路径、数量逐项一致，物理、结构、内容三类状态不混淆；ZIP字段齐全。
- 委派全部达到`VERIFIED`或`REJECTED`，`VERIFIED`记录主调查抽核。
- 问题覆盖矩阵没有`OPEN`或`IN_PROGRESS`；交易分析适用时已覆盖全部账户、直接与间接路径、跨来源、时序、反证和材料缺口。
- 技战法目录已完成覆盖；所有`APPLICABLE`技战法均已转入问题并执行完成，缺数据和不适用项有明确理由，报告保留简要覆盖结果。
- 报告与`final-claims.json`的claim集合、陈述、物理路径和`materialId`一致；未编目文件不借用标识。
- 计算结果通过总数、总额、分组、事件、全部输入源行、镜像去重和报告文本勾稽。
- 报告使用中文。授权案件Workspace、调查笔记、证据台账、Claim、复核报告及办案页面中的身份证号、银行卡号、账号、手机号按原件保留，以便核对主体同一性、收付方向和原始证据；不得擅自掩码或改写。生产日志、普通通知和非办案展示仍不得带出原始值。

最终报告、claims和独立复核处理完成后，主调查Agent亲自运行唯一入口：

```bash
python3 /root/.hermes/skills/cxba/cxba-case-investigator/scripts/final_investigation_gate.py --workspace /workspace
```

只有本次运行输出`FINAL_GATE_PASS`且退出码为0才授权首次结案。`FINAL_GATE_FAIL`、未运行或运行失败时，不得声称完成或`FULL`。门禁后如报告、claims、台账、目录或计算结果变化，原PASS立即失效并必须重跑；脚本不使用hash、版本或seal自动检测后续修改。不得修改薄清单、删除失败记录或人工目测绕过。首次结案后不因迟到通知再次提交报告。
