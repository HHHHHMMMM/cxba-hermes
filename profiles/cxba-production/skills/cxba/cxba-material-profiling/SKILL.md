---
name: cxba-material-profiling
description: Profile case materials without treating metadata as proof.
author: CXBA Project Team, Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cxba, files, profiling]
    related_skills: [cxba-case-investigation, cxba-raw-material-investigation, cxba-safe-tabular-analysis]
---

# CXBA Material Profiling Skill

Build a bounded structural view of case materials while preserving the Spring material identity. Profiling discovers formats and navigation points; it does not prove business meaning or require reading every material before focused work begins.

## When to Use

- A material format, encoding, Sheet layout, page count, or archive structure is unknown.
- A stable catalog is needed before opening selected source locations.
- New or moved materials require refreshed path resolution.
- Do not use profile output alone as evidence or as a substitute for source reading.

## Prerequisites

- Spring exports a JSON catalog whose entries contain `materialId` and `relativePath`.
- Catalog paths resolve below the read-only `/data` mount.
- `/workspace` is writable.
- All parsers are preinstalled in the Sandbox image; runtime installation is forbidden.

## How to Run

Normalize the Spring catalog:

```text
terminal(command="python /opt/cxba/scripts/material_catalog.py --catalog /workspace/input/materials.json --data-root /data --output /workspace/catalog/materials.json")
```

Profile one tabular material by stable identity:

```text
terminal(command="python /opt/cxba/scripts/tabular_profile.py --catalog /workspace/catalog/materials.json --material-id <materialId> --output /workspace/catalog/<safe-name>.table.json")
```

Extract one document by stable identity:

```text
terminal(command="python /opt/cxba/scripts/document_extract.py --catalog /workspace/catalog/materials.json --material-id <materialId> --output-dir /workspace/intermediate/documents")
```

OCR one image or PDF when direct extraction is inadequate:

```text
terminal(command="python /opt/cxba/scripts/ocr_material.py --catalog /workspace/catalog/materials.json --material-id <materialId> --output-dir /workspace/intermediate/ocr")
```

## Quick Reference

| Script | Output |
|---|---|
| `material_catalog.py` | Validated `materialId` to current relative path mapping |
| `tabular_profile.py` | Sheet/table structure, bounded samples, row counts, parse errors |
| `document_extract.py` | Extracted text plus page/paragraph metadata |
| `ocr_material.py` | OCR text and page-level output metadata |

## Procedure

1. Obtain the catalog from Spring; do not synthesize entries from a directory scan.
2. Normalize it with `material_catalog.py`. Completion means every active catalog entry has a unique non-empty `materialId` and a path confined below `/data`.
3. Select materials relevant to the current question. A complete catalog does not require a complete read.
4. Run the format-specific profiler. Large tabular inputs must be streamed or read in bounded batches.
5. Open the relevant source location with `read_file`, `vision_analyze`, or a format-specific script before making a factual claim.
6. Record actual read ranges and failures in `/workspace/notes`; distinguish unread, partially read, read, and blocked.
7. Refresh the Spring catalog after file rename, move, upload, or restore. Continue references by `materialId`, then resolve the new path.

## Pitfalls

- Extension and detected format may disagree; record both and use the parser matching actual content.
- A sample, preview, candidate header, page count, or Sheet list is not evidence of the unobserved content.
- Never mark a document fully read when only an extract preview or sample was inspected.
- Never invent `F0001`-style identities. Directory order changes when files are added or moved.
- Never install missing parsers during a Run. Return a clear capability error for image rebuild.

## Verification

- The normalized catalog retains Spring `materialId` values byte-for-byte.
- Adding or moving a file changes only its path metadata; existing material references remain stable.
- Every output stays below `/workspace` and no source file under `/data` changes.
- Large-file tests show bounded sampling and streaming row iteration.
