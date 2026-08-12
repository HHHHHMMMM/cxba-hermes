---
name: cxba-case-investigation
description: Hermes中的CXBA两Agent案件调查入口。主调查Agent直接理解举报和全部原始材料、实施调查并写报告，独立复核Agent直接回查原材料和计算结果。
---

# CXBA两Agent调查入口

本流程只有两个Agent：

1. 当前主调查Agent：直接盘点、阅读、计算全部材料，持续维护工作底稿并形成报告。
2. 独立复核Agent：在报告完成后调用一次，直接回查原材料、来源定位和计算结果。

不存在材料Agent、问题调查子Agent或多层转述。主调查Agent必须亲自理解原件，可以按需使用材料画像、原始材料调查、解析、OCR、表格和计算工具；不要求预先逐个加载固定Skill。

`/data`只读，脚本、状态、结果和报告写入`/workspace`。相互独立的读取和计算可以批量或并行执行；依赖前一步结果的操作必须按顺序执行。详细流程见`cxba-case-investigator`。
