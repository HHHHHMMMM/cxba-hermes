# CXBA Session Workspace Layout

Use this layout inside the current Session Workspace:

```text
/workspace/
├── input/             trusted control-plane inputs such as materials.json
├── catalog/           normalized material catalog and structure profiles
├── notes/             material notes and investigation notes
├── evidence/          evidence items with source locations and limitations
├── scripts/           reproducible analysis scripts written for the case
├── intermediate/      converted text, OCR, parquet, DuckDB, and bounded extracts
├── results/           tables, charts, and machine-readable analysis results
├── review/            optional independent-review inputs and results
└── reports/           draft and final reports
```

Rules:

- `/data` is read-only original material. Copy a file to `/workspace/intermediate` before conversion.
- `/case-sessions` and `/shared` are read-only inputs. `/exchange/<current-session>` is the only writable exchange directory.
- Use the Spring `materialId` in catalog entries, notes, evidence, result metadata, and proposal source references.
- A rename or move changes `relativePath`, not `materialId`. Refresh the Spring catalog before reusing a stale path.
- Do not create scan-order material numbers, content hashes, version directories, or automatic new Sessions.
- Scripts and intermediate files remain visible in the case file workspace; do not hide them in package caches.
