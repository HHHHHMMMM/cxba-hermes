#!/usr/bin/env python3
"""Single deterministic final gate for a CXBA investigation workspace.

The gate checks structure, exact references, arithmetic reconciliation and
publication consistency. It deliberately does not decide whether case facts,
relationships or investigative judgments are true.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

from validate_investigation_notebook import (
    inventory_paths,
    parse_records,
    validate_delegations,
    validate_evidence,
    validate_materials,
    validate_questions,
    validate_techniques,
)


CLAIM_TYPES = {"FACT", "CALCULATION", "RELATION", "FINDING", "HYPOTHESIS", "GAP"}
COVERAGES = {"FULL", "PARTIAL", "NONE"}
FINAL_TYPES_WITHOUT_SOURCE = {"HYPOTHESIS", "GAP"}
QUALITATIVE_WORDS = ("规避监管", "掩饰", "通道", "远超正常", "异常")
CLAIM_CODE_RE = re.compile(r"\[([A-Z][A-Z0-9_-]{0,31})\]")


def load_json(path: Path, label: str, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"{label}无法读取：{error}")
        return None


def workspace_file(workspace: Path, relative: Any, label: str, errors: list[str]) -> Path | None:
    raw = str(relative or "").strip().replace("\\", "/")
    posix = PurePosixPath(raw)
    if not raw or posix.is_absolute() or ".." in posix.parts:
        errors.append(f"{label}必须是/workspace下安全相对路径：{raw}")
        return None
    candidate = workspace.joinpath(*posix.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, ValueError):
        errors.append(f"{label}文件不存在或越界：{raw}")
        return None
    if not resolved.is_file():
        errors.append(f"{label}不是文件：{raw}")
        return None
    return resolved


def materials_map(payload: Any, errors: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    items = payload.get("materials") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        errors.append("materials.json必须是数组或包含materials数组")
        return {}, {}
    by_id: dict[str, str] = {}
    by_path: dict[str, str] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"materials.json第{index + 1}项不是对象")
            continue
        material_id = str(item.get("materialId") or item.get("material_id") or "").strip()
        relative = str(item.get("relativePath") or item.get("relative_path") or "").strip().replace("\\", "/")
        path = PurePosixPath(relative)
        if not material_id or not relative or path.is_absolute() or ".." in path.parts:
            errors.append(f"materials.json第{index + 1}项标识或路径无效")
            continue
        if material_id in by_id and by_id[material_id] != relative:
            errors.append(f"materialId映射冲突：{material_id}")
        if relative in by_path and by_path[relative] != material_id:
            errors.append(f"材料路径映射多个materialId：{relative}")
        by_id[material_id] = relative
        by_path[relative] = material_id
    return by_id, by_path


def state_field(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}[：:]\s*(\S.*?)\s*$", text)
    return match.group(1).strip() if match else ""


def split_codes(value: str) -> set[str]:
    if value.strip() in {"", "无"}:
        return set()
    bracketed = set(CLAIM_CODE_RE.findall(value))
    if bracketed:
        return bracketed
    return {part for part in re.split(r"[\s,，、;；]+", value.strip()) if part}


def decimal_value(value: Any, label: str, errors: list[str]) -> Decimal | None:
    if isinstance(value, bool):
        errors.append(f"{label}必须是十进制金额")
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        errors.append(f"{label}必须是十进制金额：{value}")
        return None


def positive_row(value: Any, label: str, errors: list[str]) -> int | None:
    if isinstance(value, bool):
        errors.append(f"{label}必须是正整数")
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors.append(f"{label}必须是正整数")
        return None
    if parsed < 1:
        errors.append(f"{label}必须是正整数")
        return None
    return parsed


def cited_rows(refs: list[Any], errors: list[str]) -> set[tuple[str, str, int]]:
    result: set[tuple[str, str, int]] = set()
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        relative = str(ref.get("relativePath") or "").strip().replace("\\", "/")
        locator_type = ref.get("locatorType")
        locator = ref.get("locator")
        if not isinstance(locator, dict):
            continue
        if locator_type in {"EXCEL_RANGE", "PARQUET_ROWS", "DUCKDB_ROWS"}:
            raw_location = (
                locator.get("sheet")
                if locator_type == "EXCEL_RANGE"
                else locator.get("table")
            )
            location_name = str(raw_location or "").strip()
            if isinstance(locator.get("rows"), list):
                rows = locator["rows"]
            else:
                start = positive_row(locator.get("startRow"), f"sourceRefs[{index}] startRow", errors)
                end = positive_row(locator.get("endRow"), f"sourceRefs[{index}] endRow", errors)
                if start and end and end - start > 1_000_000:
                    errors.append(f"sourceRefs[{index}]连续范围过大")
                    rows = []
                else:
                    rows = range(start, end + 1) if start and end and end >= start else []
            for row in rows:
                parsed = positive_row(row, f"sourceRefs[{index}] row", errors)
                if parsed:
                    result.add((relative, location_name, parsed))
        elif locator_type == "CSV_LINES":
            if isinstance(locator.get("lines"), list):
                rows = locator["lines"]
            else:
                start = positive_row(locator.get("startLine"), f"sourceRefs[{index}] startLine", errors)
                end = positive_row(locator.get("endLine"), f"sourceRefs[{index}] endLine", errors)
                if start and end and end - start > 1_000_000:
                    errors.append(f"sourceRefs[{index}]连续范围过大")
                    rows = []
                else:
                    rows = range(start, end + 1) if start and end and end >= start else []
            for row in rows:
                parsed = positive_row(row, f"sourceRefs[{index}] line", errors)
                if parsed:
                    result.add((relative, "", parsed))
    return result


def validate_metric(
    metric: Any,
    code: str,
    claim_coverage: str,
    report: str,
    claim_rows: set[tuple[str, str, int]],
    inventory: set[str],
    errors: list[str],
) -> None:
    label = f"指标{code}"
    if not isinstance(metric, dict):
        errors.append(f"{label}不是对象")
        return
    total_count = metric.get("totalCount")
    if not isinstance(total_count, int) or isinstance(total_count, bool) or total_count < 0:
        errors.append(f"{label} totalCount必须是非负整数")
        total_count = None
    total_amount = decimal_value(metric.get("totalAmount"), f"{label} totalAmount", errors)
    events = metric.get("events")
    groups = metric.get("groups")
    input_rows = metric.get("inputRows")
    if not isinstance(events, list):
        errors.append(f"{label} events必须是数组")
        events = []
    if not isinstance(groups, list):
        errors.append(f"{label} groups必须是数组")
        groups = []
    if not isinstance(input_rows, list):
        errors.append(f"{label} inputRows必须是数组")
        input_rows = []

    event_amounts: dict[str, Decimal] = {}
    event_observations: dict[str, set[tuple[str, str, int]]] = {}
    transaction = isinstance(metric.get("transactionChecks"), dict) and metric["transactionChecks"].get("domain") == "TRANSACTION"
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"{label} events[{index}]不是对象")
            continue
        event_id = str(event.get("eventId") or "").strip()
        amount = decimal_value(event.get("amount"), f"{label} events[{index}].amount", errors)
        if not event_id or event_id in event_amounts:
            errors.append(f"{label}事件ID缺失或重复：{event_id}")
            continue
        if amount is not None:
            event_amounts[event_id] = amount
        observations = event.get("observations")
        if not isinstance(observations, list) or not observations:
            errors.append(f"{label}事件{event_id}必须有原始观测")
            observations = []
        positions: set[tuple[str, str, int]] = set()
        for obs_index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                errors.append(f"{label}事件{event_id}观测{obs_index}不是对象")
                continue
            relative = str(observation.get("relativePath") or "").strip().replace("\\", "/")
            sheet = str(observation.get("sheet") or observation.get("table") or "").strip()
            row = positive_row(observation.get("row"), f"{label}事件{event_id}观测行", errors)
            if relative not in inventory:
                errors.append(f"{label}事件{event_id}观测路径不在物理薄清单：{relative}")
            if transaction and not str(observation.get("strongEventId") or "").strip():
                errors.append(f"{label}交易事件{event_id}观测缺少强流水ID")
            if relative and row:
                positions.add((relative, sheet, row))
        event_observations[event_id] = positions

    if total_count is not None and total_count != len(event_amounts):
        errors.append(f"{label}总笔数不等于去重后事件数")
    if total_amount is not None and total_amount != sum(event_amounts.values(), Decimal("0")):
        errors.append(f"{label}总额不等于事件金额和")

    grouped_ids: list[str] = []
    grouped_count = 0
    grouped_amount = Decimal("0")
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            errors.append(f"{label} groups[{index}]不是对象")
            continue
        ids = group.get("eventIds")
        count = group.get("count")
        amount = decimal_value(group.get("amount"), f"{label} groups[{index}].amount", errors)
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            errors.append(f"{label} groups[{index}].eventIds必须是字符串数组")
            continue
        if not isinstance(count, int) or isinstance(count, bool) or count != len(ids):
            errors.append(f"{label} groups[{index}]笔数与eventIds不一致")
        unknown = set(ids) - set(event_amounts)
        if unknown:
            errors.append(f"{label}分组引用未知事件：{sorted(unknown)}")
        expected = sum((event_amounts[item] for item in ids if item in event_amounts), Decimal("0"))
        if amount is not None and amount != expected:
            errors.append(f"{label} groups[{index}]金额与事件金额不一致")
        grouped_ids.extend(ids)
        grouped_count += count if isinstance(count, int) and not isinstance(count, bool) else 0
        grouped_amount += amount if amount is not None else Decimal("0")
    if len(grouped_ids) != len(set(grouped_ids)) or set(grouped_ids) != set(event_amounts):
        errors.append(f"{label}分组事件未无重无漏覆盖全部事件")
    if total_count is not None and grouped_count != total_count:
        errors.append(f"{label}分组笔数和不等于总笔数")
    if total_amount is not None and grouped_amount != total_amount:
        errors.append(f"{label}分组金额和不等于总额")

    input_positions: set[tuple[str, str, int]] = set()
    included_events: set[str] = set()
    for index, row_item in enumerate(input_rows):
        if not isinstance(row_item, dict):
            errors.append(f"{label} inputRows[{index}]不是对象")
            continue
        relative = str(row_item.get("relativePath") or "").strip().replace("\\", "/")
        sheet = str(row_item.get("sheet") or row_item.get("table") or "").strip()
        row = positive_row(row_item.get("row"), f"{label} inputRows[{index}].row", errors)
        disposition = row_item.get("disposition")
        reason = str(row_item.get("reason") or "").strip()
        if relative not in inventory:
            errors.append(f"{label}输入行路径不在物理薄清单：{relative}")
        if disposition not in {"INCLUDED", "EXCLUDED"}:
            errors.append(f"{label} inputRows[{index}]处置状态非法")
        if not reason:
            errors.append(f"{label} inputRows[{index}]缺少处置原因")
        position = (relative, sheet, row) if relative and row else None
        if position:
            if position in input_positions:
                errors.append(f"{label} inputRows存在重复原始行：{position}")
            input_positions.add(position)
        if disposition == "INCLUDED":
            event_id = str(row_item.get("eventId") or "").strip()
            if event_id not in event_amounts:
                errors.append(f"{label}纳入行未映射有效事件：{event_id}")
            else:
                included_events.add(event_id)
                if position and position not in event_observations.get(event_id, set()):
                    errors.append(f"{label}纳入行未出现在对应事件观测：{position}")
    if included_events != set(event_amounts):
        errors.append(f"{label}输入行未覆盖全部去重事件")

    if claim_coverage == "FULL":
        coverage = metric.get("inputCoverage")
        if not isinstance(coverage, dict) or coverage.get("mode") != "ALL_SOURCE_ROWS":
            errors.append(f"{label} FULL必须声明inputCoverage.mode=ALL_SOURCE_ROWS")
        elif coverage.get("sourceRowCount") != len(input_rows):
            errors.append(f"{label} FULL的sourceRowCount与inputRows数量不一致")
        missing_citations = input_positions - claim_rows
        if missing_citations:
            errors.append(f"{label} FULL存在未列入claim原始定位的输入行：{len(missing_citations)}条")

    if metric.get("missingValuePolicy") != "EXCLUDE_AND_DISCLOSE":
        errors.append(f"{label}缺失值策略必须为EXCLUDE_AND_DISCLOSE")
    missing_count = metric.get("missingRowCount")
    if not isinstance(missing_count, int) or isinstance(missing_count, bool) or missing_count < 0:
        errors.append(f"{label} missingRowCount必须是非负整数")

    if transaction:
        checks = metric["transactionChecks"]
        strong_fields = checks.get("strongIdFields")
        if not isinstance(strong_fields, list) or not strong_fields or any(not str(item).strip() for item in strong_fields):
            errors.append(f"{label}交易检查缺少强流水字段")
        if checks.get("mirrorCollisionChecked") is not True:
            errors.append(f"{label}未完成跨Sheet镜像碰撞")
        if checks.get("eaReimbursementPaymentChecked") is not True:
            errors.append(f"{label}未核查EA报销支付")

    report_block = str(metric.get("reportBlock") or "").strip()
    if not report_block or report_block not in report:
        errors.append(f"{label}脚本reportBlock未原样进入报告")
    if total_amount is not None and total_amount >= 10000 and total_amount % Decimal("10000") == 0:
        expected = f"{int(total_amount / Decimal('10000'))}万元"
        if expected not in report_block:
            errors.append(f"{label}整万元金额必须写为{expected}")
        bare = format(total_amount.quantize(Decimal("1")), "f")
        if re.search(rf"(?<![\d,]){re.escape(bare)}(?![\d,])", report_block):
            errors.append(f"{label} reportBlock不得使用裸数{bare}")


def validate_final(workspace: Path) -> list[str]:
    errors: list[str] = []
    inventory_path = workspace / "thin-inventory.json"
    material_ledger = workspace / "material-review-ledger.md"
    evidence_ledger = workspace / "evidence-ledger.md"
    state_path = workspace / "investigation-state.md"
    materials_path = workspace / "input" / "materials.json"
    claims_path = workspace / "evidence-items" / "final-claims.json"
    report_path = workspace / "hermes-case-report.md"
    review_path = workspace / "review-result.md"
    required = (
        inventory_path,
        material_ledger,
        evidence_ledger,
        state_path,
        materials_path,
        claims_path,
        report_path,
        review_path,
    )
    for path in required:
        if not path.is_file():
            errors.append(f"终检缺少文件：{path.relative_to(workspace)}")
    if errors:
        return errors

    inventory = inventory_paths(inventory_path, errors)
    material_records = parse_records(material_ledger, "M", 2)
    evidence_records = parse_records(evidence_ledger, "E", 2)
    delegation_records = parse_records(state_path, "D", 3)
    question_records = parse_records(state_path, "Q", 3)
    technique_records = parse_records(state_path, "T", 3)
    errors.extend(validate_materials(inventory, material_records, True))
    errors.extend(validate_evidence(inventory, evidence_records))
    errors.extend(validate_delegations(delegation_records, True))

    state_text = state_path.read_text(encoding="utf-8")
    errors.extend(validate_questions(
        question_records,
        True,
        state_field(state_text, "交易分析"),
        state_field(state_text, "交易分析不适用理由"),
    ))
    errors.extend(validate_techniques(
        technique_records,
        True,
        state_field(state_text, "技战法目录检查"),
    ))
    closure = state_field(state_text, "结案覆盖")
    content_statuses = [fields.get("内容审阅") for _, fields in material_records]
    if closure not in {"PARTIAL", "FULL"}:
        errors.append("结案覆盖必须为PARTIAL或FULL")
    if closure == "FULL" and any(status not in {"REVIEWED", "NON_MATERIAL"} for status in content_statuses):
        errors.append("结案覆盖FULL但存在未完整内容审阅的材料")
    if closure == "PARTIAL" and any(status == "UNREAD" for status in content_statuses):
        errors.append("结案覆盖PARTIAL仍不能遗留UNREAD内容")

    catalog = load_json(materials_path, "materials.json", errors)
    by_id, by_path = materials_map(catalog, errors)
    inventory_set = set(inventory)
    claims_payload = load_json(claims_path, "final-claims.json", errors)
    if not isinstance(claims_payload, dict) or set(claims_payload) != {"claims"} or not isinstance(claims_payload.get("claims"), list):
        errors.append("final-claims.json结构必须严格为claims数组")
        claims: list[Any] = []
    else:
        claims = claims_payload["claims"]
        if not claims:
            errors.append("调查结案final-claims.json至少需要一项claim")
    report = report_path.read_text(encoding="utf-8")
    review = review_path.read_text(encoding="utf-8")
    substantive_report = [
        CLAIM_CODE_RE.sub("", line).strip()
        for line in report.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if sum(len(line) for line in substantive_report) < 10:
        errors.append("最终调查报告缺少实质正文")
    if not re.match(r"^VERDICT:\s*(PASS|NEEDS_HUMAN)\s*$", review.splitlines()[0] if review.splitlines() else ""):
        errors.append("独立复核必须完成且首行VERDICT为PASS或NEEDS_HUMAN")
    if sum(len(line.strip()) for line in review.splitlines()[1:] if line.strip()) < 10:
        errors.append("独立复核结果缺少实质正文")
    claim_by_code: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claims[{index}]不是对象")
            continue
        code = str(claim.get("claimCode") or "").strip()
        claim_type = str(claim.get("claimType") or "").strip()
        coverage = str(claim.get("coverage") or "").strip()
        statement = str(claim.get("statement") or "").strip()
        if not code or code in claim_by_code:
            errors.append(f"claimCode缺失或重复：{code}")
            continue
        claim_by_code[code] = claim
        if claim_type not in CLAIM_TYPES:
            errors.append(f"claim {code}类型非法：{claim_type}")
        if coverage not in COVERAGES:
            errors.append(f"claim {code} coverage非法：{coverage}")
        limitations = claim.get("limitations")
        if not isinstance(limitations, list) or any(not str(item or "").strip() for item in limitations):
            errors.append(f"claim {code} limitations必须是非空字符串数组")
        if f"[{code}]" not in report or not statement or statement not in report:
            errors.append(f"claim {code}陈述或标签未与最终报告一致")
        if closure != "FULL" and coverage == "FULL":
            errors.append(f"结案覆盖{closure}时claim {code}不得使用FULL")
        sources = claim.get("sourceRefs")
        calculations = claim.get("calculationRefs")
        if not isinstance(sources, list) or not isinstance(calculations, list):
            errors.append(f"claim {code}引用字段必须是数组")
            continue
        if claim_type not in FINAL_TYPES_WITHOUT_SOURCE and not sources:
            errors.append(f"claim {code}必须有原始来源")
        claim_rows = cited_rows(sources, errors)
        for source_index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"claim {code} sourceRefs[{source_index}]不是对象")
                continue
            material_id = str(source.get("materialId") or "").strip()
            relative = str(source.get("relativePath") or "").strip().replace("\\", "/")
            if not relative:
                errors.append(f"claim {code} sourceRefs[{source_index}]缺少relativePath")
            if relative not in inventory_set:
                errors.append(f"claim {code}来源路径不在物理薄清单：{relative}")
            if by_id.get(material_id) != relative or by_path.get(relative) != material_id:
                errors.append(f"claim {code} materialId与物理路径不精确一致：{material_id} -> {relative}")
            if not str(source.get("description") or "").strip():
                errors.append(f"claim {code} sourceRefs[{source_index}]缺少description")
        metric_codes = claim.get("metricCodes")
        if claim_type == "CALCULATION":
            if not isinstance(metric_codes, list) or not metric_codes or any(not isinstance(item, str) or not item for item in metric_codes):
                errors.append(f"计算claim {code}必须有metricCodes")
                metric_codes = []
            found_metrics: dict[str, Any] = {}
            for calc_index, calculation in enumerate(calculations):
                if not isinstance(calculation, dict):
                    errors.append(f"claim {code} calculationRefs[{calc_index}]不是对象")
                    continue
                if not str(calculation.get("purpose") or "").strip():
                    errors.append(f"claim {code} calculationRefs[{calc_index}]缺少purpose")
                if not str(calculation.get("calculationBasis") or "").strip():
                    errors.append(f"claim {code} calculationRefs[{calc_index}]缺少calculationBasis")
                workspace_file(workspace, calculation.get("scriptPath"), f"claim {code}计算脚本", errors)
                result_file = workspace_file(workspace, calculation.get("resultPath"), f"claim {code}计算结果", errors)
                if result_file:
                    result_payload = load_json(result_file, f"claim {code}计算结果", errors)
                    metrics = result_payload.get("publishedMetrics") if isinstance(result_payload, dict) else None
                    if not isinstance(metrics, list):
                        errors.append(f"claim {code}计算结果缺少publishedMetrics")
                    else:
                        for metric in metrics:
                            if isinstance(metric, dict) and str(metric.get("metricCode") or ""):
                                metric_code = str(metric["metricCode"])
                                if metric_code in found_metrics:
                                    errors.append(f"claim {code}重复指标：{metric_code}")
                                found_metrics[metric_code] = metric
            missing_metrics = set(metric_codes) - set(found_metrics)
            if missing_metrics:
                errors.append(f"claim {code}结果缺少metricCodes：{sorted(missing_metrics)}")
            for metric_code in metric_codes:
                if metric_code in found_metrics:
                    validate_metric(
                        found_metrics[metric_code],
                        metric_code,
                        coverage,
                        report,
                        claim_rows,
                        inventory_set,
                        errors,
                    )

    report_codes = set(CLAIM_CODE_RE.findall(report))
    if report_codes != set(claim_by_code):
        errors.append("最终报告claim标签集合与final-claims.json不一致")
    for line_number, line in enumerate(report.splitlines(), start=1):
        if any(word in line for word in QUALITATIVE_WORDS):
            codes = set(CLAIM_CODE_RE.findall(line))
            if not codes or any(claim_by_code.get(code, {}).get("claimType") not in FINAL_TYPES_WITHOUT_SOURCE for code in codes):
                errors.append(f"报告第{line_number}行使用强定性词但未标为HYPOTHESIS/GAP")

    evidence_by_claim: dict[str, list[dict[str, str]]] = {}
    pending_suspicion = False
    provisionally_excluded = False
    for evidence_id, fields in evidence_records:
        codes = split_codes(fields.get("报告Claim", ""))
        evidence_status = fields.get("状态", "")
        if evidence_status in {"CANDIDATE", "HYPOTHESIS", "GAP"}:
            pending_suspicion = True
        if evidence_status == "REFUTED":
            provisionally_excluded = True
        if evidence_status in {"CANDIDATE", "HYPOTHESIS", "REFUTED", "GAP"} and not codes:
            errors.append(f"疑点证据{evidence_id}未进入最终报告Claim")
        source = fields.get("文件", "")
        material_id = fields.get("materialId", "")
        catalog_id = by_path.get(source)
        if catalog_id:
            if material_id != catalog_id:
                errors.append(f"证据{evidence_id} materialId与物理路径不一致")
        elif source in inventory_set:
            if not material_id.startswith("无"):
                errors.append(f"未编目证据{evidence_id}不得借用materialId")
            if fields.get("状态") not in {"CANDIDATE", "GAP"}:
                errors.append(f"未编目证据{evidence_id}只能为CANDIDATE或GAP")
        for code in codes:
            if code not in claim_by_code:
                errors.append(f"证据{evidence_id}映射不存在的claim：{code}")
            else:
                evidence_by_claim.setdefault(code, []).append(fields)
                if evidence_status == "REFUTED" and claim_by_code[code].get("claimType") != "HYPOTHESIS":
                    errors.append(f"暂拟排除疑点{evidence_id}必须映射HYPOTHESIS claim")
                if not catalog_id and claim_by_code[code].get("claimType") != "GAP":
                    errors.append(f"未编目证据{evidence_id}只能映射GAP claim")
    if pending_suspicion and "待核疑点" not in report:
        errors.append("最终报告缺少待核疑点章节")
    if provisionally_excluded and "暂拟排除的疑点" not in report:
        errors.append("最终报告缺少暂拟排除的疑点章节")
    for code, claim in claim_by_code.items():
        mapped = evidence_by_claim.get(code, [])
        if not mapped:
            errors.append(f"claim {code}未映射证据台账")
        for source in claim.get("sourceRefs", []) if isinstance(claim.get("sourceRefs"), list) else []:
            if isinstance(source, dict):
                relative = str(source.get("relativePath") or "").strip()
                material_id = str(source.get("materialId") or "").strip()
                if not any(item.get("文件") == relative and item.get("materialId") == material_id for item in mapped):
                    errors.append(f"claim {code}来源未与证据台账一致：{relative}")
        if claim.get("claimType") == "RELATION" and not any(
            item.get("状态") == "VERIFIED" and "明确" in item.get("字段角色", "")
            for item in mapped
        ):
            errors.append(f"关系claim {code}缺少原件明示关系字段的VERIFIED证据")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    try:
        workspace = args.workspace.resolve(strict=True)
    except OSError as error:
        print("FINAL_GATE_FAIL")
        print(json.dumps({"status": "FAIL", "errors": [f"workspace无法读取：{error}"]}, ensure_ascii=False, indent=2))
        return 1
    if not workspace.is_dir():
        print("FINAL_GATE_FAIL")
        print(json.dumps({"status": "FAIL", "errors": ["workspace不是目录"]}, ensure_ascii=False, indent=2))
        return 1
    errors = validate_final(workspace)
    if errors:
        print("FINAL_GATE_FAIL")
        print(json.dumps({"status": "FAIL", "errorCount": len(errors), "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    print("FINAL_GATE_PASS")
    print(json.dumps({"status": "PASS", "errors": []}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
