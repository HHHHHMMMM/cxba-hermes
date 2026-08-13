#!/usr/bin/env python3
"""Validate only the generic CXBA claim delivery structure and references."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any


CLAIM_CODE = re.compile(r"[A-Z][A-Z0-9_-]{0,31}")
MATERIAL_ID_TOKEN = re.compile(
    r"materialId(?:\*\*)?[`\"']?\s*[：:=]\s*[`\"']?([A-Za-z0-9_-]+)"
)
CLAIM_TYPES = {"FACT", "CALCULATION", "RELATION", "FINDING", "HYPOTHESIS", "GAP"}
COVERAGES = {"FULL", "PARTIAL", "NONE"}
SOURCE_OPTIONAL_TYPES = {"HYPOTHESIS", "GAP"}
SOURCE_ROLES = {"SUPPORT", "COUNTER"}
ROW_LOCATORS = {"EXCEL_RANGE", "PARQUET_ROWS", "DUCKDB_ROWS"}
OOXML_REQUIRED_MEMBERS = {
    ".xlsx": {"[Content_Types].xml", "xl/workbook.xml"},
    ".xlsm": {"[Content_Types].xml", "xl/workbook.xml"},
    ".docx": {"[Content_Types].xml", "word/document.xml"},
    ".pptx": {"[Content_Types].xml", "ppt/presentation.xml"},
}
BINARY_SUFFIXES = set(OOXML_REQUIRED_MEMBERS) | {
    ".xls",
    ".pdf",
    ".parquet",
    ".duckdb",
}


def clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_json(path: Path, label: str, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"{label}无法读取：{error}")
        return None


def safe_relative(value: Any, label: str, errors: list[str]) -> PurePosixPath | None:
    raw = clean(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        errors.append(f"{label}必须是workspace内安全相对路径")
        return None
    return path


def material_mapping(payload: Any, errors: list[str]) -> dict[str, str]:
    rows = payload.get("materials") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        errors.append("materials.json必须是数组或包含materials数组")
        return {}
    mapping: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"materials[{index}]必须是对象")
            continue
        material_id = clean(row.get("materialId"))
        relative_path = clean(row.get("relativePath")).replace("\\", "/")
        if material_id and relative_path:
            mapping[material_id] = relative_path
    return mapping


def artifact_material_identity_errors(
    path: Path, label: str, materials: dict[str, str], errors: list[str]
) -> None:
    """Reject path/materialId pairs in deliverables that disagree with materials.json."""
    suffix = path.suffix.lower()
    if suffix in BINARY_SUFFIXES:
        validate_binary_artifact(path, label, errors)
        return
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            errors.append(f"{label}过大，无法执行证据身份校验")
            return
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        errors.append(f"{label}无法执行证据身份校验：{error}")
        return
    for index, line in enumerate(lines):
        material_ids = MATERIAL_ID_TOKEN.findall(line)
        if not material_ids:
            continue
        window = " ".join(lines[max(0, index - 1) : min(len(lines), index + 2)]).replace("\\", "/")
        referenced_paths: set[str] = set()
        for relative_path in materials.values():
            if f"/data/{relative_path}" in window:
                referenced_paths.add(relative_path)
            elif ("相对路径" in window or "relativePath" in window) and relative_path in window:
                referenced_paths.add(relative_path)
        if not referenced_paths:
            continue
        for material_id in material_ids:
            if materials.get(material_id) not in referenced_paths:
                errors.append(f"{label}第{index + 1}行包含materialId与文件路径不一致：{material_id}")


def validate_binary_artifact(path: Path, label: str, errors: list[str]) -> None:
    """Validate known binary containers without decoding them as UTF-8 text."""
    suffix = path.suffix.lower()
    try:
        if suffix in OOXML_REQUIRED_MEMBERS:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                missing = OOXML_REQUIRED_MEMBERS[suffix] - names
                if missing:
                    errors.append(f"{label}不是有效的{suffix}文件：缺少{sorted(missing)}")
                    return
                broken = archive.testzip()
                if broken:
                    errors.append(f"{label}的{suffix}压缩内容损坏：{broken}")
            return
        if suffix == ".pdf":
            if path.read_bytes()[:5] != b"%PDF-":
                errors.append(f"{label}不是有效的PDF文件")
            return
        if suffix == ".parquet":
            with path.open("rb") as handle:
                head = handle.read(4)
                handle.seek(-4, 2)
                tail = handle.read(4)
            if head != b"PAR1" or tail != b"PAR1":
                errors.append(f"{label}不是有效的Parquet文件")
            return
        if suffix == ".xls":
            if path.read_bytes()[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
                errors.append(f"{label}不是有效的XLS文件")
            return
        if suffix == ".duckdb" and path.stat().st_size == 0:
            errors.append(f"{label}不是有效的DuckDB文件：文件为空")
    except (OSError, zipfile.BadZipFile, ValueError) as error:
        errors.append(f"{label}不是有效的{suffix or '二进制'}文件：{error}")


def comparable_value(value: Any, label: str, errors: list[str]) -> tuple[str, Any] | None:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        errors.append(f"{label}必须是非空标量")
        return None
    if isinstance(value, float):
        errors.append(f"{label}不得使用浮点数，金额请使用十进制字符串")
        return None
    raw = str(value).strip()
    if not raw:
        errors.append(f"{label}必须是非空标量")
        return None
    try:
        return ("number", Decimal(raw.replace(",", "")))
    except InvalidOperation:
        return ("text", raw)


def validate_published_metric(
    metric: Any,
    metric_code: str,
    claim_label: str,
    statement: str,
    answer: str,
    require_artifact_reconciliation: bool,
    errors: list[str],
) -> None:
    label = f"{claim_label}指标{metric_code}"
    if not isinstance(metric, dict):
        errors.append(f"{label}不是对象")
        return
    report_block = clean(metric.get("reportBlock"))
    if not report_block:
        errors.append(f"{label}缺少脚本生成的reportBlock")
    else:
        if report_block not in statement:
            errors.append(f"{label}的reportBlock未原样进入claim.statement")
        if report_block not in answer:
            errors.append(f"{label}的reportBlock未原样进入最终回复")

    has_reconciliation = "sourceValue" in metric or "artifactValue" in metric
    if not require_artifact_reconciliation and not has_reconciliation:
        return
    if "sourceValue" not in metric or "artifactValue" not in metric:
        errors.append(f"{label}缺少sourceValue或artifactValue")
        return
    source_value = comparable_value(metric["sourceValue"], f"{label}.sourceValue", errors)
    artifact_value = comparable_value(metric["artifactValue"], f"{label}.artifactValue", errors)
    if source_value is not None and artifact_value is not None and source_value != artifact_value:
        errors.append(f"{label}原始复算值与交付物回读值不一致")


def positive_ints(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, int) and item > 0 for item in value)
        and value == sorted(set(value))
    )


def positive_range(locator: dict[str, Any], start: str, end: str) -> bool:
    return (
        isinstance(locator.get(start), int)
        and isinstance(locator.get(end), int)
        and 0 < locator[start] <= locator[end]
    )


def validate_locator(source: dict[str, Any], label: str, errors: list[str]) -> None:
    locator_type = clean(source.get("locatorType"))
    locator = source.get("locator")
    if not locator_type or not isinstance(locator, dict) or not locator:
        errors.append(f"{label}缺少locatorType或非空locator")
        return
    if locator_type == "EXCEL_RANGE" and not clean(locator.get("sheet")):
        errors.append(f"{label}的Excel定位缺少Sheet")
    if locator_type in {"PARQUET_ROWS", "DUCKDB_ROWS"} and not clean(locator.get("table")):
        errors.append(f"{label}的表定位缺少table")
    if locator_type in ROW_LOCATORS:
        if not positive_ints(locator.get("rows")) and not positive_range(
            locator, "startRow", "endRow"
        ):
            errors.append(f"{label}缺少有效原始行定位")
    elif locator_type == "CSV_LINES":
        if not positive_ints(locator.get("lines")) and not positive_range(
            locator, "startLine", "endLine"
        ):
            errors.append(f"{label}缺少有效原始行定位")
    elif locator_type == "PDF_PAGE":
        pages = locator.get("pages")
        if not (isinstance(locator.get("page"), int) and locator["page"] > 0) and not positive_ints(pages):
            errors.append(f"{label}缺少有效页码")


def validate_workspace_file(
    workspace: Path, value: Any, label: str, errors: list[str]
) -> Path | None:
    relative = safe_relative(value, label, errors)
    if relative is None:
        return None
    path = workspace.joinpath(*relative.parts)
    if not path.is_file() or path.is_symlink():
        errors.append(f"{label}引用文件不存在或不是普通文件：{relative.as_posix()}")
        return None
    return path


def load_published_metrics(
    path: Path, label: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    if path.suffix.lower() != ".json":
        errors.append(f"{label}必须是UTF-8 JSON；Excel等二进制交付物请写入artifactPaths")
        return {}
    payload = load_json(path, label, errors)
    metrics = payload.get("publishedMetrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, list):
        errors.append(f"{label}缺少publishedMetrics数组")
        return {}
    found: dict[str, dict[str, Any]] = {}
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            errors.append(f"{label}.publishedMetrics[{index}]必须是对象")
            continue
        code = clean(metric.get("metricCode"))
        if not code or code in found:
            errors.append(f"{label}.publishedMetrics[{index}].metricCode缺失或重复")
            continue
        found[code] = metric
    return found


def validate(workspace: Path, answer_path: Path) -> list[str]:
    errors: list[str] = []
    claims_payload = load_json(
        workspace / "evidence-items" / "final-claims.json", "final-claims.json", errors
    )
    materials_payload = load_json(
        workspace / "input" / "materials.json", "materials.json", errors
    )
    try:
        answer = answer_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        errors.append(f"最终回复草稿无法读取：{error}")
        answer = ""
    if not isinstance(claims_payload, dict) or set(claims_payload) != {"claims"}:
        errors.append("final-claims.json顶层必须只包含claims")
        return errors
    claims = claims_payload.get("claims")
    if not isinstance(claims, list):
        errors.append("claims必须是数组")
        return errors
    materials = material_mapping(materials_payload, errors)
    artifact_paths: dict[Path, str] = {answer_path: "最终回复草稿"}
    notebook = workspace / "analysis-notebook.md"
    if notebook.is_file() and not notebook.is_symlink():
        artifact_paths[notebook] = "分析笔记"
    seen: set[str] = set()
    for index, claim in enumerate(claims):
        label = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{label}必须是对象")
            continue
        code = clean(claim.get("claimCode"))
        if not CLAIM_CODE.fullmatch(code) or code in seen:
            errors.append(f"{label}.claimCode缺失、重复或格式错误")
        else:
            seen.add(code)
            if f"[{code}]" not in answer:
                errors.append(f"最终回复未引用[{code}]")
        if not clean(claim.get("statement")):
            errors.append(f"{label}.statement不能为空")
        statement = clean(claim.get("statement"))
        claim_type = clean(claim.get("claimType"))
        if claim_type not in CLAIM_TYPES:
            errors.append(f"{label}.claimType无效")
        if clean(claim.get("coverage")) not in COVERAGES:
            errors.append(f"{label}.coverage无效")
        if not isinstance(claim.get("limitations"), list):
            errors.append(f"{label}.limitations必须是数组")
        sources = claim.get("sourceRefs")
        calculations = claim.get("calculationRefs")
        if not isinstance(sources, list) or not isinstance(calculations, list):
            errors.append(f"{label}的sourceRefs和calculationRefs必须是数组")
            continue
        if claim_type not in SOURCE_OPTIONAL_TYPES and not sources:
            errors.append(f"{label}缺少原始证据")
        if claim_type == "CALCULATION" and not calculations:
            errors.append(f"{label}缺少计算引用")
        metric_codes = claim.get("metricCodes")
        if claim_type == "CALCULATION":
            if not (
                isinstance(metric_codes, list)
                and bool(metric_codes)
                and all(isinstance(item, str) and item.strip() for item in metric_codes)
                and len(metric_codes) == len(set(metric_codes))
            ):
                errors.append(f"{label}.metricCodes必须是非空、不重复的字符串数组")
                metric_codes = []
        else:
            metric_codes = []
        for source_index, source in enumerate(sources):
            source_label = f"{label}.sourceRefs[{source_index}]"
            if not isinstance(source, dict):
                errors.append(f"{source_label}必须是对象")
                continue
            material_id = clean(source.get("materialId"))
            source_path = clean(source.get("relativePath")).replace("\\", "/")
            if not material_id or materials.get(material_id) != source_path:
                errors.append(f"{source_label}的materialId与relativePath未精确映射")
            if clean(source.get("role")) not in SOURCE_ROLES:
                errors.append(f"{source_label}.role无效")
            validate_locator(source, source_label, errors)
        published_metrics: dict[str, dict[str, Any]] = {}
        has_artifact_paths = False
        for calc_index, calculation in enumerate(calculations):
            calc_label = f"{label}.calculationRefs[{calc_index}]"
            if not isinstance(calculation, dict):
                errors.append(f"{calc_label}必须是对象")
                continue
            script_file = validate_workspace_file(
                workspace, calculation.get("scriptPath"), f"{calc_label}.scriptPath", errors
            )
            result_file = validate_workspace_file(
                workspace, calculation.get("resultPath"), f"{calc_label}.resultPath", errors
            )
            if script_file is not None:
                artifact_paths[script_file] = f"{calc_label}计算脚本"
            if result_file is not None:
                artifact_paths[result_file] = f"{calc_label}计算结果"
                for metric_code, metric in load_published_metrics(
                    result_file, f"{calc_label}计算结果", errors
                ).items():
                    if metric_code in published_metrics:
                        errors.append(f"{calc_label}重复发布指标：{metric_code}")
                    published_metrics[metric_code] = metric

            artifact_values = calculation.get("artifactPaths", [])
            if not isinstance(artifact_values, list) or any(
                not isinstance(item, str) or not item.strip() for item in artifact_values
            ):
                errors.append(f"{calc_label}.artifactPaths必须是字符串数组")
                artifact_values = []
            if artifact_values:
                has_artifact_paths = True
            for artifact_index, artifact_value in enumerate(artifact_values):
                artifact_file = validate_workspace_file(
                    workspace,
                    artifact_value,
                    f"{calc_label}.artifactPaths[{artifact_index}]",
                    errors,
                )
                if artifact_file is not None:
                    artifact_paths[artifact_file] = f"{calc_label}交付物"

        for metric_code in metric_codes:
            metric = published_metrics.get(metric_code)
            if metric is None:
                errors.append(f"{label}计算结果缺少指标：{metric_code}")
                continue
            validate_published_metric(
                metric,
                metric_code,
                label,
                statement,
                answer,
                has_artifact_paths,
                errors,
            )
    referenced = set(re.findall(r"\[([A-Z][A-Z0-9_-]{0,31})\]", answer))
    for code in sorted(referenced - seen):
        errors.append(f"最终回复引用了未交付的[{code}]")
    for path, label in artifact_paths.items():
        if path.is_file() and not path.is_symlink():
            artifact_material_identity_errors(path, label, materials, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--answer", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(args.workspace.resolve(), args.answer.resolve())
    if errors:
        print("CLAIM_DELIVERY_FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("CLAIM_DELIVERY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
