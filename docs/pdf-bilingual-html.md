# Procedure: bilingual HTML for PDF papers

Target: PDFs that have no arXiv HTML version (e.g. conference proceedings). If an arXiv HTML version exists,
prefer that instead (`bilingual-paper arxiv`, `docs/arxiv-bilingual-html.md`) — it reproduces formulas and
figures more faithfully. Implementation: `src/bilingual_paper/pdf.py`.

## What you get

The PDF is converted via layout analysis into Markdown (with formulas as LaTeX), turned into HTML, and a single
HTML file is produced with the translation (default Japanese, changeable with `--target-lang`) inserted right
after each heading, paragraph, list item, and figure/table caption. Formulas are rendered in-browser via MathJax
(CDN), and figures are embedded as base64 in the HTML, so the file opens standalone.

## Prerequisites

Translation-side requirements are the same as `bilingual-paper arxiv` (Vertex AI Gemini, ADC auth,
`GOOGLE_CLOUD_PROJECT` required). In addition, a PDF-to-Markdown converter must be installed **separately**
(kept as an external command rather than a bundled dependency, to avoid pulling heavy model weights and their
licenses into the core tool).

```bash
uv tool install "mineru[core]"   # default; installs the mineru-kit command. Downloads ~3GB of models on first run
uv tool install docling          # alternative (--converter docling)
```

| Converter | Paragraph joining (across columns/pages) | Inline formulas | Display formulas | Code | Time (11 pages, M4) | License |
|---|---|---|---|---|---|---|
| MinerU (default) | ✓ | Converted to LaTeX (sometimes adds stray accent marks) | ✓ | ✓ | ~2 min | Apache-2.0 + additional terms (commercial license required above 100M monthly active users / $20M monthly revenue) |
| docling | ✗ | Not converted (kept as original text) | Partial (some garbled output) | ✓ | ~3.5 min (including formulas) | MIT |

Marker was excluded because its current version requires a system install of `llama-server` (llama.cpp).

## Running it

```bash
uv run bilingual-paper pdf <PDF> [--converter mineru|docling] [--converter-bin PATH] [--converter-arg ARG ...] \
    [--reconvert] [--output PATH] [--checkpoint PATH] [--model MODEL] [--target-lang CODE]
```

- `--target-lang` behaves the same as in `arxiv`. When run from a chat request, infer it from the language of the
  instruction.
- The conversion result is cached at `work/<slug>/<converter>/<slug>.md`; re-running skips conversion and only
  translates. Use `--reconvert` to redo the conversion (also needed if you change `--converter-arg`).
- `--converter-arg` passes extra arguments straight through to the converter (repeatable). Examples: OCR a
  scanned PDF with MinerU via `--converter-arg=--ocr-mode --converter-arg=ocr`, or specify docling's OCR
  language.
- If the converter isn't on PATH, point `--converter-bin` directly at the executable.
- Default output/checkpoint paths, resuming from a checkpoint, Gemini retries, and model selection all work the
  same as in `arxiv` (`<slug>` is the PDF's filename).

MinerU only analyzes locally unless `--remote` is passed (this tool never passes it). `mineru-kit parse` doesn't
send telemetry (telemetry is a feature of the separate `mineru` document-library-server command).

## How it works internally (read this when troubleshooting)

1. **`convert_pdf`**: Runs the converter to get Markdown. MinerU only analyzes the first 10 pages by default, so
   `-p all` is passed. docling's default `docling_parse` backend loses inter-word spacing on LaTeX-derived
   cramped fonts, so `--pdf-backend pypdfium2` is passed, along with `--enrich-formula` to convert formulas to
   LaTeX.
2. **`markdown_to_html`**: Converts to HTML via markdown-it (CommonMark + tables + `dollarmath`). `$...$` /
   `$$...$$` become `<span class="math inline">\(...\)</span>` / `<div class="math block">\[...\]</div>` for
   MathJax. Raw HTML emitted by the converter (`<table>`, `<sup>`, etc.) passes through, but `script` tags,
   `on*` attributes, and `javascript:` links are stripped.
3. **`collect_units`**: Collects headings, paragraphs, and list items in document order. Content inside `pre`
   (code) and `table`, and paragraphs with no characters (e.g. page numbers), are excluded. If there's an
   "Abstract" heading, paragraphs before it (authors, affiliations) aren't translated. Paragraphs between a
   "References" / "Bibliography" heading and the next heading (bibliography entries) aren't translated either.
   Paragraphs starting with `Figure 1:` / `Table 2.` / `Listing 3:` etc. are treated as captions (`tr-caption`).
4. From here it's shared with `arxiv`: formulas, `code`, links, and `sub`/`sup` become `@@N@@` placeholders sent
   to Gemini (`extract_segment`, `translate_units`), and the translation is inserted right after the original
   element (inside the `<li>` for list items) (`apply_translations`).

## Verification steps (do these every time)

Same as `arxiv` (check placeholder warnings, view via a local server in a browser). Additionally:

- Check that paragraphs aren't split at a column/page break, or conversely that separate paragraphs weren't
  merged.
- Check that formulas render via MathJax (no red TeX error text).

## Known limitations

- Formula quality depends entirely on the converter's recognition. MinerU sometimes adds stray `\dot{}` /
  `\tilde{}` / `\vec{}` to inline formulas, or misidentifies monospaced code snippets as formulas and mixes in
  CJK characters (e.g. `$乜 _{i}$`). This tool does not correct these.
- Tables are not translated (they're mostly numeric, and complex tables can already be mangled by the converter
  at conversion time). Captions are translated.
- Captions inside code listings (a `Listing N: ...` that MinerU included inside a code block) are not
  translated.
- Author name diacritics can get mangled (e.g. `Köppl` → `Koppl ¨`).
