from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "profiles"
    / "cxba-production"
    / "skills"
    / "cxba"
    / "cxba-safe-tabular-analysis"
    / "scripts"
)


def run_script(name: str, *args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
    return result


def test_inventory_uses_stable_file_ids_and_relative_paths(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "第二批").mkdir(parents=True)
    (data / "第一批.csv").write_text("账户,金额\nA,1\n", encoding="utf-8")
    (data / "第二批" / "流水.csv").write_text("账户,金额\nB,2\n", encoding="utf-8")
    output = tmp_path / "workspace" / "inventory.json"

    run_script(
        "case_material_inventory.py",
        "--root",
        str(data),
        "--output",
        str(output),
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["fileCount"] == 2
    assert [item["fileId"] for item in result["files"]] == ["F0001", "F0002"]
    assert [item["path"] for item in result["files"]] == ["第一批.csv", "第二批/流水.csv"]


def test_catalog_reads_chinese_filename_and_counts_all_rows(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "账户流水（第一批）.csv"
    source.write_text("账户,金额\nacct-A,12.34\nacct-B,56.78\n", encoding="utf-8")
    output = tmp_path / "workspace" / "tabular.json"

    run_script(
        "case_file_profiler.py",
        "catalog",
        "--root",
        str(data),
        "--output",
        str(output),
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    file_result = result["files"][0]
    sheet = file_result["sheets"][0]
    assert file_result["file"] == source.name
    assert sheet["rows"] == 3
    assert sheet["headerRow"] == 1
    assert sheet["headers"] == ["账户", "金额"]


def test_inspect_reads_complete_large_csv_with_bounded_samples(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "large.csv"
    with source.open("w", encoding="utf-8") as handle:
        handle.write("序号,金额\n")
        for index in range(20_000):
            handle.write(f"{index},{index}.01\n")
    output = tmp_path / "workspace" / "profile.json"

    run_script(
        "case_file_profiler.py",
        "inspect",
        "--root",
        str(data),
        "--file",
        source.name,
        "--output",
        str(output),
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["rows"] == 20_001
    assert result["dataRows"] == 20_000
    assert len(result["sampleRows"]) == 5


def test_search_returns_exact_locations_without_copying_values(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "流水.csv"
    source.write_text(
        "账户,摘要\nA,普通交易\nB,专项核查词\nC,普通交易\nD,专项核查词\n",
        encoding="utf-8",
    )
    output = tmp_path / "workspace" / "matches.json"

    run_script(
        "case_file_profiler.py",
        "search",
        "--root",
        str(data),
        "--term",
        "专项核查词",
        "--output",
        str(output),
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert [(item["row"], item["column"]) for item in result["matches"]] == [(3, 2), (5, 2)]
    assert all(item["file"] == source.name for item in result["matches"])
    assert all("value" not in item for item in result["matches"])


def test_inspect_rejects_path_escape(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("账户,金额\nA,1\n", encoding="utf-8")

    result = run_script(
        "case_file_profiler.py",
        "inspect",
        "--root",
        str(data),
        "--file",
        "../outside.csv",
        "--output",
        str(tmp_path / "workspace" / "profile.json"),
        expect_success=False,
    )

    assert "FILE_OUTSIDE_SOURCE_ROOT" in result.stderr


def test_output_must_stay_outside_source_root(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "流水.csv").write_text("账户,金额\nA,1\n", encoding="utf-8")

    result = run_script(
        "case_file_profiler.py",
        "catalog",
        "--root",
        str(data),
        "--output",
        str(data / "catalog.json"),
        expect_success=False,
    )

    assert "OUTPUT_MUST_BE_OUTSIDE_SOURCE_ROOT" in result.stderr


def test_search_rejects_out_of_range_match_limit(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "流水.csv").write_text("账户,金额\nA,1\n", encoding="utf-8")

    result = run_script(
        "case_file_profiler.py",
        "search",
        "--root",
        str(data),
        "--term",
        "A",
        "--max-matches",
        "0",
        "--output",
        str(tmp_path / "workspace" / "matches.json"),
        expect_success=False,
    )

    assert "MAX_MATCHES_OUT_OF_RANGE" in result.stderr


def test_build_material_catalog_joins_inventory_and_profiles(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "流水.csv").write_text("账户,金额\nA,1\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    inventory = workspace / "inventory.json"
    tabular = workspace / "tabular.json"
    documents = workspace / "documents.json"
    output = workspace / "materials.json"

    run_script(
        "case_material_inventory.py",
        "--root",
        str(data),
        "--output",
        str(inventory),
    )
    run_script(
        "case_file_profiler.py",
        "catalog",
        "--root",
        str(data),
        "--output",
        str(tabular),
    )
    documents.write_text(json.dumps({"files": []}), encoding="utf-8")
    run_script(
        "build_material_catalog.py",
        "--inventory",
        str(inventory),
        "--tabular",
        str(tabular),
        "--documents",
        str(documents),
        "--output",
        str(output),
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["fileCount"] == 1
    assert result["tabularFileCount"] == 1
    assert result["materials"][0]["contentStatus"] == "tabular_profiled"
    assert (workspace / "material-summary.json").exists()
