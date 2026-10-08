# Step 18b — designed PDF: design

Status: draft for review. Builds on the Step 18 spec
([2026-10-08-step18-cv-rendering-design.md](2026-10-08-step18-cv-rendering-design.md))
and the merged-or-open 18a work (PR #54): the same `RenderDoc` feeds the PDF.

## Purpose

Render the same approved tailored CV as a **designed PDF** that looks like the
user's own CV template, for sending to people (not parsers). The ATS `.docx`
(18a) stays the file for application portals.

Success: the PDF visually matches the template (A4, Calibri, blue name and
section headings, one column), reads in the same section order as the `.docx`
(automated test on the extracted PDF text), and no personal data or
proprietary font is committed.

## Decisions already made

- **Flat text.** Bullets carry no bold key phrases (Step 17 strips emphasis
  and the user chose to keep the PDF flat).
- **Same content.** The PDF is rendered from `RenderDoc`, so wording, order,
  headings, dates and acronym expansion are identical to the `.docx`.

## Design

### Engine: reportlab

Pure Python, arm64/Docker-friendly, no system libraries, exact control of
fonts, colours and spacing. Rejected: WeasyPrint (needs pango/cairo system
libraries on macOS and in the slim image) and a .docx → PDF conversion
(needs LibreOffice, which is not installed). Pin `reportlab` in
`requirements.txt`; add `pypdf` to `requirements-dev.txt` for the tests.

### New module `core/render/pdf_designed.py`

`write_pdf(doc: RenderDoc, path: Path) -> None`, plus `find_fonts() -> Fonts`
(a small dataclass of regular/bold/italic font names registered with
reportlab). It maps each `Block` kind to a paragraph style:

| Block kind | Style (from the template, to be tuned side by side) |
|---|---|
| `name` | Calibri Bold ~20 pt, blue (~#2F5597) |
| `headline` | Calibri Bold ~11 pt, black |
| `contact` | Calibri ~9.5 pt; the LinkedIn URL in blue |
| `heading` | Calibri Bold ~10.5 pt, blue; text is already uppercase |
| `role_title` | Calibri Bold ~10 pt |
| `role_meta` | Calibri Italic ~9.5 pt |
| `paragraph` | Calibri ~9.5 pt |
| `bullet` | Calibri ~9.5 pt with a `•` marker and hanging indent |

A4, ~50 pt side margins. A role title, its meta line and its first bullet are
kept together so a page never ends on a lone role heading. Real CVs may run
past one page; the template is one page, so layout flows to a second page
rather than shrinking text.

### Fonts: the template's Calibri is proprietary

Calibri cannot be committed to a public repo. `find_fonts()` looks, in order:

1. `private/fonts/` (git-ignored): `Calibri.ttf`, `Calibrib.ttf`,
   `Calibrii.ttf` if the user drops them there;
2. the copies shipped with Microsoft Word on macOS
   (`/Applications/Microsoft Word.app/Contents/Resources/DFonts/`);
3. otherwise reportlab's built-in Helvetica family, with a logged warning
   that the PDF will not match the template's typeface.

So the PDF matches the template on this Mac, and degrades safely in Docker
unless the fonts are mounted. Nothing proprietary is committed.

### Entry point and output

`render-cv` (from 18a) also writes `<same base name>.pdf` next to the
`.docx` and `.txt`; a new `--ats-only` flag skips the PDF. `render_cv_files`
returns the PDF path in `RenderedCv` (new field `pdf_path: Path | None`).

### Verification

After writing, extract the PDF's text with `pypdf` (a dev/test dependency
only; the runtime check is lighter): the service verifies the file opens,
contains the exact `target_title`, and that the section headings appear in
`RenderDoc` order. On failure the PDF is removed and `RenderError` is
raised, like the `.docx`. Tests use invented fixtures and run with the
Helvetica fallback so they pass anywhere.

## Tests (unittest, invented fixtures)

1. A PDF is written, is A4, and its extracted text contains the title and
   every heading, in `RenderDoc` order.
2. `find_fonts()` falls back to Helvetica (with a warning) when no Calibri is
   found, and prefers `private/fonts/` over the Word copy.
3. A very long CV flows onto a second page without cutting text.
4. A control character or very long bullet does not crash the writer.
5. `render-cv` writes the `.pdf` by default and not with `--ats-only`; the
   existing 18a tests still pass.

## Out of scope

Editing wording at render time, bold key phrases, clickable links beyond the
LinkedIn URL, uploading or emailing the PDF, any web UI.

## Open questions

1. OK that `render-cv` writes the PDF by default (with `--ats-only` to skip)?
2. The colours and sizes above are read off a rendering of the template, not
   from its source. OK to tune them by comparing the first generated PDF
   against your template side by side?
