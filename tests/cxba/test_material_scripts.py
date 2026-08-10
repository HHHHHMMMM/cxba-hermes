from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "profiles"
    / "cxba-production"
    / "skills"
    / "cxba"
    / "cxba-material-profiling"
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


@pytest.fixture
def material_fixture(tmp_path: Path) -> dict[str, Path]:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "transactions.csv"
    source.write_text(
        "account,name,bank,amount,category\n"
        "acct-A,Alpha,Bank One,0.10,one\n"
        "ACCT-A,Alpha Alternate,Bank Two,0.20,one\n"
        "acct-B,Beta,Bank Three,999999999999.01,two\n"
        "acct-C,Gamma,Bank Four,invalid,two\n"
        ",Missing,,,three\n",
        encoding="utf-8",
    )
    spring_catalog = tmp_path / "spring-materials.json"
    spring_catalog.write_text(
        json.dumps(
            {
                "materials": [
                    {
                        "materialId": "material-stable-1",
                        "relativePath": "transactions.csv",
                        "displayName": "transactions.csv",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    normalized = tmp_path / "workspace" / "catalog" / "materials.json"
    run_script(
        "material_catalog.py",
        "--catalog",
        str(spring_catalog),
        "--data-root",
        str(data),
        "--output",
        str(normalized),
    )
    return {
        "data": data,
        "source": source,
        "spring_catalog": spring_catalog,
        "normalized": normalized,
        "workspace": tmp_path / "workspace",
    }


def test_catalog_uses_spring_id_and_survives_a_move(material_fixture: dict[str, Path]) -> None:
    before = json.loads(material_fixture["normalized"].read_text(encoding="utf-8"))
    assert before["materials"][0]["materialId"] == "material-stable-1"

    moved = material_fixture["data"] / "moved" / "transactions.csv"
    moved.parent.mkdir()
    material_fixture["source"].rename(moved)
    material_fixture["spring_catalog"].write_text(
        json.dumps(
            {
                "materials": [
                    {
                        "materialId": "material-stable-1",
                        "relativePath": "moved/transactions.csv",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    run_script(
        "material_catalog.py",
        "--catalog",
        str(material_fixture["spring_catalog"]),
        "--data-root",
        str(material_fixture["data"]),
        "--output",
        str(material_fixture["normalized"]),
    )
    after = json.loads(material_fixture["normalized"].read_text(encoding="utf-8"))
    assert after["materials"][0]["materialId"] == "material-stable-1"
    assert after["materials"][0]["relativePath"] == "moved/transactions.csv"


def test_profile_reads_chinese_material_filename(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "账户流水（第一批）.csv"
    source.write_text("账户,金额\nacct-A,12.34\n", encoding="utf-8")
    spring_catalog = tmp_path / "spring-materials.json"
    spring_catalog.write_text(
        json.dumps(
            {
                "materials": [
                    {
                        "materialId": "material-chinese-path",
                        "relativePath": source.name,
                        "displayName": source.name,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    normalized = tmp_path / "catalog.json"
    profile = tmp_path / "profile.json"

    run_script(
        "material_catalog.py",
        "--catalog",
        str(spring_catalog),
        "--data-root",
        str(data),
        "--output",
        str(normalized),
    )
    run_script(
        "tabular_profile.py",
        "--catalog",
        str(normalized),
        "--material-id",
        "material-chinese-path",
        "--output",
        str(profile),
    )

    catalog = json.loads(normalized.read_text(encoding="utf-8"))
    result = json.loads(profile.read_text(encoding="utf-8"))["tables"][0]
    assert catalog["materials"][0]["relativePath"] == source.name
    assert result["rowCount"] == 1
    assert result["headers"] == ["账户", "金额"]


def test_account_enumeration_reads_complete_range(material_fixture: dict[str, Path]) -> None:
    output = material_fixture["workspace"] / "results" / "accounts.jsonl"
    run_script(
        "enumerate_accounts.py",
        "--catalog",
        str(material_fixture["normalized"]),
        "--material-id",
        "material-stable-1",
        "--account",
        "BANK_ACCOUNT=account",
        "--name-column",
        "name",
        "--bank-column",
        "bank",
        "--output",
        str(output),
    )
    accounts = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [item["accountId"] for item in accounts] == ["acct-A", "acct-B", "acct-C"]
    assert accounts[0]["occurrenceCount"] == 2
    assert accounts[0]["accountNameCandidates"] == ["Alpha", "Alpha Alternate"]
    assert accounts[0]["bankNameCandidates"] == ["Bank One", "Bank Two"]
    assert accounts[0]["accountName"] is None
    assert accounts[0]["bankName"] is None
    assert accounts[0]["accountNameConflict"] is True
    assert accounts[0]["bankNameConflict"] is True
    assert accounts[1]["accountName"] == "Beta"
    assert all(item["materialId"] == "material-stable-1" for item in accounts)
    assert not list(output.parent.glob(".account-enumeration.*.sqlite"))


def test_account_enumeration_rejects_unknown_type(material_fixture: dict[str, Path]) -> None:
    result = run_script(
        "enumerate_accounts.py",
        "--catalog",
        str(material_fixture["normalized"]),
        "--material-id",
        "material-stable-1",
        "--account",
        "UNKNOWN=account",
        "--output",
        str(material_fixture["workspace"] / "accounts.jsonl"),
        expect_success=False,
    )
    assert "BANK_ACCOUNT" in result.stderr


def test_decimal_summary_is_exact_and_counts_invalid_values(material_fixture: dict[str, Path]) -> None:
    output = material_fixture["workspace"] / "results" / "amounts.json"
    run_script(
        "decimal_summary.py",
        "--catalog",
        str(material_fixture["normalized"]),
        "--material-id",
        "material-stable-1",
        "--amount-column",
        "amount",
        "--group-by",
        "category",
        "--output",
        str(output),
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["amount"] == "999999999999.31"
    assert result["validAmountCount"] == 3
    assert result["invalidAmountCount"] == 1
    assert result["nullAmountCount"] == 1
    assert result["groups"][0]["amount"] == "0.30"


def test_decimal_summary_bounds_distinct_groups(material_fixture: dict[str, Path]) -> None:
    result = run_script(
        "decimal_summary.py",
        "--catalog",
        str(material_fixture["normalized"]),
        "--material-id",
        "material-stable-1",
        "--amount-column",
        "amount",
        "--group-by",
        "category",
        "--max-groups",
        "1",
        "--output",
        str(material_fixture["workspace"] / "results" / "bounded.json"),
        expect_success=False,
    )
    assert "exceeded max-groups" in result.stderr


def test_profile_streams_all_rows_with_bounded_samples(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    source = data / "large.csv"
    with source.open("w", encoding="utf-8") as handle:
        handle.write("row,value\n")
        for index in range(20000):
            handle.write(f"{index},{index}.01\n")
    spring_catalog = tmp_path / "spring.json"
    spring_catalog.write_text(
        json.dumps({"materials": [{"materialId": "large-material", "relativePath": "large.csv"}]}),
        encoding="utf-8",
    )
    normalized = tmp_path / "catalog.json"
    profile = tmp_path / "profile.json"
    run_script(
        "material_catalog.py",
        "--catalog",
        str(spring_catalog),
        "--data-root",
        str(data),
        "--output",
        str(normalized),
    )
    run_script(
        "tabular_profile.py",
        "--catalog",
        str(normalized),
        "--material-id",
        "large-material",
        "--sample-rows",
        "3",
        "--output",
        str(profile),
    )
    result = json.loads(profile.read_text(encoding="utf-8"))["tables"][0]
    assert result["rowCount"] == 20000
    assert len(result["firstRows"]) == 3
    assert len(result["lastRows"]) == 3


def test_catalog_rejects_path_escape(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("value\n1\n", encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps({"materials": [{"materialId": "escape", "relativePath": "../outside.csv"}]}),
        encoding="utf-8",
    )
    result = run_script(
        "material_catalog.py",
        "--catalog",
        str(catalog),
        "--data-root",
        str(data),
        "--output",
        str(tmp_path / "output.json"),
        expect_success=False,
    )
    assert "below the data root" in result.stderr
