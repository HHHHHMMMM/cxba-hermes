#!/usr/bin/env python3
"""Validate CXBA investigation notebook structure without reading case content."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath


FIELD_RE = re.compile(r"^([^：:\n]+)[：:]\s*(.*)$")


def clean(value: str) -> str:
    return value.strip().strip("`").strip()


def parse_records(path: Path, prefix: str, level: int) -> list[tuple[str, dict[str, str]]]:
    text = path.read_text(encoding="utf-8")
    marker = re.compile(
        rf"(?m)^{'#' * level}\s+\[?({re.escape(prefix)}\d+)\]?\s*$"
    )
    matches = list(marker.finditer(text))
    records: list[tuple[str, dict[str, str]]] = []
    for index, match in enumerate(matches):
        following_heading = re.search(
            rf"(?m)^#{{1,{level}}}\s+", text[match.end() :]
        )
        end = (
            match.end() + following_heading.start()
            if following_heading
            else len(text)
        )
        fields: dict[str, str] = {}
        for line in text[match.end() : end].splitlines():
            field_match = FIELD_RE.match(line.strip().lstrip("- "))
            if field_match:
                fields[field_match.group(1).strip()] = clean(field_match.group(2))
        records.append((match.group(1), fields))
    return records


def require_fields(
    kind: str,
    record_id: str,
    fields: dict[str, str],
    required: tuple[str, ...],
    errors: list[str],
) -> None:
    for field in required:
        if not fields.get(field, "").strip():
            errors.append(f"{kind} {record_id} 缺少字段：{field}")


def inventory_paths(path: Path, errors: list[str]) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"薄清单无法读取：{error}")
        return []
    files = payload.get("files")
    if not isinstance(files, list):
        errors.append("薄清单 files 必须是数组")
        return []
    paths = [item.get("path") for item in files if isinstance(item, dict)]
    if any(not isinstance(item, str) or not item for item in paths):
        errors.append("薄清单每个文件必须有非空 path")
        return []
    if payload.get("fileCount") != len(paths):
        errors.append("薄清单 fileCount 与 files 数量不一致")
    if len(set(paths)) != len(paths):
        errors.append("薄清单存在重复 path")
    return paths


def validate_materials(
    inventory: list[str], records: list[tuple[str, dict[str, str]]], final: bool
) -> list[str]:
    errors: list[str] = []
    required = (
        "路径",
        "状态",
        "物理清点",
        "结构盘点",
        "内容审阅",
        "格式与结构",
        "实际覆盖",
        "主要内容",
        "可能有用的点",
        "可疑线索",
        "限制与失败",
        "下一步",
    )
    valid_statuses = {"UNREAD", "PARTIAL", "REVIEWED", "FAILED", "NON_MATERIAL"}
    ledger_paths: list[str] = []
    seen_ids: set[str] = set()
    for record_id, fields in records:
        if record_id in seen_ids:
            errors.append(f"材料编号重复：{record_id}")
        seen_ids.add(record_id)
        require_fields("材料", record_id, fields, required, errors)
        path = clean(fields.get("路径", ""))
        ledger_paths.append(path)
        status = fields.get("状态", "")
        if status not in valid_statuses:
            errors.append(f"材料 {record_id} 状态非法：{status}")
        if status == "NON_MATERIAL" and PurePosixPath(path).name != ".DS_Store":
            errors.append(f"材料 {record_id} 仅 .DS_Store 可标 NON_MATERIAL")
        if final and status == "UNREAD":
            errors.append(f"材料 {record_id} 报告前仍为 UNREAD")
        if fields.get("物理清点") != "INVENTORIED":
            errors.append(f"材料 {record_id} 物理清点必须为 INVENTORIED")
        for field in ("结构盘点", "内容审阅"):
            field_status = fields.get(field, "")
            if field_status not in valid_statuses:
                errors.append(f"材料 {record_id} {field}状态非法：{field_status}")
        if status != fields.get("内容审阅"):
            errors.append(f"材料 {record_id} 状态必须与内容审阅一致")
        if final and fields.get("内容审阅") == "UNREAD":
            errors.append(f"材料 {record_id} 报告前内容仍为 UNREAD")
        if path.lower().endswith(".zip"):
            require_fields(
                "ZIP材料",
                record_id,
                fields,
                ("ZIP成员数", "ZIP成员检查", "成员清单路径"),
                errors,
            )
            zip_status = fields.get("ZIP成员检查", "")
            if zip_status not in {"REVIEWED", "PARTIAL", "FAILED"}:
                errors.append(f"ZIP材料 {record_id} 成员检查状态非法：{zip_status}")
            if status == "REVIEWED" and zip_status != "REVIEWED":
                errors.append(f"ZIP材料 {record_id} 文件已 REVIEWED 但成员未 REVIEWED")
            if zip_status == "REVIEWED" and not fields.get("ZIP成员数", "").isdigit():
                errors.append(f"ZIP材料 {record_id} 成员数必须是整数")
            member_list = fields.get("成员清单路径", "")
            if member_list and not member_list.startswith("/workspace/"):
                errors.append(f"ZIP材料 {record_id} 成员清单必须位于 /workspace")

    inventory_set = set(inventory)
    ledger_set = set(ledger_paths)
    if len(ledger_paths) != len(inventory):
        errors.append(
            f"材料台账数量 {len(ledger_paths)} 与薄清单 {len(inventory)} 不一致"
        )
    if len(ledger_set) != len(ledger_paths):
        errors.append("材料台账存在重复路径")
    for path in sorted(inventory_set - ledger_set):
        errors.append(f"材料台账缺少薄清单路径：{path}")
    for path in sorted(ledger_set - inventory_set):
        errors.append(f"材料台账出现薄清单外路径：{path}")
    return errors


def validate_evidence(
    inventory: list[str], records: list[tuple[str, dict[str, str]]]
) -> list[str]:
    errors: list[str] = []
    required = (
        "状态",
        "问题",
        "主体与角色",
        "事实或疑点",
        "文件",
        "materialId",
        "Sheet/页",
        "Excel原始行/唯一流水",
        "字段角色",
        "收付方向",
        "口径",
        "关键字段",
        "方法",
        "支持证据",
        "反证与正常解释",
        "限制与缺口",
        "主调查回查",
        "下一步",
        "报告Claim",
    )
    valid_statuses = {"CANDIDATE", "VERIFIED", "REFUTED", "HYPOTHESIS", "GAP"}
    inventory_set = set(inventory)
    seen_ids: set[str] = set()
    for record_id, fields in records:
        if record_id in seen_ids:
            errors.append(f"证据编号重复：{record_id}")
        seen_ids.add(record_id)
        require_fields("证据", record_id, fields, required, errors)
        status = fields.get("状态", "")
        if status not in valid_statuses:
            errors.append(f"证据 {record_id} 状态非法：{status}")
        source = clean(fields.get("文件", ""))
        source_is_na = source.startswith("不适用") or source in {"无", "未知"}
        if not source_is_na and source not in inventory_set:
            errors.append(f"证据 {record_id} 文件不在物理薄清单：{source}")
        if status in {"CANDIDATE", "VERIFIED", "REFUTED"} and source_is_na:
            errors.append(f"证据 {record_id} 状态 {status} 必须定位物理文件")
        if status == "VERIFIED" and "已回查" not in fields.get("主调查回查", ""):
            errors.append(f"证据 {record_id} VERIFIED 但主调查未回查")
    return errors


def validate_delegations(
    records: list[tuple[str, dict[str, str]]], final: bool
) -> list[str]:
    errors: list[str] = []
    required = (
        "状态",
        "任务",
        "输入范围",
        "输出路径",
        "定位粒度",
        "候选上限",
        "主调查抽核",
        "拒绝原因",
    )
    valid_statuses = {"PENDING", "RECEIVED", "VERIFIED", "REJECTED"}
    seen_ids: set[str] = set()
    for record_id, fields in records:
        if record_id in seen_ids:
            errors.append(f"委派编号重复：{record_id}")
        seen_ids.add(record_id)
        require_fields("委派", record_id, fields, required, errors)
        status = fields.get("状态", "")
        if status not in valid_statuses:
            errors.append(f"委派 {record_id} 状态非法：{status}")
        limit = fields.get("候选上限", "")
        if not limit.isdigit() or not 1 <= int(limit) <= 200:
            errors.append(f"委派 {record_id} 候选上限必须为 1 至 200")
        if final and status in {"PENDING", "RECEIVED"}:
            errors.append(f"委派 {record_id} 报告前未达到终态：{status}")
        if status == "VERIFIED" and fields.get("主调查抽核") != "已抽核":
            errors.append(f"委派 {record_id} VERIFIED 但主调查未抽核")
        if status == "REJECTED" and fields.get("拒绝原因") in {"", "无"}:
            errors.append(f"委派 {record_id} REJECTED 但未写拒绝原因")
    return errors


def validate_questions(
    records: list[tuple[str, dict[str, str]]],
    final: bool,
    transaction_analysis: str,
    transaction_reason: str,
) -> list[str]:
    errors: list[str] = []
    required = (
        "状态",
        "来源",
        "分析维度",
        "专项Skill",
        "选择理由",
        "问题",
        "对象与角色",
        "已检查范围",
        "支持假设",
        "正常解释",
        "反向假设",
        "证据或缺口",
        "未覆盖与原因",
        "下一步",
        "报告Claim",
    )
    valid_statuses = {
        "OPEN",
        "IN_PROGRESS",
        "VERIFIED",
        "EXCLUDED",
        "GAP",
        "NOT_APPLICABLE",
    }
    terminal_statuses = {"VERIFIED", "EXCLUDED", "GAP", "NOT_APPLICABLE"}
    transaction_dimensions = {
        "ACCOUNT_COVERAGE",
        "DIRECT_FLOW",
        "INDIRECT_FLOW",
        "CROSS_SOURCE",
        "TEMPORAL",
        "COUNTER_EVIDENCE",
        "MATERIAL_GAP",
    }
    seen_ids: set[str] = set()
    dimensions: set[str] = set()
    for record_id, fields in records:
        if record_id in seen_ids:
            errors.append(f"问题编号重复：{record_id}")
        seen_ids.add(record_id)
        require_fields("问题", record_id, fields, required, errors)
        status = fields.get("状态", "")
        if status not in valid_statuses:
            errors.append(f"问题 {record_id} 状态非法：{status}")
        if final and status not in terminal_statuses:
            errors.append(f"问题 {record_id} 报告前未达到终态：{status}")
        dimension = fields.get("分析维度", "")
        if dimension:
            dimensions.add(dimension)
        if status == "NOT_APPLICABLE" and fields.get("未覆盖与原因", "") in {"", "无"}:
            errors.append(f"问题 {record_id} NOT_APPLICABLE但未说明原因")

    if final and not records:
        errors.append("首次结案缺少问题覆盖矩阵")
    if transaction_analysis not in {"REQUIRED", "NOT_APPLICABLE"}:
        errors.append("交易分析必须为REQUIRED或NOT_APPLICABLE")
    elif transaction_analysis == "NOT_APPLICABLE":
        if transaction_reason in {"", "无"}:
            errors.append("交易分析NOT_APPLICABLE必须说明业务理由")
    elif final:
        missing = sorted(transaction_dimensions - dimensions)
        if missing:
            errors.append(f"交易分析问题覆盖不完整：{','.join(missing)}")
    return errors


def validate_techniques(
    records: list[tuple[str, dict[str, str]]],
    final: bool,
    catalog_status: str,
) -> list[str]:
    errors: list[str] = []
    required = (
        "技战法路径",
        "标题",
        "状态",
        "适用场景",
        "所需材料",
        "现有材料",
        "缺失材料",
        "适用判断",
        "转入问题",
    )
    valid_statuses = {"APPLICABLE", "COMPLETED", "DATA_MISSING", "NOT_APPLICABLE"}
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for record_id, fields in records:
        if record_id in seen_ids:
            errors.append(f"技战法编号重复：{record_id}")
        seen_ids.add(record_id)
        require_fields("技战法", record_id, fields, required, errors)
        path = fields.get("技战法路径", "")
        if path in seen_paths:
            errors.append(f"技战法路径重复：{path}")
        seen_paths.add(path)
        status = fields.get("状态", "")
        if status not in valid_statuses:
            errors.append(f"技战法 {record_id} 状态非法：{status}")
        if final and status == "APPLICABLE":
            errors.append(f"技战法 {record_id} 报告前仍未执行：APPLICABLE")
        if status == "COMPLETED" and fields.get("转入问题", "") in {"", "无"}:
            errors.append(f"技战法 {record_id} COMPLETED但未转入调查问题")
        if status == "DATA_MISSING" and fields.get("缺失材料", "") in {"", "无"}:
            errors.append(f"技战法 {record_id} DATA_MISSING但未写缺失材料")
        if status == "NOT_APPLICABLE" and fields.get("适用判断", "") in {"", "无"}:
            errors.append(f"技战法 {record_id} NOT_APPLICABLE但未说明理由")

    if catalog_status not in {"COMPLETED", "NONE"}:
        errors.append("技战法目录检查必须为COMPLETED或NONE")
    elif catalog_status == "COMPLETED" and not records:
        errors.append("技战法目录检查COMPLETED但没有覆盖记录")
    elif catalog_status == "NONE" and records:
        errors.append("技战法目录检查NONE但存在覆盖记录")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--material-ledger", type=Path, required=True)
    parser.add_argument("--evidence-ledger", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    inventory = inventory_paths(args.inventory, errors)
    if not errors:
        errors.extend(
            validate_materials(
                inventory, parse_records(args.material_ledger, "M", 2), args.final
            )
        )
        errors.extend(
            validate_evidence(inventory, parse_records(args.evidence_ledger, "E", 2))
        )
        errors.extend(
            validate_delegations(parse_records(args.state, "D", 3), args.final)
        )
        state_text = args.state.read_text(encoding="utf-8")
        transaction_analysis = clean(
            re.search(r"(?m)^交易分析[：:]\s*(\S.*?)\s*$", state_text).group(1)
        ) if re.search(r"(?m)^交易分析[：:]\s*(\S.*?)\s*$", state_text) else ""
        transaction_reason = clean(
            re.search(r"(?m)^交易分析不适用理由[：:]\s*(\S.*?)\s*$", state_text).group(1)
        ) if re.search(r"(?m)^交易分析不适用理由[：:]\s*(\S.*?)\s*$", state_text) else ""
        errors.extend(
            validate_questions(
                parse_records(args.state, "Q", 3),
                args.final,
                transaction_analysis,
                transaction_reason,
            )
        )
        technique_status_match = re.search(
            r"(?m)^技战法目录检查[：:]\s*(\S.*?)\s*$", state_text
        )
        errors.extend(
            validate_techniques(
                parse_records(args.state, "T", 3),
                args.final,
                clean(technique_status_match.group(1)) if technique_status_match else "",
            )
        )

    payload = {"status": "failed" if errors else "passed", "errors": errors}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
