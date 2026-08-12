"""Deliver CXBA claim evidence from the current Run workspace.

The agent writes one bounded JSON result.  The Gateway validates references
against the trusted Spring binding and Run mounts before forwarding them.  It
does not infer evidence from assistant prose or copy any source file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


CLAIM_RESULT_PATH = PurePosixPath("/workspace/evidence-items/final-claims.json")
_CLAIM_CODE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
_ANSWER_CLAIM_CODE = re.compile(r"\[([A-Z][A-Z0-9_-]{0,31})\]")
_CLAIM_TYPES = frozenset(
    {"FACT", "CALCULATION", "RELATION", "FINDING", "HYPOTHESIS", "GAP"}
)
_COVERAGE = frozenset({"FULL", "PARTIAL", "NONE"})
_SOURCE_ROLES = frozenset({"SUPPORT", "COUNTER"})
_LOCATOR_TYPES = frozenset(
    {
        "EXCEL_RANGE",
        "CSV_LINES",
        "PARQUET_ROWS",
        "DUCKDB_ROWS",
        "PDF_PAGE",
        "WORD_ANCHOR",
        "IMAGE_REGION",
        "NEAREST",
    }
)


class ClaimDeliveryError(ValueError):
    """A final claim result cannot be delivered as verified evidence."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _workspace_root(run: Any) -> Path | None:
    for mount in run.context.mounts:
        if mount.target == "/workspace" and not mount.read_only:
            return Path(mount.source).resolve(strict=True)
    return None


def _data_root(run: Any) -> Path | None:
    for mount in run.context.mounts:
        if mount.target == "/data" and mount.read_only:
            return Path(mount.source).resolve(strict=True)
    return None


def _safe_relative_path(value: Any, *, code: str) -> PurePosixPath:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ClaimDeliveryError(code)
    return path


def _existing_regular_file(root: Path, relative: PurePosixPath, *, code: str) -> None:
    candidate = root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ClaimDeliveryError(code) from exc
    if not resolved.is_file():
        raise ClaimDeliveryError(code)


def prepare_claim_delivery(run: Any) -> None:
    """Remove the previous turn's bounded result before a new turn starts."""
    root = _workspace_root(run)
    if root is None:
        return
    result = root.joinpath(*CLAIM_RESULT_PATH.relative_to("/workspace").parts)
    if result.is_symlink():
        raise ClaimDeliveryError("claim_result_symlink")
    if result.exists():
        if not result.is_file():
            raise ClaimDeliveryError("claim_result_not_file")
        result.unlink()


def _trusted_materials(run: Any) -> dict[str, PurePosixPath]:
    case_context = getattr(run, "case_context", None)
    catalog = case_context.get("material_catalog") if isinstance(case_context, dict) else None
    if not isinstance(catalog, list):
        raise ClaimDeliveryError("trusted_material_catalog_missing")
    materials: dict[str, PurePosixPath] = {}
    for item in catalog:
        if not isinstance(item, dict):
            continue
        if item.get("recycled") is True:
            continue
        material_id = str(item.get("materialId") or item.get("material_id") or "").strip()
        if not material_id:
            continue
        relative = _safe_relative_path(
            item.get("relativePath") or item.get("relative_path"),
            code="trusted_material_path_invalid",
        )
        materials[material_id] = relative
    data_root = _data_root(run)
    if materials and data_root is None:
        raise ClaimDeliveryError("trusted_data_mount_missing")
    return materials


def _positive_int(value: Any, *, code: str) -> int:
    if isinstance(value, bool):
        raise ClaimDeliveryError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ClaimDeliveryError(code) from exc
    if parsed < 1:
        raise ClaimDeliveryError(code)
    return parsed


def _exact_positions(locator: dict[str, Any], name: str, *, range_names: tuple[str, str], code: str) -> None:
    values = locator.get(name)
    if not isinstance(values, list) or not values or len(values) > 1_000:
        raise ClaimDeliveryError(code)
    if any(range_name in locator for range_name in range_names):
        raise ClaimDeliveryError(code)
    previous = 0
    for value in values:
        current = _positive_int(value, code=code)
        if current <= previous:
            raise ClaimDeliveryError(code)
        previous = current


def _validate_locator(locator_type: str, locator: Any) -> dict[str, Any]:
    if locator_type not in _LOCATOR_TYPES or not isinstance(locator, dict):
        raise ClaimDeliveryError("source_locator_invalid")
    normalized = dict(locator)
    if locator_type == "EXCEL_RANGE":
        if not str(locator.get("sheet") or "").strip():
            raise ClaimDeliveryError("excel_sheet_missing")
        if "rows" in locator:
            _exact_positions(locator, "rows", range_names=("startRow", "endRow"), code="excel_row_invalid")
        else:
            start = _positive_int(locator.get("startRow"), code="excel_row_invalid")
            end = _positive_int(locator.get("endRow"), code="excel_row_invalid")
            if end < start:
                raise ClaimDeliveryError("excel_row_invalid")
    elif locator_type == "CSV_LINES":
        if "lines" in locator:
            _exact_positions(locator, "lines", range_names=("startLine", "endLine"), code="csv_line_invalid")
        else:
            start = _positive_int(locator.get("startLine"), code="csv_line_invalid")
            end = _positive_int(locator.get("endLine"), code="csv_line_invalid")
            if end < start:
                raise ClaimDeliveryError("csv_line_invalid")
    elif locator_type in {"PARQUET_ROWS", "DUCKDB_ROWS"}:
        if not str(locator.get("table") or "").strip():
            raise ClaimDeliveryError("table_locator_missing")
        if "rows" in locator:
            _exact_positions(locator, "rows", range_names=("startRow", "endRow"), code="table_row_invalid")
        else:
            start = _positive_int(locator.get("startRow"), code="table_row_invalid")
            end = _positive_int(locator.get("endRow"), code="table_row_invalid")
            if end < start:
                raise ClaimDeliveryError("table_row_invalid")
    elif locator_type in {"PDF_PAGE", "IMAGE_REGION"}:
        _positive_int(locator.get("page"), code="page_locator_invalid")
        bbox = locator.get("bbox")
        if bbox is not None and (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(not isinstance(item, (int, float)) for item in bbox)
        ):
            raise ClaimDeliveryError("bbox_locator_invalid")
    elif locator_type in {"WORD_ANCHOR", "NEAREST"}:
        if not any(str(locator.get(key) or "").strip() for key in ("heading", "paragraph", "table", "anchor")):
            raise ClaimDeliveryError("text_anchor_missing")
    return normalized


def _required_text(value: Any, *, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClaimDeliveryError(code)
    return text


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any, *, code: str) -> list[str]:
    if not isinstance(value, list):
        raise ClaimDeliveryError(code)
    return [_required_text(item, code=code) for item in value]


def read_claim_delivery(run: Any, answer: str) -> dict[str, Any]:
    """Return a verified payload or an explicit non-verified status."""
    root = _workspace_root(run)
    if root is None:
        return {"evidence_status": "INVALID", "claims": [], "evidence_errors": ["workspace_mount_missing"]}
    result = root.joinpath(*CLAIM_RESULT_PATH.relative_to("/workspace").parts)
    if not result.exists():
        return {"evidence_status": "NOT_PROVIDED", "claims": [], "evidence_errors": []}
    try:
        if result.is_symlink() or not result.is_file():
            raise ClaimDeliveryError("claim_result_not_regular_file")
        if result.stat().st_size > 1024 * 1024:
            raise ClaimDeliveryError("claim_result_too_large")
        raw = json.loads(result.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"claims"} or not isinstance(raw["claims"], list):
            raise ClaimDeliveryError("claim_result_shape_invalid")
        trusted_materials = _trusted_materials(run)
        data_root = _data_root(run)
        claims: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw["claims"]:
            if not isinstance(item, dict):
                raise ClaimDeliveryError("claim_shape_invalid")
            code = _required_text(item.get("claimCode"), code="claim_code_missing")
            if not _CLAIM_CODE.fullmatch(code) or code in seen:
                raise ClaimDeliveryError("claim_code_invalid")
            seen.add(code)
            if f"[{code}]" not in answer:
                raise ClaimDeliveryError("claim_not_referenced_by_answer")
            claim_type = _required_text(item.get("claimType"), code="claim_type_missing")
            coverage = _required_text(item.get("coverage"), code="claim_coverage_missing")
            if claim_type not in _CLAIM_TYPES:
                raise ClaimDeliveryError("claim_type_invalid")
            if coverage not in _COVERAGE:
                raise ClaimDeliveryError("claim_coverage_invalid")
            raw_sources = item.get("sourceRefs")
            raw_calculations = item.get("calculationRefs")
            if not isinstance(raw_sources, list) or not isinstance(raw_calculations, list):
                raise ClaimDeliveryError("claim_references_invalid")
            if claim_type not in {"HYPOTHESIS", "GAP"} and not raw_sources:
                raise ClaimDeliveryError("claim_source_missing")
            if claim_type == "CALCULATION" and not raw_calculations:
                raise ClaimDeliveryError("calculation_reference_missing")
            sources = []
            for source in raw_sources:
                if not isinstance(source, dict):
                    raise ClaimDeliveryError("source_reference_invalid")
                material_id = _required_text(source.get("materialId"), code="source_material_missing")
                relative = trusted_materials.get(material_id)
                if relative is None or data_root is None:
                    raise ClaimDeliveryError("source_material_untrusted")
                _existing_regular_file(data_root, relative, code="source_material_file_missing")
                role = _required_text(source.get("role"), code="source_role_missing")
                if role not in _SOURCE_ROLES:
                    raise ClaimDeliveryError("source_role_invalid")
                locator_type = _required_text(source.get("locatorType"), code="source_locator_missing")
                sources.append(
                    {
                        "materialId": material_id,
                        "role": role,
                        "locatorType": locator_type,
                        "locator": _validate_locator(locator_type, source.get("locator")),
                        "description": _required_text(source.get("description"), code="source_description_missing"),
                    }
                )
            calculations = []
            for calculation in raw_calculations:
                if not isinstance(calculation, dict):
                    raise ClaimDeliveryError("calculation_reference_invalid")
                script = _safe_relative_path(calculation.get("scriptPath"), code="calculation_script_path_invalid")
                result_path = _safe_relative_path(calculation.get("resultPath"), code="calculation_result_path_invalid")
                _existing_regular_file(root, script, code="calculation_script_missing")
                _existing_regular_file(root, result_path, code="calculation_result_missing")
                calculations.append(
                    {
                        "scriptPath": str(script),
                        "resultPath": str(result_path),
                        "purpose": _required_text(calculation.get("purpose"), code="calculation_purpose_missing"),
                        "calculationBasis": _required_text(calculation.get("calculationBasis"), code="calculation_basis_missing"),
                    }
                )
            claims.append(
                {
                    "claimCode": code,
                    "statement": _required_text(item.get("statement"), code="claim_statement_missing"),
                    "claimType": claim_type,
                    "coverage": coverage,
                    "userBasis": _optional_text(item.get("userBasis")),
                    "supportSummary": _optional_text(item.get("supportSummary")),
                    "counterSummary": _optional_text(item.get("counterSummary")),
                    "limitations": _string_list(item.get("limitations", []), code="claim_limitations_invalid"),
                    "sourceRefs": sources,
                    "calculationRefs": calculations,
                }
            )
        if not set(_ANSWER_CLAIM_CODE.findall(answer)).issubset(seen):
            raise ClaimDeliveryError("answer_claim_reference_missing")
        return {"evidence_status": "VERIFIED", "claims": claims, "evidence_errors": []}
    except (OSError, UnicodeError, json.JSONDecodeError, ClaimDeliveryError) as exc:
        code = exc.code if isinstance(exc, ClaimDeliveryError) else "claim_result_unreadable"
        return {"evidence_status": "INVALID", "claims": [], "evidence_errors": [code]}
