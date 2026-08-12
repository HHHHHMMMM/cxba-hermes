#!/usr/bin/env python3
"""Validate a CXBA claim-review package using only the Python standard library.

This is a structural and reference-existence precheck. It does not review
evidence content, reproduce calculations, or decide claim verdicts.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


CLAIM_TYPES = {
    "FACT",
    "CALCULATION",
    "TEMPORAL_ASSOCIATION",
    "BUSINESS_INFERENCE",
}
COVERAGE_VALUES = {"FULL", "SAMPLE", "UNKNOWN"}


@dataclass(frozen=True)
class Issue:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    claim_count: int
    reference_count: int
    issues: list[Issue]

    @property
    def ok(self) -> bool:
        return not self.issues


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _add_required_string(
    value: Any, location: str, issues: list[Issue]
) -> bool:
    if _is_non_empty_string(value):
        return True
    issues.append(Issue("REQUIRED_STRING", location, "must be a non-empty string"))
    return False


def _resolve_file(
    raw_path: Any,
    location: str,
    base_dir: Path,
    issues: list[Issue],
) -> bool:
    if not _add_required_string(raw_path, location, issues):
        return False
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        issues.append(Issue("REF_NOT_FOUND", location, f"file does not exist: {path}"))
        return False
    if not path.is_file():
        issues.append(Issue("REF_NOT_FILE", location, f"reference is not a file: {path}"))
        return False
    return True


def _validate_source_refs(
    refs: Any,
    location: str,
    base_dir: Path,
    issues: list[Issue],
    *,
    require_non_empty: bool = True,
) -> int:
    if not isinstance(refs, list):
        issues.append(Issue("REQUIRED_ARRAY", location, "must be an array"))
        return 0
    if require_non_empty and not refs:
        issues.append(Issue("EMPTY_SOURCE_REFS", location, "must contain at least one source reference"))

    checked = 0
    for index, ref in enumerate(refs):
        ref_location = f"{location}[{index}]"
        if not isinstance(ref, dict):
            issues.append(Issue("INVALID_OBJECT", ref_location, "must be an object"))
            continue
        if _resolve_file(ref.get("path"), f"{ref_location}.path", base_dir, issues):
            checked += 1
        _add_required_string(ref.get("locator"), f"{ref_location}.locator", issues)
    return checked


def _validate_calculation_refs(
    refs: Any,
    location: str,
    base_dir: Path,
    issues: list[Issue],
    *,
    required: bool,
) -> int:
    if not isinstance(refs, list):
        issues.append(Issue("REQUIRED_ARRAY", location, "must be an array"))
        return 0
    if required and not refs:
        issues.append(Issue("MISSING_CALCULATION_REF", location, "CALCULATION claim requires at least one calculation reference"))

    checked = 0
    for index, ref in enumerate(refs):
        ref_location = f"{location}[{index}]"
        if not isinstance(ref, dict):
            issues.append(Issue("INVALID_OBJECT", ref_location, "must be an object"))
            continue
        for field in ("script_path", "result_path"):
            if _resolve_file(ref.get(field), f"{ref_location}.{field}", base_dir, issues):
                checked += 1
        _add_required_string(ref.get("purpose"), f"{ref_location}.purpose", issues)
    return checked


def _validate_chain_hops(
    hops: Any,
    location: str,
    base_dir: Path,
    issues: list[Issue],
) -> int:
    if not isinstance(hops, list):
        issues.append(Issue("REQUIRED_ARRAY", location, "must be an array"))
        return 0

    checked = 0
    for index, hop in enumerate(hops):
        hop_location = f"{location}[{index}]"
        if not isinstance(hop, dict):
            issues.append(Issue("INVALID_OBJECT", hop_location, "must be an object"))
            continue
        for field in ("from_node", "to_node", "basis"):
            _add_required_string(hop.get(field), f"{hop_location}.{field}", issues)
        checked += _validate_source_refs(
            hop.get("source_refs"),
            f"{hop_location}.source_refs",
            base_dir,
            issues,
        )
    return checked


def _validate_limitations(
    limitations: Any,
    location: str,
    issues: list[Issue],
    *,
    required_non_empty: bool,
) -> None:
    if not isinstance(limitations, list):
        issues.append(Issue("REQUIRED_ARRAY", location, "must be an array"))
        return
    if required_non_empty and not limitations:
        issues.append(Issue("MISSING_LIMITATION", location, "must describe at least one limitation"))
    for index, limitation in enumerate(limitations):
        _add_required_string(limitation, f"{location}[{index}]", issues)


def validate_package(package: Any, base_dir: Path) -> ValidationResult:
    issues: list[Issue] = []
    reference_count = 0

    if not isinstance(package, dict):
        return ValidationResult(
            claim_count=0,
            reference_count=0,
            issues=[Issue("INVALID_ROOT", "$", "package must be a JSON object")],
        )

    if _resolve_file(package.get("report_path"), "$.report_path", base_dir, issues):
        reference_count += 1

    claims = package.get("claims")
    if not isinstance(claims, list):
        issues.append(Issue("REQUIRED_ARRAY", "$.claims", "must be an array"))
        return ValidationResult(0, reference_count, issues)
    if not claims:
        issues.append(Issue("EMPTY_CLAIMS", "$.claims", "must contain at least one claim"))

    seen_ids: set[str] = set()
    for index, claim in enumerate(claims):
        location = f"$.claims[{index}]"
        if not isinstance(claim, dict):
            issues.append(Issue("INVALID_OBJECT", location, "must be an object"))
            continue

        claim_id = claim.get("id")
        if _add_required_string(claim_id, f"{location}.id", issues):
            normalized_id = claim_id.strip()
            if normalized_id in seen_ids:
                issues.append(Issue("DUPLICATE_CLAIM_ID", f"{location}.id", f"duplicate claim id: {normalized_id}"))
            seen_ids.add(normalized_id)

        _add_required_string(claim.get("statement"), f"{location}.statement", issues)
        _add_required_string(claim.get("user_basis"), f"{location}.user_basis", issues)

        claim_type = claim.get("type")
        if claim_type not in CLAIM_TYPES:
            issues.append(
                Issue(
                    "INVALID_CLAIM_TYPE",
                    f"{location}.type",
                    f"must be one of: {', '.join(sorted(CLAIM_TYPES))}",
                )
            )

        coverage = claim.get("coverage")
        if coverage not in COVERAGE_VALUES:
            issues.append(
                Issue(
                    "INVALID_COVERAGE",
                    f"{location}.coverage",
                    f"must be one of: {', '.join(sorted(COVERAGE_VALUES))}",
                )
            )

        reference_count += _validate_source_refs(
            claim.get("source_refs"),
            f"{location}.source_refs",
            base_dir,
            issues,
        )
        reference_count += _validate_calculation_refs(
            claim.get("calculation_refs"),
            f"{location}.calculation_refs",
            base_dir,
            issues,
            required=claim_type == "CALCULATION",
        )
        reference_count += _validate_chain_hops(
            claim.get("chain_hops"),
            f"{location}.chain_hops",
            base_dir,
            issues,
        )
        _validate_limitations(
            claim.get("limitations"),
            f"{location}.limitations",
            issues,
            required_non_empty=(
                coverage in {"SAMPLE", "UNKNOWN"}
                or claim_type == "BUSINESS_INFERENCE"
            ),
        )

    return ValidationResult(len(claims), reference_count, issues)


def load_and_validate(manifest_path: Path) -> ValidationResult:
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            package = json.load(handle)
    except FileNotFoundError:
        return ValidationResult(0, 0, [Issue("PACKAGE_NOT_FOUND", "$", f"file does not exist: {manifest_path}")])
    except IsADirectoryError:
        return ValidationResult(0, 0, [Issue("PACKAGE_NOT_FILE", "$", f"path is not a file: {manifest_path}")])
    except UnicodeDecodeError as exc:
        return ValidationResult(0, 0, [Issue("INVALID_ENCODING", "$", f"package must be UTF-8: {exc}")])
    except json.JSONDecodeError as exc:
        return ValidationResult(0, 0, [Issue("INVALID_JSON", "$", f"line {exc.lineno}, column {exc.colno}: {exc.msg}")])
    except OSError as exc:
        return ValidationResult(0, 0, [Issue("PACKAGE_READ_FAILED", "$", str(exc))])

    return validate_package(package, manifest_path.resolve().parent)


def _self_test() -> int:
    checks = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for relative_path in ("report.md", "source.csv", "calculate.py", "result.json"):
            (root / relative_path).write_text("test\n", encoding="utf-8")

        valid_package = {
            "report_path": "report.md",
            "claims": [
                {
                    "id": "C001",
                    "statement": "A reproducible aggregate",
                    "type": "CALCULATION",
                    "user_basis": "Declared scope",
                    "coverage": "FULL",
                    "source_refs": [{"path": "source.csv", "locator": "rows 2-4"}],
                    "calculation_refs": [
                        {
                            "script_path": "calculate.py",
                            "result_path": "result.json",
                            "purpose": "aggregate",
                        }
                    ],
                    "chain_hops": [],
                    "limitations": [],
                }
            ],
        }

        manifest_path = root / "claim-review.json"
        manifest_path.write_text(json.dumps(valid_package), encoding="utf-8")
        result = load_and_validate(manifest_path)
        assert result.ok, result.issues
        assert result.claim_count == 1
        assert result.reference_count == 4
        checks += 1

        broken = json.loads(json.dumps(valid_package))
        broken["claims"][0]["source_refs"][0]["path"] = "missing.csv"
        result = validate_package(broken, root)
        assert any(issue.code == "REF_NOT_FOUND" for issue in result.issues)
        checks += 1

        duplicate = json.loads(json.dumps(valid_package))
        duplicate["claims"].append(json.loads(json.dumps(duplicate["claims"][0])))
        result = validate_package(duplicate, root)
        assert any(issue.code == "DUPLICATE_CLAIM_ID" for issue in result.issues)
        checks += 1

        missing_calculation = json.loads(json.dumps(valid_package))
        missing_calculation["claims"][0]["calculation_refs"] = []
        result = validate_package(missing_calculation, root)
        assert any(issue.code == "MISSING_CALCULATION_REF" for issue in result.issues)
        checks += 1

        unbounded_sample = json.loads(json.dumps(valid_package))
        unbounded_sample["claims"][0]["coverage"] = "SAMPLE"
        result = validate_package(unbounded_sample, root)
        assert any(issue.code == "MISSING_LIMITATION" for issue in result.issues)
        checks += 1

    print(f"SELF-TEST: PASS ({checks} checks)")
    return 0


def _print_human(result: ValidationResult) -> None:
    if result.ok:
        print(
            f"VALID: {result.claim_count} claim(s), "
            f"{result.reference_count} referenced file(s) checked"
        )
        print("NOTE: structural success does not establish evidentiary correctness.")
        return
    print(f"INVALID: {len(result.issues)} issue(s)")
    for issue in result.issues:
        print(f"- [{issue.code}] {issue.location}: {issue.message}")


def _print_json(result: ValidationResult) -> None:
    payload = {
        "ok": result.ok,
        "claim_count": result.claim_count,
        "reference_count": result.reference_count,
        "issues": [asdict(issue) for issue in result.issues],
        "notice": "Structure and file existence only; independent evidence review is still required.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check claim-review package structure and referenced file existence."
    )
    parser.add_argument("package", nargs="?", type=Path, help="path to claim-review.json")
    parser.add_argument("--json", action="store_true", dest="json_output", help="print JSON result")
    parser.add_argument("--self-test", action="store_true", help="run built-in tests")
    args = parser.parse_args(argv)

    if args.self_test:
        if args.package is not None:
            parser.error("package cannot be used with --self-test")
        return _self_test()
    if args.package is None:
        parser.error("package is required unless --self-test is used")

    result = load_and_validate(args.package)
    if args.json_output:
        _print_json(result)
    else:
        _print_human(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
