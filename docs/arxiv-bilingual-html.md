# Procedure: bilingual HTML for arXiv HTML papers

Target: arXiv HTML papers published at `https://arxiv.org/html/<arXiv ID>` (LaTeXML output). Since the source
is already structured HTML (with inline MathML), a single `bilingual-paper arxiv` command handles the whole
pipeline (implementation: `src/bilingual_paper/arxiv.py`).

## What you get

A single HTML file that keeps the original arXiv page's DOM structure, CSS, MathML, figures and footnote links
completely intact, with the matching translation (default Japanese, changeable with `--target-lang`) inserted
right after each paragraph, heading and figure/table caption. Formulas, citation numbers and cross-reference
links (e.g. "Section 3", "[5]", "Table 1") are duplicated verbatim, so formulas and reference links keep working
on the translated side too.

## Prerequisites

```bash
uv sync
gcloud auth application-default login   # once
export GOOGLE_CLOUD_PROJECT=<your-gcp-project>   # check with `gcloud config get-value project`
```

Gemini is called through Vertex AI (no API key needed, ADC auth). `GOOGLE_CLOUD_LOCATION` is optional (defaults
to `global`).

## Running it

```bash
uv run bilingual-paper arxiv <SOURCE> [--output PATH] [--checkpoint PATH] [--model MODEL] [--base-href URL] [--target-lang CODE]
```

`--target-lang` defaults to `ja` (Japanese). You can pass any code listed in `LANGUAGES` in
`src/bilingual_paper/languages.py` (`ko` Korean, `zh-Hans` Simplified Chinese, `zh-Hant` Traditional Chinese,
etc.) — anything else errors immediately. When running this from a chat request, infer the target language from
the language of the user's instruction (e.g. a Korean request implies `--target-lang ko`).

`SOURCE` accepts any of the following; all are normalized to the same handling internally.

- A bare arXiv ID: `2307.01412` / `2307.01412v4`
- An arXiv URL: `https://arxiv.org/abs/2307.01412`, `/pdf/...`, or `/html/...`
- The path to a locally saved arXiv HTML file (e.g. saved from a browser via Save As)

For an ID/URL, `https://arxiv.org/html/<id>` is fetched directly (no need to download it via a browser first).
For a local file, the ID is auto-detected from the watermark in the page's top-right corner
(`arXiv:2307.01412v4 [cs.DS] ...`); if there's no watermark, it looks for an `arXiv:<id>` / `arxiv.org/abs/<id>`
string appearing before `<article>` (it won't pick up IDs of other papers cited in the body). A paper with no
arXiv HTML version (404) produces an error saying so.

If output paths are omitted:

- Main output: `outputs/<slug>-<lang>-bilingual.html`
- Translation checkpoint: `work/<slug>/<lang>/translations.json`

(`<slug>` is the detected arXiv ID, or the local filename if none was detected. `<lang>` is `--target-lang`.)

Both are split per language, so re-translating the same paper into a different language never overwrites or
mixes in the other language's translations.

Progress is saved to the checkpoint per batch (12 units by default), so if a run is interrupted (e.g. by an API
error) re-running it skips already-translated units and resumes from where it left off. The checkpoint records
the target language and each unit's English text, so:

- Passing a checkpoint whose language differs from `--target-lang` is an error.
- If a paper's English text changed (e.g. a new revision), the affected unit is retranslated rather than reusing
  the stale translation.
- An old-format checkpoint (one that doesn't record language/English text) errors out; delete it and start over.

Transient Gemini errors (5xx/429, empty or malformed replies) are retried automatically with backoff.

### Choosing a model

The default is `gemini-flash-lite-latest` (`bilingual_paper.gemini.DEFAULT_MODEL`), but for papers dense with
formulas and references, the lightweight model is more prone to dropping placeholders (see below). For quality,
`--model gemini-2.5-flash` is recommended (verified: completed 2307.01412 with zero warnings).

```bash
uv run bilingual-paper arxiv 2307.01412 --model gemini-2.5-flash
```

## How it works internally (read this when troubleshooting)

`src/bilingual_paper/arxiv.py` processes things in this order:

1. **`resolve_source`**: Resolves SOURCE into HTML text and a `<base href>`. For arXiv-sourced input, it adds
   `<base href="https://arxiv.org/html/">`. This makes relative paths in the page (CSS under `/static/...`,
   figures like `2307.01412v4/xxx.png`) resolve to their real arXiv paths, so styles and images render correctly
   when the file is opened in a browser (the original page itself has no `<base>`, so without this the saved
   HTML would immediately have broken layout and missing images).
2. **`collect_units`**: Collects translation targets from inside `<article>`, in DOM order. Targets are: the
   title (`h1`), headings (`h2`–`h4`), paragraphs directly under the abstract, each `p.ltx_p` directly under a
   `div.ltx_para` (including the lead-in text of an `ltx_para` that wraps a list or theorem; nested items are
   each only picked up once, via their own `ltx_para`), `figcaption`, and footnote bodies
   (`span.ltx_note_content`). The bibliography (`ltx_bibliography`) is excluded (reference entries aren't
   translated).
3. **`extract_segment`**: When extracting each unit's text, formulas (`<math>`), citations (`<cite>`), reference
   links (`<a class="ltx_ref">`), numbering tags (elements whose `class` contains `ltx_tag`, e.g. section numbers
   or the "Theorem 1." heading number), and footnotes (`ltx_note`, footnote marker `ltx_note_mark`) are treated
   as "opaque" and replaced with ASCII placeholder tokens `@@0@@`, `@@1@@`, ... (the original HTML fragment is
   kept, with only its `id` attribute stripped to avoid ID collisions at the copy destination). Other text (e.g.
   `em`) is left as plain text.
4. Gemini receives the plain text with placeholders and an instruction to translate it while keeping the tokens
   exactly as-is, placed wherever they'd naturally fall in the target language
   (`build_instructions(target_lang)` fills in the language name). ID reconciliation
   (`merge_invented_ids`) and a check for missing placeholders (`missing_tokens`) follow; if any are missing,
   just that unit is resent individually (up to 3 times, with a stronger instruction each time). If it's still
   missing after that, the tool falls back to appending the missing placeholder's content verbatim at the end of
   the translation — on the judgment that this looks worse but is better than silently losing content (a warning
   is printed to stderr). Headings (`tr-heading`) and footnote bodies (`tr-note`) are the only exceptions that
   tolerate missing section-number/footnote-number placeholders, since the number already appears right above in
   the English text, so there's no real harm.
5. **`reconstruct`**: Substitutes each `@@N@@` in the translated text back with its corresponding original HTML
   fragment, producing the translated side's HTML fragment.
6. **`apply_translations`**: Inserts the translation right after each unit (`insert_after`) as a new element with
   `class="tr-para"` / `"tr-heading"` / `"tr-caption"` / `"tr-note"`, `lang="<target_lang>"`, and `dir` (`rtl`
   for Arabic). The original English element is never modified. Fonts are switched per language via `:lang()`
   (so Chinese text doesn't render with Japanese glyph shapes).

## Verification steps (do these every time)

1. After running the command, check stderr for `warning: ... kept placeholder token(s) ... unmerged`. If it
   appears, look up the affected unit in `work/<slug>/<lang>/translations.json` and visually judge whether it's
   acceptable.
2. Start a local server and open the file in Chrome to check the rendering (`file://` can't be opened from
   extensions, so go through a local server).

   ```bash
   cd outputs && uv run python -m http.server 8791
   # in another tab, open http://localhost:8791/<slug>-<lang>-bilingual.html from claude-in-chrome
   ```

   Things to check: does the translation appear right below the title/heading, are formulas rendered, do figure
   images show up (is `<base>` working), and are citation `[n]` and "Section n" links still clickable.
3. After checking, stop the local server, e.g. with `pkill -f "http.server 8791"`.

## Known limitations

- The bibliography is not translated.
- Footnote body translations are inserted right after the English footnote body. Footnotes duplicated on the
  translated paragraph side (via placeholders) stay in English. This hasn't been verified on a real paper with
  footnotes, so check it visually the first time.
- `<base href>` is hardcoded to `https://arxiv.org/html/` (image paths are self-contained, including the
  versioned directory). If this breaks for a different version or a future arXiv path change, override it with
  `--base-href`.
