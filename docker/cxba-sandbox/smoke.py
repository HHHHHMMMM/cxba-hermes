#!/usr/bin/env python3
"""Offline smoke check for the CXBA analysis image and synthetic fixture."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


MODULES = (
    "PIL",
    "duckdb",
    "docx",
    "matplotlib",
    "numpy",
    "odf",
    "openpyxl",
    "pandas",
    "pdfplumber",
    "plotly",
    "pptx",
    "pyarrow",
    "pypdf",
    "pytesseract",
    "pyxlsb",
    "pymupdf",
    "seaborn",
    "striprtf",
    "xlrd",
    "xlsxwriter",
)

BINARIES = (
    "7z",
    "antiword",
    "catdoc",
    "file",
    "jq",
    "libreoffice",
    "node",
    "pdftotext",
    "tesseract",
    "unzip",
)

FORBIDDEN_INSTALLERS = ("apt", "apt-get", "npm", "npx", "pip", "pip3")


def run_script(name: str, *arguments: str) -> None:
    subprocess.run(
        [sys.executable, f"/opt/cxba/scripts/{name}", *arguments],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    os.environ.setdefault("XDG_CACHE_HOME", str(args.workspace / ".cache"))
    for directory in (
        "input",
        "catalog",
        "notes",
        "evidence",
        "scripts",
        "intermediate",
        "results",
        "review",
        "reports",
    ):
        (args.workspace / directory).mkdir(parents=True, exist_ok=True)

    missing_modules: list[str] = []
    for module in MODULES:
        try:
            importlib.import_module(module)
        except ImportError:
            missing_modules.append(module)
    missing_binaries = [binary for binary in BINARIES if shutil.which(binary) is None]
    present_installers = [binary for binary in FORBIDDEN_INSTALLERS if shutil.which(binary)]
    pip_module_present = importlib.util.find_spec("pip") is not None
    if missing_modules or missing_binaries or present_installers or pip_module_present:
        raise SystemExit(
            f"cxba_sandbox_smoke_failed missingModules={missing_modules} "
            f"missingBinaries={missing_binaries} presentInstallers={present_installers} "
            f"pipModulePresent={pip_module_present}"
        )

    catalog = args.workspace / "catalog" / "materials.json"
    profile = args.workspace / "notes" / "table-profile.json"
    accounts = args.workspace / "results" / "accounts.jsonl"
    amounts = args.workspace / "results" / "amounts.json"
    document = args.workspace / "notes" / "synthetic-document.txt"
    run_script(
        "material_catalog.py",
        "--catalog",
        str(args.fixture / "spring-materials.json"),
        "--data-root",
        str(args.fixture / "data"),
        "--output",
        str(catalog),
    )
    run_script(
        "tabular_profile.py",
        "--catalog",
        str(catalog),
        "--material-id",
        "synthetic-table",
        "--output",
        str(profile),
    )
    run_script(
        "enumerate_accounts.py",
        "--catalog",
        str(catalog),
        "--material-id",
        "synthetic-table",
        "--account",
        "BANK_ACCOUNT=account",
        "--name-column",
        "name",
        "--output",
        str(accounts),
    )
    run_script(
        "decimal_summary.py",
        "--catalog",
        str(catalog),
        "--material-id",
        "synthetic-table",
        "--amount-column",
        "amount",
        "--output",
        str(amounts),
    )
    run_script(
        "document_extract.py",
        "--catalog",
        str(catalog),
        "--material-id",
        "synthetic-document",
        "--output-dir",
        str(document.parent),
    )

    amount_payload = json.loads(amounts.read_text(encoding="utf-8"))
    account_payload = accounts.read_text(encoding="utf-8").splitlines()
    if amount_payload["amount"] != "999999999999.31" or len(account_payload) != 2:
        raise SystemExit("cxba_sandbox_smoke_failed unexpected synthetic results")
    if "Synthetic structure sample" not in document.read_text(encoding="utf-8"):
        raise SystemExit("cxba_sandbox_smoke_failed document extraction mismatch")
    print("cxba_sandbox_smoke_passed network=none fixture=synthetic")


if __name__ == "__main__":
    main()
