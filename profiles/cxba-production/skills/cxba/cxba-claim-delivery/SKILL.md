---
name: cxba-claim-delivery
description: CXBA全部分析Skill的公共结论交付合同。最终回答包含案件材料事实、计算、关系、规律候选、业务判断、假设或材料缺口时使用，把每条陈述绑定到真实materialId、相对路径、原始定位及计算产物，并运行通用预检。
---

# CXBA Claim 交付与证据身份校验

读取[最终 Claim 交付合同](references/final-claim-delivery.md)，只为本轮最终回答实际出现的陈述生成`/workspace/evidence-items/final-claims.json`。不要复制材料正文，不要把笔记或脚本结果当作原始证据。

## 最终步骤

1. 把准备发送的最终回答原样写入`/workspace/final-answer.md`，在对应陈述旁紧邻写`[C001]`等claimCode。
2. 按公共合同写入`final-claims.json`；事实和计算必须有真实原件定位。计算Claim必须声明`metricCodes`，引用实际执行的脚本和UTF-8 JSON结果；Excel等二进制交付物只放入`artifactPaths`，严禁作为文本读取。结果JSON中的`reportBlock`必须由脚本生成并原样进入Claim陈述和最终回复；存在二进制交付物时，脚本必须落盘后重新打开交付物，把原始复算值和交付物回读值分别写入`sourceValue`、`artifactValue`，两者不一致不得交付。`materialId`、`relativePath`和`sandboxPath`只能从当前`/workspace/input/materials.json`同一条记录逐字复制，严禁凭记忆填写、按文件名猜测或借用同名副本。脚本需要记录材料身份时必须运行时读取`materials.json`，严禁硬编码标识。
3. 正式回复只要引用任何原始证据，必须亲自运行以下证据校验工具，不得省略、目测替代或把旧Run的PASS当作本轮PASS：

```bash
python3 /root/.hermes/skills/cxba/cxba-claim-delivery/scripts/preflight_claim_delivery.py --workspace /workspace --answer /workspace/final-answer.md
```

4. `terminal`会把脚本的完整标准输出和退出码返回当前主Agent。必须完整读取`CLAIM_DELIVERY_FAIL`后的每条错误，把它们逐项作为修正清单；修正相关Claim、笔记、脚本、结果或回复后重新运行同一命令。不得使用`|| true`、忽略退出码、截掉错误输出，或把“工具调用已结束”误当成校验通过。
5. 工具会同时检查Claim、最终回复、分析笔记及Claim实际引用的计算脚本和结果中的材料身份；按文件类型校验二进制交付物结构，并检查计算结果、交付物回读值和发布文本勾稽。只要存在路径与`materialId`不一致、二进制格式损坏、复算值不一致或`reportBlock`未原样发布，证据校验即失败。
6. 只有本轮最新一次执行退出码为0且输出首行为`CLAIM_DELIVERY_PASS`后，才可把有原始证据支持的陈述称为“已核验”并逐字发送`final-answer.md`。失败时严禁声称`CLAIM_DELIVERY_PASS`、证据已核验或全部交付成功；必须修正全部不一致后重跑。Gateway在正式回复后返回的`INVALID`只是最后兜底，不得替代回复前的校验与修正循环。

没有原始证据的内容可以按`HYPOTHESIS`或`GAP`正常完成任务，明确写出推测或材料缺口，并允许`sourceRefs`为空；这不等于证据校验通过，也不得伪装成`FACT`、`CALCULATION`、`RELATION`或`FINDING`。若原本声称有证据但校验失败，可以修正引用，或诚实降级为`HYPOTHESIS`/`GAP`后完成；不得仅删除有效证据定位来逃避错误。

证据校验工具检查Claim结构、正文引用、materialId与完整相对路径、原始定位、脚本/结果文件存在、二进制格式、原始复算值与交付物回读值，以及脚本发布文本与最终回复的一致性；它不判断筛选口径、关系或业务结论是否正确。专项Skill仍负责真实读取、口径、确定性计算和原件回查；完整案件还要执行自己的完整终检。Gateway最终返回的`VERIFIED | NOT_PROVIDED | INVALID`是证据交付状态，不是任务完成状态；`INVALID`时任务可以结束，但正文必须明确证据不可核验，不能保留“已核验”表述。
