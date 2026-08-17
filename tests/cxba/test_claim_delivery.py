import json
from pathlib import Path
from types import SimpleNamespace

from tools.run_sandbox import RunMount, TrustedRunContext
from tui_gateway.cxba_claims import prepare_claim_delivery, read_claim_delivery


def _run(tmp_path: Path):
    workspace = tmp_path / "workspace"
    materials = tmp_path / "materials"
    workspace.mkdir()
    materials.mkdir()
    (materials / "流水.xlsx").write_bytes(b"xlsx-test")
    context = TrustedRunContext(
        case_id="case-1",
        business_session_id="session-1",
        business_branch_id="branch-1",
        run_id="run-1",
        actor_user_id="user-1",
        mounts=(
            RunMount(str(workspace), "/workspace", False),
            RunMount(str(materials), "/data", True),
        ),
    )
    case_context = {
        "case_basic": {"case_id": "case-1"},
        "global_master_links": [],
        "investigation_mode": "STANDARD",
        "material_catalog": [
            {"materialId": "material-1", "relativePath": "流水.xlsx"}
        ],
    }
    return SimpleNamespace(context=context, case_context=case_context), workspace


def _write_claims(workspace: Path, claims):
    target = workspace / "evidence-items" / "final-claims.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"claims": claims}, ensure_ascii=False), encoding="utf-8")


def _fact_claim():
    return {
        "claimCode": "C001",
        "statement": "账户发生三笔交易",
        "claimType": "FACT",
        "coverage": "FULL",
        "userBasis": "按交易明细统计",
        "supportSummary": "三行原始记录",
        "counterSummary": "未见冲正",
        "limitations": [],
        "sourceRefs": [
            {
                "materialId": "material-1",
                "role": "SUPPORT",
                "locatorType": "EXCEL_RANGE",
                "locator": {"sheet": "流水", "startRow": 2, "endRow": 4},
                "description": "三笔交易原始行",
            }
        ],
        "calculationRefs": [],
    }


def test_reads_verified_claim_from_current_run_workspace(tmp_path):
    run, workspace = _run(tmp_path)
    _write_claims(workspace, [_fact_claim()])

    delivered = read_claim_delivery(run, "结论如下 [C001]")

    assert delivered["evidence_status"] == "VERIFIED"
    assert delivered["claims"][0]["sourceRefs"][0]["materialId"] == "material-1"
    assert "relativePath" not in delivered["claims"][0]["sourceRefs"][0]


def test_reads_exact_non_contiguous_excel_rows(tmp_path):
    run, workspace = _run(tmp_path)
    claim = _fact_claim()
    claim["sourceRefs"][0]["locator"] = {
        "sheet": "流水",
        "rows": [13, 16, 21, 24, 28, 43, 65, 74, 120],
    }
    _write_claims(workspace, [claim])

    delivered = read_claim_delivery(run, "离散原始记录 [C001]")

    assert delivered["evidence_status"] == "VERIFIED"
    assert delivered["claims"][0]["sourceRefs"][0]["locator"]["rows"] == [
        13, 16, 21, 24, 28, 43, 65, 74, 120
    ]


def test_exact_excel_rows_cannot_be_combined_with_range(tmp_path):
    run, workspace = _run(tmp_path)
    claim = _fact_claim()
    claim["sourceRefs"][0]["locator"] = {
        "sheet": "流水",
        "rows": [13, 16],
        "startRow": 13,
        "endRow": 16,
    }
    _write_claims(workspace, [claim])

    delivered = read_claim_delivery(run, "混合定位 [C001]")

    assert delivered["evidence_status"] == "INVALID"
    assert delivered["evidence_errors"] == ["excel_row_invalid"]


def test_invalid_material_keeps_answer_but_delivers_no_verified_claim(tmp_path):
    run, workspace = _run(tmp_path)
    (tmp_path / "materials" / "伪造.xlsx").write_bytes(b"forged")
    (workspace / "input").mkdir()
    (workspace / "input" / "materials.json").write_text(
        json.dumps(
            [
                {
                    "materialId": "material-other-case",
                    "relativePath": "伪造.xlsx",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    claim = _fact_claim()
    claim["sourceRefs"][0]["materialId"] = "material-other-case"
    _write_claims(workspace, [claim])

    delivered = read_claim_delivery(run, "真实回答仍然交付 [C001]")

    assert delivered == {
        "evidence_status": "INVALID",
        "claims": [],
        "evidence_errors": ["source_material_untrusted"],
    }


def test_calculation_requires_existing_script_and_result(tmp_path):
    run, workspace = _run(tmp_path)
    claim = _fact_claim()
    claim["claimType"] = "CALCULATION"
    claim["calculationRefs"] = [
        {
            "scriptPath": "scripts/sum.py",
            "resultPath": "results/sum.json",
            "purpose": "汇总金额",
            "calculationBasis": "收入方向，排除冲正",
        }
    ]
    _write_claims(workspace, [claim])

    delivered = read_claim_delivery(run, "金额结论 [C001]")

    assert delivered["evidence_status"] == "INVALID"
    assert delivered["evidence_errors"] == ["calculation_script_missing"]


def test_prepare_removes_previous_run_result(tmp_path):
    run, workspace = _run(tmp_path)
    _write_claims(workspace, [_fact_claim()])

    prepare_claim_delivery(run)

    assert not (workspace / "evidence-items" / "final-claims.json").exists()


def test_missing_result_is_explicitly_not_provided(tmp_path):
    run, _workspace = _run(tmp_path)

    delivered = read_claim_delivery(run, "普通寒暄")

    assert delivered == {
        "evidence_status": "NOT_PROVIDED",
        "claims": [],
        "evidence_errors": [],
    }


def test_answer_cannot_reference_claim_missing_from_result(tmp_path):
    run, workspace = _run(tmp_path)
    _write_claims(workspace, [_fact_claim()])

    delivered = read_claim_delivery(run, "已有 [C001]，但还写了 [C002]")

    assert delivered["evidence_status"] == "INVALID"
    assert delivered["evidence_errors"] == ["answer_claim_reference_missing"]


def test_each_delivered_claim_must_be_bracketed_in_answer(tmp_path):
    run, workspace = _run(tmp_path)
    _write_claims(workspace, [_fact_claim()])

    delivered = read_claim_delivery(run, "正文只写了 C001，没有可点击标签")

    assert delivered["evidence_status"] == "INVALID"
    assert delivered["evidence_errors"] == ["claim_not_referenced_by_answer"]
