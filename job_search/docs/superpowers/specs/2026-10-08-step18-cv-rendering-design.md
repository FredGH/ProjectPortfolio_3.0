# Step 18 — CV rendering (18a ATS .docx, 18b designed PDF): design

Status: approved by the user 2026-10-08 (open questions resolved below). Branch `feat/step18-ats-docx`.

## Purpose

Turn an approved Step 17 `TailoredDocument` into two files for one job:

- **18a** an ATS-safe `.docx` plus a `.txt` twin. Machine-readable first.
- **18b** a designed PDF that reproduces the look of the user's own CV
  (`private/FirstName_LastName_2026_v4_template.pdf` (placeholder-name template; `.pages` source alongside)). Human-readable first.

Success: text extracted from the generated `.docx` reads in the intended
section order (automated test), the exact `title_for_display` is in the
rendered text, and no personal data is ever committed.

## Constraints

- **The repo is public.** No CV file, template or output is committed. The
  reference PDF stays in `private/`, outputs go to `output/` (both
  git-ignored). Tests use invented fixtures only.
- Rules are enforced by construction, not by prompt: the builder has no code
  path for tables, text boxes, headers/footers or images.
- Only approved content is rendered. The renderer never edits wording, never
  reads the database, never calls an LLM.

## Shared render model

New package `core/render/`:

| Module | One purpose |
|---|---|
| `model.py` | `RenderDoc`: the document as ordered, typed sections (heading, paragraphs, bullets). Built from `TailoredDocument` + job context (`company`). The single place that fixes section order and heading names. |
| `format.py` | Pure text rules: `MM/YYYY – MM/YYYY` dates, acronym expansion, filename. |
| `docx_ats.py` | `RenderDoc` → `.docx` (python-docx, single column). |
| `text.py` | `RenderDoc` → `.txt`, and `extract_docx_text()` reading the .docx XML in document order. |
| `pdf_designed.py` (18b) | `RenderDoc` → PDF using the design tokens below. |

18a and 18b read the same `RenderDoc`, so content and order cannot drift.

## 18a details

- **Order and headings:** name, headline (`target_title`), contact line, then
  the user's own template wording, in its order, each only when non-empty:
  `PROFESSIONAL SUMMARY`, `CORE TECHNICAL SKILLS`, `WORK EXPERIENCE`,
  `PERSONAL PROJECTS`, `PUBLICATIONS`, `EDUCATION`,
  `PROFESSIONAL QUALIFICATIONS & CONTINUOUS PERSONAL DEVELOPMENT`,
  `ACTIVITIES & INTERESTS`. (Decided 2026-10-08: the user chose their full
  template wording over the shorter ATS-standard names.)
- **Title:** `target_title` is a template field set once at the top. Never
  passed through any text transform.
- **Dates:** truth-base `start`/`end` → `MM/YYYY – MM/YYYY`; open end →
  `Present`. Unparseable values are kept verbatim and logged.
- **Acronyms:** a small curated dictionary (`ELT`, `GCP`, `CI/CD`, ...). On
  first use in the document, `ELT` → `ELT (Extract, Load, Transform)`. Deterministic,
  not LLM-generated; only listed acronyms are expanded.
- **Filename:** `<surname>_<title_for_display>_<company>.docx`, sanitised for
  the filesystem (spaces → `_`, no path characters), surname = last word of
  `identity`.
- **Contact:** one body paragraph, plain text (no hyperlink fields, no icons).
- **Text twin:** `.txt` from `RenderDoc` directly; test diffs it against text
  extracted from the generated `.docx` XML.

## 18a tests (unittest, invented fixtures)

1. Extracted `.docx` text equals the `.txt` twin.
2. Section headings appear in the specified order.
3. The exact `target_title` is present in the rendered text.
4. The package contains no `w:tbl`, `w:txbxContent`, header or footer parts,
   or media.
5. Date and acronym rules (first use only), filename sanitising.

## 18b outline (own plan after 18a merges)

Design tokens read from the reference (A4, one column): name large bold in
blue (~#2F5597), headline bold black, contact line separated by pipes with
the LinkedIn URL in blue, section headings uppercase bold blue, role title
bold and `Company – Dates` italic, bullets with bold key phrases, sans-serif
(Calibri-like) body at ~9.5 pt. The reference is used as a visual guide
only; no personal text is copied into code.

**Decided:** the PDF stays flat. Step 17 strips bullet emphasis and 18b does
not reintroduce bold key phrases; the reference's bold is not reproduced.

## Out of scope

Cover letter and pitch (Step 19), editing wording at render time, uploading
files anywhere, UI/API wiring (a `render-cv` CLI command is the only
entry point in 18a).

## Resolved questions

1. `company` in the filename comes from the job's company record
   (`dim_company`).
2. Dependencies: `python-docx` for 18a; the PDF engine for 18b is chosen in
   its own plan.
