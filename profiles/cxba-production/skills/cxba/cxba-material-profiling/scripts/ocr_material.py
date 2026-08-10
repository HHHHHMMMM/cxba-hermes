#!/usr/bin/env python3
"""Run local OCR page by page without external services."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cxba_material_common import (
    MaterialToolError,
    atomic_write_text,
    load_material,
    sanitized_name,
    write_json,
)


def ocr_image(path: Path, languages: str) -> str:
    import pytesseract
    from PIL import Image

    with Image.open(path) as image:
        return pytesseract.image_to_string(image, lang=languages)


def ocr_pdf(path: Path, languages: str) -> tuple[str, list[dict[str, int]]]:
    import pymupdf
    import pytesseract
    from PIL import Image

    document = pymupdf.open(path)
    parts = []
    pages = []
    try:
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            text = pytesseract.image_to_string(image, lang=languages)
            parts.append(f"\n\n--- page {index} ---\n{text}")
            pages.append({"page": index, "characters": len(text)})
            image.close()
    finally:
        document.close()
    return "".join(parts).lstrip(), pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--languages", default="chi_sim+eng")
    args = parser.parse_args()

    entry, path = load_material(args.catalog, args.material_id)
    if path.suffix.lower() == ".pdf":
        text, pages = ocr_pdf(path, args.languages)
    elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        text = ocr_image(path, args.languages)
        pages = [{"page": 1, "characters": len(text)}]
    else:
        raise MaterialToolError("OCR supports PDF and image materials")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitized_name(args.material_id)
    text_path = args.output_dir / f"{stem}.txt"
    metadata_path = args.output_dir / f"{stem}.json"
    atomic_write_text(text_path, text)
    write_json(
        metadata_path,
        {
            "materialId": args.material_id,
            "relativePath": entry["relativePath"],
            "languages": args.languages,
            "textPath": str(text_path),
            "pages": pages,
        },
    )
    print(json.dumps({"status": "completed", "textPath": str(text_path), "metadataPath": str(metadata_path)}))


if __name__ == "__main__":
    try:
        main()
    except MaterialToolError as exc:
        raise SystemExit(f"ocr_material_failed: {exc}") from exc
