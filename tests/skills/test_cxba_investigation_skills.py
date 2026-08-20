from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml


SKILLS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "profiles"
    / "cxba-production"
    / "skills"
    / "cxba"
)
PRODUCTION_SKILLS_ROOT = SKILLS_ROOT.parent
SKILL_NAMES = (
    "cxba-case-investigation",
    "cxba-case-investigator",
)
VALIDATOR = (
    SKILLS_ROOT
    / "cxba-case-investigator"
    / "scripts"
    / "validate_investigation_notebook.py"
)
FINAL_GATE = (
    SKILLS_ROOT
    / "cxba-case-investigator"
    / "scripts"
    / "final_investigation_gate.py"
)
CLAIM_PREFLIGHT = (
    SKILLS_ROOT
    / "cxba-claim-delivery"
    / "scripts"
    / "preflight_claim_delivery.py"
)
GATEWAY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cxba-gateway.sh"


def parse_skill(name: str) -> tuple[dict, str]:
    content = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
    assert match, f"invalid frontmatter for {name}"
    return yaml.safe_load(match.group(1)), match.group(2)


def test_case_skills_use_minimal_frontmatter() -> None:
    for name in SKILL_NAMES:
        frontmatter, body = parse_skill(name)
        assert frontmatter == {
            "name": name,
            "description": frontmatter["description"],
        }
        assert frontmatter["description"].strip()
        assert body.strip()


def test_generated_artifact_names_do_not_expose_runtime_brand() -> None:
    artifact_pattern = re.compile(
        r"(?i)(?P<path>[A-Za-z0-9_./-]*hermes[A-Za-z0-9_./-]*\."
        r"(?:md|docx|xlsx|xls|csv|pdf|pptx|json|html|txt|png|jpg|jpeg))"
    )
    violations: list[str] = []
    for path in PRODUCTION_SKILLS_ROOT.rglob("*"):
        if path.suffix.lower() not in {".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8")
        for match in artifact_pattern.finditer(text):
            candidate = match.group("path")
            if "/.hermes/" in candidate:
                continue
            violations.append(f"{path.relative_to(PRODUCTION_SKILLS_ROOT)}: {candidate}")
    assert violations == []


def test_full_case_report_uses_neutral_artifact_name() -> None:
    investigator = parse_skill("cxba-case-investigator")[1]
    gate = FINAL_GATE.read_text(encoding="utf-8")

    assert "case-investigation-report.md" in investigator
    assert "case-investigation-report.md" in gate
    assert "hermes-case-report.md" not in investigator
    assert "hermes-case-report.md" not in gate


def test_focused_questions_route_away_from_full_case_investigation() -> None:
    entry_meta, entry = parse_skill("cxba-case-investigation")
    investigator_meta, investigator = parse_skill("cxba-case-investigator")
    interactive_meta, interactive = parse_skill("cxba-interactive-data-analysis")

    for description in (
        entry_meta["description"],
        investigator_meta["description"],
    ):
        assert "明确要求遍历" in description
        assert "cxba-interactive-data-analysis" in description
    assert "默认使用" in interactive_meta["description"]
    assert "即使用户称其为“调查”或“核查”" in interactive_meta["description"]
    for body in (entry, investigator):
        assert "不得加载本Skill" in body or "立即停止本Skill" in body
        assert "全目录薄清单" in body
    assert "不得创建全目录薄清单" in interactive
    assert "cxba-evidence-review" in interactive


def test_full_case_skill_loads_only_after_trusted_mode_confirmation() -> None:
    investigator = parse_skill("cxba-case-investigator")[1]

    assert "可信Run上下文标明`FULL_CASE`" in investigator
    assert "通过`skill_view`加载" in investigator
    assert "Gateway负责加载前的案件准备和用户确认" in investigator
    assert "不得再次询问同一轮启动确认" in investigator
    assert "立即停止本Skill" in investigator
    assert "先完成一次有界的全案准备" not in investigator


def test_router_selects_minimal_primary_and_specialist_skills() -> None:
    metadata, router = parse_skill("cxba-analysis-router")

    assert "强制总入口" in metadata["description"]
    for skill_name in (
        "cxba-evidence-review",
        "cxba-case-investigation",
        "cxba-case-investigator",
        "cxba-interactive-data-analysis",
        "cxba-material-profiling",
        "cxba-analysis-pitfalls",
        "cxba-source-reconciliation",
        "cxba-expense-pattern-analysis",
        "cxba-temporal-graph-analysis",
        "cxba-safe-tabular-analysis",
    ):
        assert skill_name in router
    assert "默认专项" in router
    assert "跨文件汇总" in router
    assert "Router本身不读取材料" in metadata["description"]
    assert "不得创建`task-scope.json`" in router
    assert "不要在此时展开全部专项Skill" in router
    assert "每个问题进入执行时" in router
    assert "不得把所有专项Skill内容拼入完整案件Prompt" in router
    assert "不是允许方法的封闭清单" in router
    assert "计划外反常情况" in router
    assert "新的统计、图或交叉验证方法" in router


def test_all_material_analysis_skills_use_common_notebook_and_claim_contract() -> None:
    material_skills = (
        "cxba-case-investigation",
        "cxba-case-investigator",
        "cxba-evidence-review",
        "cxba-expense-pattern-analysis",
        "cxba-interactive-data-analysis",
        "cxba-material-profiling",
        "cxba-raw-material-investigation",
        "cxba-safe-tabular-analysis",
        "cxba-source-reconciliation",
        "cxba-temporal-graph-analysis",
    )
    for skill_name in material_skills:
        body = parse_skill(skill_name)[1]
        assert "cxba-analysis-notebook" in body, skill_name
        assert "cxba-claim-delivery" in body, skill_name

    for skill_name in (
        "cxba-case-investigator",
        "cxba-evidence-review",
        "cxba-expense-pattern-analysis",
        "cxba-interactive-data-analysis",
        "cxba-raw-material-investigation",
        "cxba-safe-tabular-analysis",
        "cxba-source-reconciliation",
        "cxba-temporal-graph-analysis",
    ):
        assert "cxba-analysis-pitfalls" in parse_skill(skill_name)[1], skill_name


def test_analysis_pitfalls_skill_collects_verified_cross_case_failures() -> None:
    metadata, body = parse_skill("cxba-analysis-pitfalls")

    assert metadata["name"] == "cxba-analysis-pitfalls"
    assert len(metadata["description"]) <= 60
    for phrase in (
        "真实运行中已经暴露并验证过",
        "普通调查Run不生成Skill候选文件",
        "只有SuperAdmin可以",
        "完整单文件UPDATE草稿",
        "经SuperAdmin确认后才进入公共Skill",
        "不把一次案件中的偶发现象泛化",
        "最低护栏，不限制Agent",
    ):
        assert phrase in body
    for entry in (
        "AP-001 候选池命名过早",
        "AP-002 Sheet名称冒充交易主体",
        "AP-003 负金额或摘要词直接决定方向",
        "AP-004 使用float计算正式金额",
        "AP-005 把相似交易直接去重",
        "AP-006 把冲正或退款当重复删除",
        "AP-007 缺少原始行号",
        "AP-008 第一个命中后提前返回",
        "AP-009 客户资料代替交易明细",
        "AP-010 身份未知候选被过滤",
        "AP-011 反证使原疑点静默消失",
        "AP-012 把有界口径外推成整体结论",
    ):
        assert entry in body
    assert "姓名、企业、账号、证件号、金额、原始流水或材料正文" in body


def test_common_notebook_requires_immediate_per_file_external_memory() -> None:
    notebook = parse_skill("cxba-analysis-notebook")[1]

    for phrase in (
        "/workspace/analysis-notebook.md",
        "每完成一个实际文件",
        "立即更新",
        "禁止模型连续处理多个文件后在结尾凭记忆批量回填",
        "batch-summary.json",
        "不需要在对话中逐文件重复无命中摘要",
        "主要内容",
        "可能用途",
        "可疑线索或反证",
        "精确定位",
        "下一步对照",
        "不扩大用户要求的材料范围",
    ):
        assert phrase in notebook
    assert "专项任务只记录本轮实际处理的文件" in notebook
    assert "完整案件调查以其薄清单为母表" in notebook


def test_investigation_knowledge_uses_read_only_vault_and_workspace_drafts() -> None:
    metadata, body = parse_skill("cxba-investigation-knowledge")

    assert metadata["name"] == "cxba-investigation-knowledge"
    for phrase in (
        "只读`/knowledge`",
        "不依赖RAG、向量库或固定关键词枚举",
        "/workspace/.cxba-coauthor/source.json",
        "`cutoffRunId`只是截止定位",
        "用户问题、Steer和AI最终答复",
        "不包含工具调用、内部推理或运行事件",
        "/workspace/.cxba-coauthor/draft.md",
        "/workspace/.cxba-coauthor/manifest.json",
        "只写泛化方法",
        "不得声称已经写入正式知识或公共Skill",
    ):
        assert phrase in body


def test_investigation_knowledge_produces_human_reviewable_business_markdown() -> None:
    _, body = parse_skill("cxba-investigation-knowledge")

    for phrase in (
        "SESSION",
        "CASE",
        "普通专题知识共创只能形成`DATA_SOURCE`、`TECHNIQUE`、`MISCONDUCT_PATTERN`或`INTEGRITY_RISK`",
        "只能形成`CASE_EXPERIENCE`",
        "`skillMaintenance`存在",
        "完整读取`skillMaintenance.currentSkill`",
        "不得搜索后改投其他Skill",
        "不得新建Skill或改变目标",
        "Skill维护只输出完整`SKILL.md`",
        "### 新型数据源",
        "### 新型违规违纪手法",
        "### 廉洁风险点",
        "适用场景",
        "所需材料",
        "办理步骤",
        "判断依据",
        "反证与排除",
        "适用边界",
        "用户确认前不得声称已经写入正式知识或公共Skill",
        "首次完成来源分析后",
        "不得让右侧草稿面板一直空着等待用户先回答",
        "顶层必须是单个JSON对象",
        "即使来源是测试或虚构材料",
    ):
        assert phrase in body

    assert "sensitiveFindings" not in body

    assert "targetRelativePath" in body


def test_focused_analysis_uses_generic_claim_delivery_preflight() -> None:
    interactive = parse_skill("cxba-interactive-data-analysis")[1]
    claim_skill = parse_skill("cxba-claim-delivery")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-claim-delivery"
        / "references"
        / "final-claim-delivery.md"
    ).read_text(encoding="utf-8")
    command = (
        "python3 /root/.hermes/skills/cxba/cxba-claim-delivery/scripts/"
        "preflight_claim_delivery.py --workspace /workspace --answer "
        "/workspace/final-answer.md"
    )
    assert "cxba-claim-delivery" in interactive
    assert command in claim_skill
    assert "完整相对路径" in contract
    assert "不判断筛选口径、关系或业务结论是否正确" in claim_skill
    assert "原始复算值与交付物回读值" in claim_skill
    assert "禁止使用`read_text()`" in contract
    assert "CLAIM_DELIVERY_PASS" in claim_skill
    assert "完整标准输出和退出码返回当前主Agent" in claim_skill
    assert "不得使用`|| true`" in claim_skill
    assert "退出码为0且输出首行为`CLAIM_DELIVERY_PASS`" in claim_skill
    assert "Gateway在正式回复后返回的`INVALID`只是最后兜底" in claim_skill
    assert "每个`sourceRef`必须填写非空`description`" in claim_skill
    assert "非空`scriptPath`、`resultPath`、`purpose`和`calculationBasis`" in claim_skill
    assert "REGENERATE_FROM_SKILL" in claim_skill
    assert "不得转入完整案件清单或完整案件终检" in interactive


def test_full_case_report_and_focused_analysis_constraints_live_in_skills() -> None:
    investigator = parse_skill("cxba-case-investigator")[1]
    interactive = parse_skill("cxba-interactive-data-analysis")[1]
    expense = parse_skill("cxba-expense-pattern-analysis")[1]
    pitfalls = parse_skill("cxba-analysis-pitfalls")[1]

    for phrase in (
        "下一阶段材料调取与人工核实清单",
        "缺什么、用于核实什么、建议向谁或从哪个业务环节调取、取得后如何验证",
        "按当前案件问题和实际材料形成的专题核查章节",
        "不得把某一案件的专题、人数、阈值、文件或结论固化为所有案件的默认结构",
    ):
        assert phrase in investigator

    for phrase in (
        "不因“梳理”“看看关系”“画关系图”自动提交任何业务写入提案",
        "不得只按姓名出现次数、单笔金额、交易总额或模型主观印象排名",
        "已由原件支持的正式关系",
        "仅观察到的交易联系",
        "cxba_read_dictionaries",
        "提案只是待人工审批的业务建议",
    ):
        assert phrase in interactive

    assert "必须分别以申请人和收款方运行排名、集中度和候选分析" in expense
    assert "先完成不设金额阈值的全量主体排名和分布分析" in expense
    assert "只回答该有界口径" in expense
    assert "本口径零命中" in pitfalls
    assert "尚未检查" in pitfalls


def test_evidence_review_uses_cross_session_mount_and_independent_local_calculation() -> None:
    review = parse_skill("cxba-evidence-review")[1]

    for phrase in (
        "/case-sessions/<session-id>",
        "不能因为当前 `/workspace` 没有这些文件就声称其不可访问",
        "必须写入当前复核工作区",
        "不得复制调查脚本或结果冒充独立复算产物",
        "逐字节一致/不一致",
        "不得在笔记、报告、Claim 或业务数据中新增、发布或依赖 MD5、SHA 等摘要值",
        "原始材料 → 计算结果字段 → 报告表头和单元格 → 页面最终回复",
        "只生成当前复核工作区的结果文件而没有保存并实际执行独立脚本",
        "原始路径和`materialId`必须逐字符对照`materials.json`",
    ):
        assert phrase in review


def test_cxba_gateway_deploys_complete_managed_skill_directories() -> None:
    script = GATEWAY_SCRIPT.read_text(encoding="utf-8")

    assert 'PROFILE_SOURCE=${CXBA_HERMES_PROFILE_SOURCE:-"${PROJECT_DIR}/profiles/${PROFILE}"}' in script
    assert 'cp -R "${source_skills}/." "${target_skills}/"' in script
    assert "sync_managed_profile_skills" in script
    assert script.index("sync_managed_profile_skills\n") < script.index('mkdir -p "$(dirname "${LOG_FILE}")"')


def test_claim_delivery_preflight_checks_answer_material_and_locator(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    for directory in ("input", "evidence-items", "scripts", "results"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "input" / "materials.json").write_text(
        json.dumps(
            [{"materialId": "material-a", "relativePath": "materials/a.xlsx"}]
        ),
        encoding="utf-8",
    )
    (workspace / "scripts" / "calc.py").write_text("# fixture\n", encoding="utf-8")
    (workspace / "results" / "calc.json").write_text(
        json.dumps(
            {
                "publishedMetrics": [
                    {
                        "metricCode": "METRIC-001",
                        "reportBlock": "两笔记录",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    claims = {
        "claims": [
            {
                "claimCode": "C001",
                "statement": "两笔记录",
                "claimType": "CALCULATION",
                "coverage": "FULL",
                "metricCodes": ["METRIC-001"],
                "limitations": [],
                "sourceRefs": [
                    {
                        "materialId": "material-a",
                        "relativePath": "materials/a.xlsx",
                        "role": "SUPPORT",
                        "locatorType": "EXCEL_RANGE",
                        "locator": {"sheet": "交易", "rows": [2, 5]},
                        "description": "两笔原始交易",
                    }
                ],
                "calculationRefs": [
                    {
                        "scriptPath": "scripts/calc.py",
                        "resultPath": "results/calc.json",
                        "purpose": "汇总两笔交易",
                        "calculationBasis": "按交易Sheet原始行汇总并排除冲正",
                    }
                ],
            }
        ]
    }
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps(claims, ensure_ascii=False), encoding="utf-8"
    )
    answer = workspace / "final-answer.md"
    answer.write_text("结论：存在两笔记录 [C001]\n", encoding="utf-8")

    passed = subprocess.run(
        [
            sys.executable,
            str(CLAIM_PREFLIGHT),
            "--workspace",
            str(workspace),
            "--answer",
            str(answer),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert passed.returncode == 0, passed.stdout
    assert passed.stdout.splitlines()[0] == "CLAIM_DELIVERY_PASS"

    incomplete = json.loads(json.dumps(claims, ensure_ascii=False))
    del incomplete["claims"][0]["sourceRefs"][0]["description"]
    del incomplete["claims"][0]["calculationRefs"][0]["purpose"]
    del incomplete["claims"][0]["calculationRefs"][0]["calculationBasis"]
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps(incomplete, ensure_ascii=False), encoding="utf-8"
    )
    incomplete_result = subprocess.run(
        [
            sys.executable,
            str(CLAIM_PREFLIGHT),
            "--workspace",
            str(workspace),
            "--answer",
            str(answer),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert incomplete_result.returncode == 1
    assert "sourceRefs[0].description不能为空" in incomplete_result.stdout
    assert "calculationRefs[0].purpose不能为空" in incomplete_result.stdout
    assert "calculationRefs[0].calculationBasis不能为空" in incomplete_result.stdout
    assert "修正方式：按上面的Claim序号、引用序号和字段名修改" in incomplete_result.stdout
    assert "REGENERATE_FROM_SKILL" in incomplete_result.stdout
    assert "重新通过skill_view完整读取cxba-claim-delivery" in incomplete_result.stdout

    answer.write_text("结论：存在两笔记录 C001\n", encoding="utf-8")
    claims["claims"][0]["sourceRefs"][0]["relativePath"] = "a.xlsx"
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps(claims, ensure_ascii=False), encoding="utf-8"
    )
    failed = subprocess.run(
        [
            sys.executable,
            str(CLAIM_PREFLIGHT),
            "--workspace",
            str(workspace),
            "--answer",
            str(answer),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert "最终回复未引用[C001]" in failed.stdout
    assert "materialId与relativePath未精确映射" in failed.stdout


def test_claim_delivery_rejects_stale_material_id_in_calculation_artifacts(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    for directory in ("input", "evidence-items", "scripts", "results"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    materials = {
        "materials": [
            {"materialId": "material-root", "relativePath": "财务费用情况表.xlsx"},
            {"materialId": "material-other", "relativePath": "其他/人民银行账户.xlsx"},
        ]
    }
    (workspace / "input" / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False), encoding="utf-8"
    )
    (workspace / "scripts" / "calc.py").write_text(
        'SOURCE = "/data/财务费用情况表.xlsx (materialId: material-other)"\n',
        encoding="utf-8",
    )
    (workspace / "results" / "calc.json").write_text(
        json.dumps(
            {
                "publishedMetrics": [
                    {
                        "metricCode": "METRIC-001",
                        "reportBlock": "完成计算",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    claims = {
        "claims": [
            {
                "claimCode": "C001",
                "statement": "完成计算",
                "claimType": "CALCULATION",
                "coverage": "FULL",
                "metricCodes": ["METRIC-001"],
                "limitations": [],
                "sourceRefs": [
                    {
                        "materialId": "material-root",
                        "relativePath": "财务费用情况表.xlsx",
                        "role": "SUPPORT",
                        "locatorType": "EXCEL_RANGE",
                        "locator": {"sheet": "明细", "rows": [2]},
                        "description": "参与计算的原始行",
                    }
                ],
                "calculationRefs": [
                    {
                        "scriptPath": "scripts/calc.py",
                        "resultPath": "results/calc.json",
                        "purpose": "验证材料身份",
                        "calculationBasis": "按明细原始行直接计算",
                    }
                ],
            }
        ]
    }
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps(claims, ensure_ascii=False), encoding="utf-8"
    )
    answer = workspace / "final-answer.md"
    answer.write_text("完成计算 [C001]\n", encoding="utf-8")

    failed = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert failed.returncode == 1
    assert "计算脚本" in failed.stdout
    assert "materialId与文件路径不一致：material-other" in failed.stdout


def test_claim_delivery_allows_source_free_hypothesis_or_gap(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    for directory in ("input", "evidence-items"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "input" / "materials.json").write_text(
        json.dumps({"materials": []}), encoding="utf-8"
    )
    claims = {
        "claims": [
            {
                "claimCode": "C001",
                "statement": "目前仅为待核推测",
                "claimType": "HYPOTHESIS",
                "coverage": "NONE",
                "limitations": ["缺少原始材料"],
                "sourceRefs": [],
                "calculationRefs": [],
            }
        ]
    }
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps(claims, ensure_ascii=False), encoding="utf-8"
    )
    answer = workspace / "final-answer.md"
    answer.write_text("目前仅为待核推测 [C001]\n", encoding="utf-8")

    passed = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert passed.returncode == 0, passed.stdout
    assert passed.stdout.splitlines()[0] == "CLAIM_DELIVERY_PASS"


def test_claim_delivery_checks_structured_material_id_in_result(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    for directory in ("input", "evidence-items", "scripts", "results"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "input" / "materials.json").write_text(
        json.dumps(
            {"materials": [
                {"materialId": "right-id", "relativePath": "根目录/目标.xlsx"},
                {"materialId": "wrong-id", "relativePath": "其他.xlsx"},
            ]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (workspace / "scripts" / "calc.py").write_text("# fixture\n", encoding="utf-8")
    (workspace / "results" / "calc.json").write_text(
        json.dumps(
            {
                "source": {"relativePath": "根目录/目标.xlsx", "materialId": "wrong-id"},
                "publishedMetrics": [
                    {
                        "metricCode": "METRIC-001",
                        "reportBlock": "计算完成",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    claim = {
        "claimCode": "C001", "statement": "计算完成", "claimType": "CALCULATION",
        "coverage": "FULL", "metricCodes": ["METRIC-001"], "limitations": [],
        "sourceRefs": [{
            "materialId": "right-id", "relativePath": "根目录/目标.xlsx",
            "role": "SUPPORT", "locatorType": "EXCEL_RANGE",
            "locator": {"sheet": "明细", "rows": [2]},
            "description": "参与计算的原始明细",
        }],
        "calculationRefs": [{
            "scriptPath": "scripts/calc.py",
            "resultPath": "results/calc.json",
            "purpose": "验证结果材料身份",
            "calculationBasis": "按明细原始行直接计算",
        }],
    }
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps({"claims": [claim]}, ensure_ascii=False), encoding="utf-8"
    )
    answer = workspace / "final-answer.md"
    answer.write_text("计算完成 [C001]\n", encoding="utf-8")

    failed = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False, capture_output=True, text=True,
    )

    assert failed.returncode == 1
    assert "计算结果" in failed.stdout
    assert "materialId与文件路径不一致：wrong-id" in failed.stdout


def _write_minimal_xlsx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/sharedStrings.xml", "<sst><si><t>人员甲</t></si></sst>")


def test_claim_delivery_validates_xlsx_and_publication_reconciliation(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    for directory in ("input", "evidence-items", "scripts", "results"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "input" / "materials.json").write_text(
        json.dumps(
            [{"materialId": "material-cn", "relativePath": "材料/人员甲.xlsx"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (workspace / "scripts" / "汇总.py").write_text("# fixture\n", encoding="utf-8")
    workbook = workspace / "results" / "人员甲_交易对手汇总.xlsx"
    _write_minimal_xlsx(workbook)
    report_block = "共40个交易对手、902笔，转入总金额168,598,175.88元"
    result_payload = {
        "publishedMetrics": [
            {
                "metricCode": "METRIC-TOTAL",
                "sourceValue": "168598175.88",
                "artifactValue": "168598175.88",
                "reportBlock": report_block,
            }
        ]
    }
    result_path = workspace / "results" / "汇总.json"
    result_path.write_text(
        json.dumps(result_payload, ensure_ascii=False), encoding="utf-8"
    )
    claim = {
        "claimCode": "C001",
        "statement": report_block,
        "claimType": "CALCULATION",
        "coverage": "FULL",
        "metricCodes": ["METRIC-TOTAL"],
        "limitations": [],
        "sourceRefs": [
            {
                "materialId": "material-cn",
                "relativePath": "材料/人员甲.xlsx",
                "role": "SUPPORT",
                "locatorType": "EXCEL_RANGE",
                "locator": {"sheet": "人员甲", "startRow": 2, "endRow": 903},
                "description": "参与交易对手汇总的原始行",
            }
        ],
        "calculationRefs": [
            {
                "scriptPath": "scripts/汇总.py",
                "resultPath": "results/汇总.json",
                "artifactPaths": ["results/人员甲_交易对手汇总.xlsx"],
                "purpose": "汇总交易对手和金额",
                "calculationBasis": "按账户、方向、期间和交易对手分组并排除冲正",
            }
        ],
    }
    claims_path = workspace / "evidence-items" / "final-claims.json"
    claims_path.write_text(
        json.dumps({"claims": [claim]}, ensure_ascii=False), encoding="utf-8"
    )
    answer = workspace / "final-answer.md"
    answer.write_text(f"{report_block} [C001]\n", encoding="utf-8")

    passed = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert passed.returncode == 0, passed.stdout

    wrong_report = "共40个交易对手、902笔，转入总金额175,182,720.69元"
    claim["statement"] = wrong_report
    claims_path.write_text(
        json.dumps({"claims": [claim]}, ensure_ascii=False), encoding="utf-8"
    )
    answer.write_text(f"{wrong_report} [C001]\n", encoding="utf-8")
    mismatched_report = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert mismatched_report.returncode == 1
    assert "reportBlock未原样进入claim.statement" in mismatched_report.stdout
    assert "reportBlock未原样进入最终回复" in mismatched_report.stdout

    claim["statement"] = report_block
    claims_path.write_text(
        json.dumps({"claims": [claim]}, ensure_ascii=False), encoding="utf-8"
    )
    answer.write_text(f"{report_block} [C001]\n", encoding="utf-8")
    result_payload["publishedMetrics"][0]["artifactValue"] = "175182720.69"
    result_path.write_text(
        json.dumps(result_payload, ensure_ascii=False), encoding="utf-8"
    )
    mismatched_value = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert mismatched_value.returncode == 1
    assert "原始复算值与交付物回读值不一致" in mismatched_value.stdout

    result_payload["publishedMetrics"][0]["artifactValue"] = "168598175.88"
    result_path.write_text(
        json.dumps(result_payload, ensure_ascii=False), encoding="utf-8"
    )
    workbook.write_bytes(b"not-an-xlsx")
    broken_workbook = subprocess.run(
        [sys.executable, str(CLAIM_PREFLIGHT), "--workspace", str(workspace), "--answer", str(answer)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert broken_workbook.returncode == 1
    assert "不是有效的.xlsx文件" in broken_workbook.stdout


def test_case_skills_allow_only_mechanical_delegation() -> None:
    contents = "\n".join(parse_skill(name)[1] for name in SKILL_NAMES)
    for task in (
        "格式识别",
        "OCR",
        "RTF或Office解析",
        "结构盘点",
        "候选定位",
        "确定性脚本计算",
    ):
        assert task in contents
    assert "不得委派案件事实、疑点、关系或结论的认定" in contents
    assert "子Agent不得认定案件事实、疑点、关系或结论" in contents


def test_main_investigator_must_verify_sources_and_own_evidence() -> None:
    contents = "\n".join(parse_skill(name)[1] for name in SKILL_NAMES)
    assert "主调查Agent必须亲自打开关键原件" in contents
    for location in ("Sheet或页码", "原始行", "金额", "收付方向"):
        assert location in contents
    assert "亲自形成证据台账和报告" in contents
    assert "子Agent摘要不能替代原件证据" in contents


def test_final_reviewer_is_independent_from_material_processing() -> None:
    contents = "\n".join(parse_skill(name)[1] for name in SKILL_NAMES)
    assert "未参与材料处理的独立复核Agent" in contents
    assert "材料处理子Agent不得充当最终复核Agent" in contents
    assert "cxba-evidence-review" in contents


def test_investigation_notebook_is_mandatory_external_memory() -> None:
    entry = parse_skill("cxba-case-investigation")[1]
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")

    for path in (
        "/workspace/investigation-state.md",
        "/workspace/material-review-ledger.md",
        "/workspace/evidence-ledger.md",
    ):
        assert path in entry
        assert path in investigator
    assert "不得只把发现保留在对话上下文或工具输出中" in entry
    assert "唯一调查进度入口" in contract
    assert "连续3次工具调用" in investigator
    assert "连续3次工具调用" in contract


def test_full_case_tracks_semantic_question_coverage_before_closure() -> None:
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")
    examples = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "transaction-discovery-examples.md"
    ).read_text(encoding="utf-8")
    temporal = parse_skill("cxba-temporal-graph-analysis")[1]
    event_contract = (
        SKILLS_ROOT
        / "cxba-temporal-graph-analysis"
        / "references"
        / "event-contract.md"
    ).read_text(encoding="utf-8")

    for phrase in (
        "文件覆盖不等于案件问题覆盖",
        "问题覆盖矩阵",
        "现任或历史同事",
        "一跳及两跳候选路径",
        "只是并行上限，不是全案问题总数",
        "遗漏高价值调查主线",
        "没有强标识不阻止流水候选发现",
        "只有准备归并人员主档时才执行强标识归并规则",
        "账户级、姓名级或观测级候选",
        "完整案件不预加载全部专项Skill",
        "只选择解决该问题所需的最小专项Skill组合",
        "禁止为了“可能有用”一次性展开全部Skill正文",
        "最低覆盖、证据和复核底线，不是封闭调查剧本",
        "自主追加问题",
        "计划外反常情况",
        "不得因为某个发现不在初始问题或示例方法中就忽略",
        "公共技战法覆盖检查",
        "/workspace/technique-catalog.json",
        "现有材料满足前提的技战法标为`APPLICABLE`",
        "关键材料缺失的标为`DATA_MISSING`",
        "技战法覆盖是公共最低检查集，不是调查上限",
    ):
        assert phrase in investigator
    assert "references/transaction-discovery-examples.md" in investigator
    for dimension in (
        "ACCOUNT_COVERAGE",
        "DIRECT_FLOW",
        "INDIRECT_FLOW",
        "CROSS_SOURCE",
        "TEMPORAL",
        "COUNTER_EVIDENCE",
        "MATERIAL_GAP",
    ):
        assert dimension in contract
    assert "首次结案前全部问题必须达到" in contract
    assert "专项Skill：Skill名称" in contract
    assert "选择理由" in contract
    for phrase in (
        "技战法目录检查：COMPLETED | NONE",
        "技战法路径",
        "状态：APPLICABLE | COMPLETED | DATA_MISSING | NOT_APPLICABLE",
        "APPLICABLE`不是结案状态",
    ):
        assert phrase in contract
    for phrase in (
        "当前/历史任职",
        "不先加“大额”阈值",
        "不能在第一个匹配后`return`",
        "客户资料和开户资料用于账户归属，不能代替交易明细",
        "直接、一跳、两跳候选数",
        "assert covered_target_nodes == target_nodes",
        "身份未知不等于无关",
        "候选发现后再识别主体与关系",
        "不得要求用户先提供姓名或关系后才开始分析",
        "强标识不作为前提",
        "不等同于人员主档ID",
        "负金额被当入账",
        "raw_observation_count >= deduplicated_event_count",
        "当前案件材料中尚未提供",
        "外卖及收货地址、打车、代驾",
        "不进入`10-新型数据源`",
    ):
        assert phrase in examples
    for phrase in (
        "候选发现不以交易对手身份、关系或人员主档强标识已知为前提",
        "Node identity is an analysis key, not a person-master merge decision",
        "SOURCE_NAME_LEVEL",
        "IDENTITY_UNRESOLVED",
        "RELATION_UNRESOLVED",
        "身份或关系未知的候选不得被过滤",
        "暂拟排除的疑点",
    ):
        assert phrase in temporal
    for phrase in (
        "SOURCE_NAME_LEVEL",
        "not person-master ids",
        "negative source value does not by itself mean incoming",
        "DIRECTION_UNKNOWN",
        "raw_observation_count",
        "reversal_of",
        "do not claim a deduplicated total",
    ):
        assert phrase in event_contract


def test_evidence_ledger_has_upgrade_rules_and_report_gate() -> None:
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")

    for status in ("CANDIDATE", "VERIFIED", "REFUTED", "HYPOTHESIS", "GAP"):
        assert status in contract
    for required in (
        "主体与角色",
        "原始来源",
        "支持证据",
        "反证与正常解释",
        "主调查回查",
    ):
        assert required in contract
    assert "子Agent摘要直接复制成`VERIFIED`" in investigator
    assert "不得声称完成或`FULL`" in investigator
    assert "报告事实、计算、关系和疑点紧邻引用证据编号" in contract


def test_every_inventory_file_requires_a_separate_review_record() -> None:
    entry = parse_skill("cxba-case-investigation")[1]
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")

    assert "每个物理文件都必须单独记录并对齐台账" in entry
    assert "为薄清单中的每个路径建立独立记录" in investigator
    for field in ("主要内容", "实际覆盖", "可能有用的点", "可疑线索"):
        assert field in contract
    for status in ("UNREAD", "PARTIAL", "REVIEWED", "FAILED"):
        assert status in contract
    assert "路径集合和文件数必须与物理薄清单一致" in contract


def test_suspicious_leads_require_exact_source_and_content_summary() -> None:
    entry = parse_skill("cxba-case-investigation")[1]
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")

    for phrase in (
        "原文件",
        "Sheet或页码",
        "原始行或唯一定位",
        "内容摘要",
    ):
        assert phrase in entry or phrase in investigator or phrase in contract
    assert "只有文件级“可能有用”而没有具体记录定位时" in contract


def test_round_three_rules_are_explicit() -> None:
    entry = parse_skill("cxba-case-investigation")[1]
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")
    contents = "\n".join((entry, investigator, contract))

    assert "物理文件覆盖的唯一母表" in contents
    assert "materials.json" in contents and "不能替代物理薄清单" in contents
    assert ".DS_Store" in contents and "NON_MATERIAL" in contents
    assert "ZIP成员" in contents
    assert "下一内容工具" in contents
    assert "batch-summary.json" in contents
    assert "凭记忆伪回填" in contents
    for field in (
        "文件",
        "Sheet/页",
        "Excel原始行/唯一流水",
        "字段角色",
        "收付方向",
        "口径",
    ):
        assert field in contract
    assert "同姓、同地址、单位名称或发生交易均不能推出亲属关系及任职事实" in contract
    for transaction_id in ("柜员流水号", "CPC流水号", "强流水标识"):
        assert transaction_id in contents
    assert "不得把借贷两侧金额相加" in contents
    assert "人员关系和任职单位只接受原件明示关系、任职或单位字段" in contract
    assert "EA报销支付" in investigator
    assert "PENDING | RECEIVED | VERIFIED | REJECTED" in contract
    assert "候选上限" in contract and "巨型JSON" in contract
    assert "未解释冲突不得发布" in investigator
    assert "过程与最终回复使用中文" in entry
    assert "授权办案人员" in contents
    assert "按原件保留" in contents
    assert "生产日志" in contents and "非办案展示" in contents
    assert not re.search(r"本轮实际\d+个", contents)
    assert "当前Run实时生成的薄清单" in investigator


def test_notebook_validator_accepts_complete_package(tmp_path: Path) -> None:
    inventory = tmp_path / "thin-inventory.json"
    materials = tmp_path / "material-review-ledger.md"
    evidence = tmp_path / "evidence-ledger.md"
    state = tmp_path / "investigation-state.md"
    inventory.write_text(
        '{"fileCount":2,"files":[{"path":"data.zip"},{"path":".DS_Store"}]}',
        encoding="utf-8",
    )
    materials.write_text(
        """## M001
路径：data.zip
状态：REVIEWED
物理清点：INVENTORIED
结构盘点：REVIEWED
内容审阅：REVIEWED
格式与结构：ZIP
实际覆盖：全部成员
主要内容：测试材料
可能有用的点：无
可疑线索：E001
限制与失败：无
下一步：无
ZIP成员数：2
ZIP成员检查：REVIEWED
成员清单路径：/workspace/results/data-zip-members.txt

## M002
路径：.DS_Store
状态：NON_MATERIAL
物理清点：INVENTORIED
结构盘点：NON_MATERIAL
内容审阅：NON_MATERIAL
格式与结构：macOS元数据
实际覆盖：格式确认
主要内容：系统元数据
可能有用的点：无
可疑线索：无
限制与失败：无
下一步：无
""",
        encoding="utf-8",
    )
    evidence.write_text(
        """## E001
状态：VERIFIED
问题：测试问题
主体与角色：甲付款，乙收款
事实或疑点：测试事实
文件：data.zip
materialId：material-zip
Sheet/页：不适用（ZIP成员）
Excel原始行/唯一流水：流水T001
字段角色：付款账号、收款账号
收付方向：甲到乙
口径：人民币，原始金额，无去重
关键字段：日期、金额
方法：直接读取
支持证据：M001
反证与正常解释：无
限制与缺口：无
主调查回查：已回查流水T001
下一步：无
报告Claim：C001
""",
        encoding="utf-8",
    )
    state.write_text(
        """交易分析：NOT_APPLICABLE
交易分析不适用理由：测试夹具只验证ZIP结构，不含交易事件
技战法目录检查：NONE
## 问题覆盖矩阵
### Q001
状态：VERIFIED
来源：测试材料
分析维度：MAIN_QUESTION
专项Skill：cxba-material-profiling
选择理由：测试ZIP结构需要统一安全解析
问题：ZIP材料是否完成结构盘点
对象与角色：测试材料
已检查范围：data.zip全部成员
支持假设：成员清单完整
正常解释：系统压缩包
反向假设：成员清单不完整
证据或缺口：E001
未覆盖与原因：无
下一步：无
报告Claim：C001
## 委派任务
### D001
状态：VERIFIED
任务：ZIP结构盘点
输入范围：data.zip
输出路径：/workspace/results/delegated.txt
定位粒度：文件及成员
候选上限：20
主调查抽核：已抽核
拒绝原因：无
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--inventory",
            str(inventory),
            "--material-ledger",
            str(materials),
            "--evidence-ledger",
            str(evidence),
            "--state",
            str(state),
            "--final",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    assert '"status": "passed"' in result.stdout


def test_notebook_validator_rejects_incomplete_final_package(tmp_path: Path) -> None:
    inventory = tmp_path / "thin-inventory.json"
    materials = tmp_path / "material-review-ledger.md"
    evidence = tmp_path / "evidence-ledger.md"
    state = tmp_path / "investigation-state.md"
    inventory.write_text(
        '{"fileCount":2,"files":[{"path":"a.xlsx"},{"path":"b.zip"}]}',
        encoding="utf-8",
    )
    materials.write_text(
        """## M001
路径：a.xlsx
状态：UNREAD
物理清点：INVENTORIED
结构盘点：REVIEWED
内容审阅：UNREAD
格式与结构：Excel
实际覆盖：无
主要内容：未知
可能有用的点：未知
可疑线索：无
限制与失败：未读
下一步：读取
""",
        encoding="utf-8",
    )
    evidence.write_text(
        """## E001
状态：VERIFIED
问题：测试
主体与角色：未知
事实或疑点：测试
文件：a.xlsx
materialId：material-a
报告Claim：C001
""",
        encoding="utf-8",
    )
    state.write_text(
        """### D001
状态：RECEIVED
任务：扫描
输入范围：a.xlsx
输出路径：/workspace/results/a.txt
定位粒度：文件
候选上限：500
主调查抽核：未抽核
拒绝原因：无
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--inventory",
            str(inventory),
            "--material-ledger",
            str(materials),
            "--evidence-ledger",
            str(evidence),
            "--state",
            str(state),
            "--final",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    for message in (
        "报告前仍为 UNREAD",
        "材料台账数量 1 与薄清单 2 不一致",
        "材料台账缺少薄清单路径：b.zip",
        "缺少字段：Sheet/页",
        "候选上限必须为 1 至 200",
        "报告前未达到终态：RECEIVED",
    ):
        assert message in result.stdout


def _write_final_gate_workspace(workspace: Path) -> None:
    (workspace / "input").mkdir(parents=True)
    (workspace / "evidence-items").mkdir()
    (workspace / "scripts").mkdir()
    (workspace / "results").mkdir()
    (workspace / "thin-inventory.json").write_text(
        '{"fileCount":1,"files":[{"path":"流水/a.xlsx"}]}',
        encoding="utf-8",
    )
    (workspace / "input" / "materials.json").write_text(
        '[{"materialId":"material-a","relativePath":"流水/a.xlsx"}]',
        encoding="utf-8",
    )
    (workspace / "material-review-ledger.md").write_text(
        """## M001
路径：流水/a.xlsx
状态：REVIEWED
物理清点：INVENTORIED
结构盘点：REVIEWED
内容审阅：REVIEWED
格式与结构：Excel，Sheet交易
实际覆盖：交易Sheet原始行2至3
主要内容：两笔测试交易
可能有用的点：金额汇总
可疑线索：E001
限制与失败：无
下一步：无
""",
        encoding="utf-8",
    )
    (workspace / "evidence-ledger.md").write_text(
        """## E001
状态：VERIFIED
问题：汇总两笔交易
主体与角色：甲付款，乙收款
事实或疑点：两笔交易合计20万元
文件：流水/a.xlsx
materialId：material-a
Sheet/页：交易
Excel原始行/唯一流水：原始行2、3；柜员流水号T1、T2
字段角色：付款账号、收款账号、明确交易流水字段
收付方向：甲到乙
口径：人民币，排除冲正，按强流水去重
关键字段：日期、金额、EA报销支付交易码
方法：scripts/calc.py；results/calc.json
支持证据：M001
反证与正常解释：未见冲正；仅陈述计算
限制与缺口：无
主调查回查：已回查交易Sheet原始行2、3
下一步：无
报告Claim：C001
""",
        encoding="utf-8",
    )
    question_dimensions = (
        "ACCOUNT_COVERAGE",
        "DIRECT_FLOW",
        "INDIRECT_FLOW",
        "CROSS_SOURCE",
        "TEMPORAL",
        "COUNTER_EVIDENCE",
        "MATERIAL_GAP",
    )
    question_records = "".join(
        f"""
### Q{index:03d}
状态：VERIFIED
来源：测试交易材料
分析维度：{dimension}
专项Skill：NONE
选择理由：测试夹具使用已有确定性脚本即可完成
问题：验证{dimension}覆盖
对象与角色：甲付款，乙收款
已检查范围：流水/a.xlsx交易Sheet原始行2至3
支持假设：两笔交易满足测试口径
正常解释：仅为测试交易
反向假设：记录不满足测试口径
证据或缺口：E001
未覆盖与原因：无
下一步：无
报告Claim：C001
"""
        for index, dimension in enumerate(question_dimensions, start=1)
    )
    (workspace / "investigation-state.md").write_text(
        """结案覆盖：FULL
交易分析：REQUIRED
交易分析不适用理由：无
技战法目录检查：NONE
最近检查点：最终报告前
下一动作：运行终检
## 问题覆盖矩阵
""" + question_records + """
## 委派任务
无
""",
        encoding="utf-8",
    )
    (workspace / "scripts" / "calc.py").write_text("# deterministic fixture\n", encoding="utf-8")
    calculation = {
        "publishedMetrics": [
            {
                "metricCode": "METRIC-001",
                "currency": "CNY",
                "totalCount": 2,
                "totalAmount": "200000.00",
                "groups": [
                    {
                        "group": "2018-01",
                        "eventIds": ["T1", "T2"],
                        "count": 2,
                        "amount": "200000.00",
                    }
                ],
                "events": [
                    {
                        "eventId": "T1",
                        "amount": "100000.00",
                        "observations": [
                            {
                                "relativePath": "流水/a.xlsx",
                                "sheet": "交易",
                                "row": 2,
                                "strongEventId": "柜员流水号T1",
                            }
                        ],
                    },
                    {
                        "eventId": "T2",
                        "amount": "100000.00",
                        "observations": [
                            {
                                "relativePath": "流水/a.xlsx",
                                "sheet": "交易",
                                "row": 3,
                                "strongEventId": "CPC流水号T2",
                            }
                        ],
                    },
                ],
                "inputRows": [
                    {
                        "relativePath": "流水/a.xlsx",
                        "sheet": "交易",
                        "row": 2,
                        "disposition": "INCLUDED",
                        "eventId": "T1",
                        "reason": "满足口径",
                    },
                    {
                        "relativePath": "流水/a.xlsx",
                        "sheet": "交易",
                        "row": 3,
                        "disposition": "INCLUDED",
                        "eventId": "T2",
                        "reason": "满足口径",
                    },
                ],
                "inputCoverage": {"mode": "ALL_SOURCE_ROWS", "sourceRowCount": 2},
                "missingValuePolicy": "EXCLUDE_AND_DISCLOSE",
                "missingRowCount": 0,
                "transactionChecks": {
                    "domain": "TRANSACTION",
                    "strongIdFields": ["柜员流水号", "CPC流水号"],
                    "mirrorCollisionChecked": True,
                    "eaReimbursementPaymentChecked": True,
                },
                "reportBlock": "2笔，合计20万元",
            }
        ]
    }
    import json

    (workspace / "results" / "calc.json").write_text(
        json.dumps(calculation, ensure_ascii=False), encoding="utf-8"
    )
    claims = {
        "claims": [
            {
                "claimCode": "C001",
                "statement": "2笔，合计20万元",
                "claimType": "CALCULATION",
                "coverage": "FULL",
                "userBasis": "人民币，排除冲正，按强流水去重",
                "supportSummary": "交易Sheet原始行2、3",
                "counterSummary": "未见冲正",
                "limitations": [],
                "sourceRefs": [
                    {
                        "materialId": "material-a",
                        "relativePath": "流水/a.xlsx",
                        "role": "SUPPORT",
                        "locatorType": "EXCEL_RANGE",
                        "locator": {"sheet": "交易", "rows": [2, 3]},
                        "description": "两笔原始交易",
                    }
                ],
                "calculationRefs": [
                    {
                        "scriptPath": "scripts/calc.py",
                        "resultPath": "results/calc.json",
                        "purpose": "汇总两笔交易",
                        "calculationBasis": "按强流水去重并排除冲正",
                    }
                ],
                "metricCodes": ["METRIC-001"],
            }
        ]
    }
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps(claims, ensure_ascii=False), encoding="utf-8"
    )
    (workspace / "case-investigation-report.md").write_text(
        "# 调查报告\n\n经计算，2笔，合计20万元，已按强流水完成镜像去重 [C001]\n",
        encoding="utf-8",
    )
    (workspace / "review-result.md").write_text(
        "VERDICT: PASS\n\n已独立复核原始行、计算结果和报告引用。\n", encoding="utf-8"
    )


def _run_final_gate(workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FINAL_GATE), "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_final_gate_single_entrypoint_passes_reconciled_package(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)

    result = _run_final_gate(workspace)

    assert result.returncode == 0, result.stdout
    assert result.stdout.splitlines()[0] == "FINAL_GATE_PASS"


def test_final_gate_rejects_same_name_material_id_borrow_and_bad_totals(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)
    inventory = json.loads((workspace / "thin-inventory.json").read_text(encoding="utf-8"))
    inventory["files"].append({"path": "a.xlsx"})
    inventory["fileCount"] = 2
    (workspace / "thin-inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False), encoding="utf-8"
    )
    ledger = (workspace / "material-review-ledger.md").read_text(encoding="utf-8")
    ledger += """
## M002
路径：a.xlsx
状态：REVIEWED
物理清点：INVENTORIED
结构盘点：REVIEWED
内容审阅：REVIEWED
格式与结构：Excel
实际覆盖：全部
主要内容：根目录同名文件
可能有用的点：无
可疑线索：E002
限制与失败：未编目
下一步：补充目录
"""
    (workspace / "material-review-ledger.md").write_text(ledger, encoding="utf-8")
    evidence = (workspace / "evidence-ledger.md").read_text(encoding="utf-8")
    evidence += """
## E002
状态：VERIFIED
问题：同名文件
主体与角色：未知
事实或疑点：借用子目录标识
文件：a.xlsx
materialId：material-a
Sheet/页：交易
Excel原始行/唯一流水：原始行2
字段角色：摘要
收付方向：不适用
口径：直接读取
关键字段：摘要
方法：直接读取
支持证据：M002
反证与正常解释：无
限制与缺口：未编目
主调查回查：已回查
下一步：补充目录
报告Claim：无
"""
    (workspace / "evidence-ledger.md").write_text(evidence, encoding="utf-8")
    result_payload = json.loads((workspace / "results" / "calc.json").read_text(encoding="utf-8"))
    result_payload["publishedMetrics"][0]["groups"][0]["amount"] = "190000.00"
    (workspace / "results" / "calc.json").write_text(
        json.dumps(result_payload, ensure_ascii=False), encoding="utf-8"
    )

    result = _run_final_gate(workspace)

    assert result.returncode == 1
    assert result.stdout.splitlines()[0] == "FINAL_GATE_FAIL"
    assert "未编目证据E002不得借用materialId" in result.stdout
    assert "分组金额和不等于总额" in result.stdout


def test_final_gate_rejects_zero_byte_report_and_empty_claims(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)
    (workspace / "case-investigation-report.md").write_text("", encoding="utf-8")
    (workspace / "evidence-items" / "final-claims.json").write_text(
        json.dumps({"claims": []}), encoding="utf-8"
    )
    (workspace / "review-result.md").write_text("VERDICT: PASS\n", encoding="utf-8")

    result = _run_final_gate(workspace)

    assert result.returncode == 1
    assert result.stdout.splitlines()[0] == "FINAL_GATE_FAIL"
    assert "最终调查报告缺少实质正文" in result.stdout
    assert "final-claims.json至少需要一项claim" in result.stdout
    assert "独立复核结果缺少实质正文" in result.stdout


def test_final_gate_rejects_missing_question_coverage_and_claim_fields(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)
    (workspace / "investigation-state.md").write_text(
        """结案覆盖：FULL
交易分析：REQUIRED
交易分析不适用理由：无
技战法目录检查：NONE
## 委派任务
无
""",
        encoding="utf-8",
    )
    claims_path = workspace / "evidence-items" / "final-claims.json"
    payload = json.loads(claims_path.read_text(encoding="utf-8"))
    source = payload["claims"][0]["sourceRefs"][0]
    calculation = payload["claims"][0]["calculationRefs"][0]
    source.pop("description")
    calculation.pop("purpose")
    calculation.pop("calculationBasis")
    claims_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = _run_final_gate(workspace)

    assert result.returncode == 1
    assert "首次结案缺少问题覆盖矩阵" in result.stdout
    assert "交易分析问题覆盖不完整" in result.stdout
    assert "sourceRefs[0]缺少description" in result.stdout
    assert "calculationRefs[0]缺少purpose" in result.stdout
    assert "calculationRefs[0]缺少calculationBasis" in result.stdout


def test_final_gate_keeps_supported_suspicion_and_counter_evidence_visible(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)
    evidence_path = workspace / "evidence-ledger.md"
    evidence = evidence_path.read_text(encoding="utf-8")
    evidence = evidence.replace("状态：VERIFIED", "状态：REFUTED", 1)
    evidence = evidence.replace("报告Claim：C001", "报告Claim：无", 1)
    evidence_path.write_text(evidence, encoding="utf-8")

    result = _run_final_gate(workspace)

    assert result.returncode == 1
    assert "疑点证据E001未进入最终报告Claim" in result.stdout
    assert "最终报告缺少暂拟排除的疑点章节" in result.stdout


def test_final_gate_rejects_unfinished_applicable_technique(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)
    state_path = workspace / "investigation-state.md"
    state = state_path.read_text(encoding="utf-8")
    state = state.replace("技战法目录检查：NONE", "技战法目录检查：COMPLETED")
    state += """
## 技战法覆盖
### T001
技战法路径：20-技战法/测试方法.md
标题：测试方法
状态：APPLICABLE
适用场景：测试交易分析
所需材料：交易流水
现有材料：流水/a.xlsx
缺失材料：无
适用判断：现有材料满足前提
转入问题：Q001
"""
    state_path.write_text(state, encoding="utf-8")

    result = _run_final_gate(workspace)

    assert result.returncode == 1
    assert "技战法 T001 报告前仍未执行：APPLICABLE" in result.stdout


def test_final_gate_rejects_full_sample_and_missing_transaction_checks(tmp_path: Path) -> None:
    import json

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_final_gate_workspace(workspace)
    result_path = workspace / "results" / "calc.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    metric = payload["publishedMetrics"][0]
    metric["inputRows"].append(
        {
            "relativePath": "流水/a.xlsx",
            "sheet": "交易",
            "row": 4,
            "disposition": "EXCLUDED",
            "reason": "不满足口径",
        }
    )
    metric["inputCoverage"]["sourceRowCount"] = 3
    metric["transactionChecks"]["mirrorCollisionChecked"] = False
    metric["transactionChecks"]["eaReimbursementPaymentChecked"] = False
    result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = _run_final_gate(workspace)

    assert result.returncode == 1
    assert "FULL存在未列入claim原始定位的输入行" in result.stdout
    assert "未完成跨Sheet镜像碰撞" in result.stdout
    assert "未核查EA报销支付" in result.stdout


def test_round_four_publication_rules_are_explicit() -> None:
    entry = parse_skill("cxba-case-investigation")[1]
    investigator = parse_skill("cxba-case-investigator")[1]
    contract = (
        SKILLS_ROOT
        / "cxba-case-investigator"
        / "references"
        / "investigation-notebook.md"
    ).read_text(encoding="utf-8")
    claims_contract = (
        SKILLS_ROOT
        / "cxba-claim-delivery"
        / "references"
        / "final-claim-delivery.md"
    ).read_text(encoding="utf-8")
    contents = "\n".join((entry, investigator, contract, claims_contract))

    for phrase in (
        "物理清点",
        "结构盘点",
        "内容审阅",
        "根目录同名文件不得借用子目录",
        "分组笔数之和必须等于总笔数",
        "禁止人工抄写月度数",
        "不能只列5个样本",
        "同事或共同任职只证明工作关系",
        "交易链均不能推出亲属关系、任职事实或资金来源",
        "字段缺失、解析失败或未覆盖不得当作0",
        "没有已证事实时也应交付`GAP`或`HYPOTHESIS`",
        "延迟子Agent结果再次提交结案",
    ):
        assert phrase in contents
    for word in ("规避监管", "掩饰", "通道", "远超正常", "异常"):
        assert word in investigator
    assert "200000" in contents and "20万元" in contents
    assert "身份证号" in contents and "银行卡号" in contents
    assert "不得擅自掩码或改写" in contents
    assert "不使用hash、版本或seal" in contents


def test_final_gate_contract_is_prominent_and_uses_stable_sandbox_path() -> None:
    entry = parse_skill("cxba-case-investigation")[1]
    investigator = parse_skill("cxba-case-investigator")[1]
    command = (
        "python3 /root/.hermes/skills/cxba/cxba-case-investigator/scripts/"
        "final_investigation_gate.py --workspace /workspace"
    )
    assert command in entry
    assert command in investigator
    assert "FINAL_GATE_PASS" in entry
    assert "FINAL_GATE_FAIL" in entry
    # Production profile Skills are mounted in the Run sandbox under this
    # stable root; the same convention is used by the shipped profiling Skill.
    profiling = (SKILLS_ROOT / "cxba-material-profiling" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "/root/.hermes/skills/cxba/" in profiling
