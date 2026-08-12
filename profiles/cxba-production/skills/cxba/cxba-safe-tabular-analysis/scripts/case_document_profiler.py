#!/usr/bin/env python3
from __future__ import annotations

import argparse
import email
import html
import json
import os
import re
import shutil
import subprocess
import zipfile
from email import policy
from pathlib import Path
from xml.etree import ElementTree

TABULAR = {".xlsx", ".xls", ".csv", ".tsv"}
TEXT = {
    ".txt", ".md", ".log", ".json", ".jsonl", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".ini", ".cfg", ".conf", ".sql",
}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"}
AUDIO_VIDEO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".mp4", ".mov", ".avi", ".mkv"}


def file_type(path: Path) -> str:
    result = subprocess.run(
        ["file", "-b", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "unknown"


def mime_type(path: Path) -> str:
    result = subprocess.run(
        ["file", "-b", "--mime-type", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "application/octet-stream"


def normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line.strip()).strip()


def extract_rtf(path: Path) -> str:
    from striprtf.striprtf import rtf_to_text
    raw = path.read_bytes().decode("latin-1", errors="replace")
    return rtf_to_text(raw, errors="replace")


def extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        parts = [node.text or "" for node in paragraph.iter(namespace + "t")]
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def extract_xml_text(xml: bytes) -> str:
    root = ElementTree.fromstring(xml)
    return "\n".join(text.strip() for text in root.itertext() if text.strip())


def extract_pptx(path: Path) -> tuple[str, dict[str, object]]:
    with zipfile.ZipFile(path) as archive:
        slides = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        text = "\n".join(extract_xml_text(archive.read(name)) for name in slides)
    return text, {"slides": len(slides)}


def extract_open_document(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return extract_xml_text(archive.read("content.xml"))


def extract_pdf(path: Path) -> tuple[str, dict[str, object]]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text, {"pages": len(reader.pages)}


def extract_msg_text(path: Path) -> tuple[str, dict[str, object]]:
    import extract_msg
    message = extract_msg.openMsg(str(path))
    try:
        fields = [
            f"Subject: {message.subject or ''}",
            f"From: {message.sender or ''}",
            f"To: {message.to or ''}",
            f"Date: {message.date or ''}",
            "",
            message.body or "",
        ]
        attachments = [getattr(item, "longFilename", None) or getattr(item, "shortFilename", None) or "" for item in message.attachments]
        return "\n".join(fields), {"attachments": attachments}
    finally:
        message.close()


def extract_doc_strings(path: Path) -> str:
    if shutil.which("antiword"):
        result = subprocess.run(
            ["antiword", str(path)], check=False, capture_output=True, text=True, errors="replace"
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    for args in (["strings", "-el", str(path)], ["strings", "-a", str(path)]):
        result = subprocess.run(args, check=False, capture_output=True, text=True, errors="replace")
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return ""


def extract_text_file(path: Path) -> tuple[str, dict[str, object]]:
    for encoding in ("utf-8-sig", "gb18030", "utf-16", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            if path.suffix.lower() in {".html", ".htm"}:
                text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
                text = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", text))
            return text, {"encoding": encoding}
        except UnicodeError:
            continue
    return "", {}


def extract_eml(path: Path) -> tuple[str, dict[str, object]]:
    message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    bodies = []
    attachments = []
    for part in message.walk():
        disposition = part.get_content_disposition()
        if disposition == "attachment":
            attachments.append(part.get_filename() or "")
        elif part.get_content_type() == "text/plain":
            try:
                bodies.append(part.get_content())
            except Exception:
                pass
    headers = [
        f"Subject: {message.get('subject', '')}",
        f"From: {message.get('from', '')}",
        f"To: {message.get('to', '')}",
        f"Date: {message.get('date', '')}",
    ]
    return "\n".join(headers + [""] + bodies), {"attachments": attachments}


def extract_image(path: Path) -> tuple[str, dict[str, object], str]:
    metadata: dict[str, object] = {}
    try:
        from PIL import Image
        with Image.open(path) as image:
            metadata.update({"width": image.width, "height": image.height, "mode": image.mode})
    except Exception:
        pass
    if not shutil.which("tesseract"):
        return "", metadata, "ocr_dependency_missing"
    result = subprocess.run(
        ["tesseract", str(path), "stdout", "-l", "chi_sim+eng"],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode != 0:
        metadata["ocrError"] = result.stderr[-500:]
        return "", metadata, "ocr_failed"
    return result.stdout, metadata, "complete_ocr"


def media_metadata(path: Path) -> dict[str, object]:
    if not shutil.which("ffprobe"):
        return {}
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def zip_listing(path: Path) -> tuple[str, dict[str, object], str]:
    with zipfile.ZipFile(path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        encrypted = sum(bool(item.flag_bits & 0x1) for item in members)
        metadata = {
            "memberCount": len(members),
            "encryptedMemberCount": encrypted,
            "uncompressedBytes": sum(item.file_size for item in members),
            "members": [
                {"path": item.filename, "sizeBytes": item.file_size}
                for item in members[:2000]
            ],
        }
    status = "encrypted_archive" if encrypted else "archive_inventory_complete"
    return "\n".join(item.filename for item in members), metadata, status


def extract_one(path: Path) -> tuple[str, dict[str, object], str]:
    suffix = path.suffix.lower()
    detected_mime = mime_type(path)
    metadata: dict[str, object] = {}
    if path.stat().st_size == 0:
        return "", metadata, "empty_file"
    if suffix == ".rtf":
        return extract_rtf(path), metadata, "complete"
    if suffix == ".docx":
        return extract_docx(path), metadata, "complete"
    if suffix == ".pptx":
        text, metadata = extract_pptx(path)
        return text, metadata, "complete"
    if suffix in {".odt", ".ods", ".odp"}:
        return extract_open_document(path), metadata, "complete"
    if suffix == ".pdf":
        text, metadata = extract_pdf(path)
        return text, metadata, "complete" if text.strip() else "ocr_required"
    if suffix == ".msg":
        text, metadata = extract_msg_text(path)
        return text, metadata, "complete"
    if suffix == ".doc":
        text = extract_doc_strings(path)
        return text, metadata, "partial_strings" if text.strip() else "unsupported_binary_doc"
    if suffix == ".eml":
        text, metadata = extract_eml(path)
        return text, metadata, "complete"
    if suffix in TEXT or detected_mime.startswith("text/"):
        text, metadata = extract_text_file(path)
        return text, metadata, "complete" if text or path.stat().st_size == 0 else "text_decode_failed"
    if suffix in IMAGES or detected_mime.startswith("image/"):
        return extract_image(path)
    if suffix == ".zip":
        return zip_listing(path)
    if suffix in AUDIO_VIDEO or detected_mime.startswith(("audio/", "video/")):
        return "", media_metadata(path), "metadata_only_requires_transcription"
    fallback = extract_doc_strings(path)
    return fallback, metadata, "unknown_binary_strings" if fallback.strip() else "parser_required"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--extracted-dir", required=True)
    parser.add_argument("--file")
    args = parser.parse_args()

    root = Path(args.root).resolve(strict=True)
    output = Path(args.output).resolve()
    extracted_dir = Path(args.extracted_dir).resolve()
    if not root.is_dir() or output == root or root in output.parents or root in extracted_dir.parents:
        raise SystemExit("INVALID_PATH_BOUNDARY")
    output.parent.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)

    records = []
    if args.file:
        selected = (root / args.file).resolve(strict=True)
        if root not in selected.parents or not selected.is_file():
            raise SystemExit("FILE_OUTSIDE_SOURCE_ROOT")
        if selected.suffix.lower() in TABULAR:
            raise SystemExit("TABULAR_FILE_NOT_SUPPORTED")
        candidates = [selected]
    else:
        candidates = sorted(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() not in TABULAR
        )
    for index, path in enumerate(candidates, start=1):
        relative = path.relative_to(root).as_posix()
        record: dict[str, object] = {
            "fileId": f"D{index:04d}",
            "file": relative,
            "extension": path.suffix.lower(),
            "sizeBytes": path.stat().st_size,
            "detectedFormat": file_type(path),
            "detectedMime": mime_type(path),
        }
        try:
            text, metadata, status = extract_one(path)
            normalized = normalize_text(text)
            extracted_path = extracted_dir / f"D{index:04d}.txt"
            extracted_path.write_text(normalized + ("\n" if normalized else ""), encoding="utf-8")
            record.update({
                "status": status,
                "extractedText": str(extracted_path),
                "characterCount": len(normalized),
                "lineCount": len(normalized.splitlines()) if normalized else 0,
                "contentPreview": normalized[:2000],
                "metadata": metadata,
            })
        except Exception as error:
            record.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
        records.append(record)

    payload = {
        "rootLabel": root.name,
        "fileCount": len(records),
        "completeCount": sum(record.get("status") == "complete" for record in records),
        "files": records,
    }
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps({
        "status": "completed",
        "output": str(output),
        "fileCount": payload["fileCount"],
        "completeCount": payload["completeCount"],
        "errorCount": sum(record.get("status") == "error" for record in records),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
