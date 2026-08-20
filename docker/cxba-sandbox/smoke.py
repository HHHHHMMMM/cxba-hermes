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
    "cairosvg",
    "duckdb",
    "docx",
    "ebooklib",
    "matplotlib",
    "mammoth",
    "markdownify",
    "nbconvert",
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

    inventory = args.workspace / "catalog" / "inventory.json"
    tabular = args.workspace / "notes" / "tabular.json"
    documents = args.workspace / "notes" / "documents.json"
    catalog = args.workspace / "catalog" / "materials.json"
    extracted_documents = args.workspace / "intermediate" / "documents"
    run_script(
        "case_material_inventory.py",
        "--root",
        str(args.fixture / "data"),
        "--output",
        str(inventory),
    )
    run_script(
        "case_file_profiler.py",
        "catalog",
        "--root",
        str(args.fixture / "data"),
        "--output",
        str(tabular),
    )
    run_script(
        "case_document_profiler.py",
        "--root",
        str(args.fixture / "data"),
        "--output",
        str(documents),
        "--extracted-dir",
        str(extracted_documents),
    )
    run_script(
        "build_material_catalog.py",
        "--inventory",
        str(inventory),
        "--tabular",
        str(tabular),
        "--documents",
        str(documents),
        "--output",
        str(catalog),
    )

    catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
    summary_payload = json.loads(
        (catalog.parent / "material-summary.json").read_text(encoding="utf-8")
    )
    document_payload = json.loads(documents.read_text(encoding="utf-8"))
    statuses = {item.get("contentStatus") for item in catalog_payload.get("materials", [])}
    if (
        catalog_payload.get("fileCount") != 2
        or catalog_payload.get("tabularFileCount") != 1
        or catalog_payload.get("documentFileCount") != 1
        or summary_payload.get("uncoveredFileCount") != 0
        or statuses != {"complete", "tabular_profiled"}
    ):
        raise SystemExit("cxba_sandbox_smoke_failed unexpected synthetic results")
    extracted_path = Path(document_payload["files"][0]["extractedText"])
    if "Synthetic structure sample" not in extracted_path.read_text(encoding="utf-8"):
        raise SystemExit("cxba_sandbox_smoke_failed document extraction mismatch")
    print("cxba_sandbox_smoke_passed network=none fixture=synthetic")


if __name__ == "__main__":
    main()
