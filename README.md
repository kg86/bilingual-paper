# bilingual-paper

Bilingual (English + target language) HTML versions of papers, translated with Gemini. Two entry points:

- the **PDF pipeline** (`inventory` → `clean` → `translate` → `render` → `audit`), below;
- **`arxiv`**, for arXiv's own HTML papers — see [arXiv HTML papers](#arxiv-html-papers).

## PDF pipeline

Creates bilingual HTML from a JSON source inventory. The English source text is immutable once a stage's output is
saved; rendering and auditing ensure it is not omitted or changed by later stages.

### Commands

```bash
uv sync
uv run bilingual-paper inventory source.pdf inventory.json
uv run bilingual-paper audit-source source.pdf inventory.json
uv run bilingual-paper clean inventory.json cleaned.json
uv run bilingual-paper translate-template cleaned.json translation-work.json --target-lang ja
uv run bilingual-paper translate cleaned.json translation-work.json --model gemini-flash-lite-latest --target-lang ja
uv run bilingual-paper render translation-work.json bilingual.html
uv run bilingual-paper audit translation-work.json bilingual.html
uv run python -m unittest discover -s tests -v
```

`--target-lang` (default `ja`) picks the translation language; see `src/bilingual_paper/languages.py` for the
supported codes (`ja`, `ko`, `zh-Hans`, `zh-Hant`, `fr`, `de`, `es`, ...). Give it once, to `translate-template` or
`translate` — the code is saved into the output JSON's `target_lang` field, and both a later `translate` on the same
file and `render` read it back from there unless overridden with their own `--target-lang`. `translate` refuses to
continue a file that already holds translations in a different language; use a separate output file per language.
Arabic output is rendered right-to-left. When this pipeline is run
on someone's behalf from a chat session, the target language should be inferred from the language the request was
written in (e.g. a Korean request implies `--target-lang ko`), not left at the `ja` default.

`inventory` is text-layer extraction with generic paragraph heuristics (a block ends at sentence-final punctuation;
roman-numeral headings, back-matter headings and bare page numbers stand alone). For scanned or unreliable PDFs, review and correct the inventory against
rendered pages before translating. `audit-source` checks the raw inventory against the PDF's own text layer
line-for-line — run it right after `inventory`, before `clean`, since `clean` intentionally rewrites text.

`clean` is optional. PDF text-layer extraction can produce mojibake, dropped characters, or sentences split across
lines/columns; `clean` asks the model to reconstruct readable English (fixing artifacts, joining broken sentences)
without summarizing, translating, or dropping content. Each run checkpoints the JSON after every batch (recording
which segment IDs are done in `cleaned_ids`), so a retry resumes where it stopped. Skip it and feed `inventory.json` straight to `translate` if the
extraction is already clean, or hand-edit the inventory yourself instead.

`translate` and `clean` call Gemini (`gemini-flash-lite-latest` by default) through Vertex AI, authenticated with
local Application Default Credentials rather than an API key:

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=<your-gcp-project>
# optional, defaults to "global":
export GOOGLE_CLOUD_LOCATION=us-central1
```

`translate` checkpoints the JSON after every batch, so a retry resumes completed translations. Transient Gemini
errors (5xx/429, empty or unparseable replies) are retried with backoff, and a batch whose reply drops or duplicates
IDs is redone one segment at a time. The renderer does
not use an LLM for English source text — it renders exactly what is in the reviewed inventory.

## arXiv HTML papers

`bilingual-paper arxiv` handles arXiv's own HTML papers (`https://arxiv.org/html/<id>`, LaTeXML output)
without going through the PDF/inventory pipeline above. Because
the source is already structured HTML, it extracts paragraphs/headings/captions straight from the DOM (inline
MathML included), translates each once via Gemini, and inserts the translation (`--target-lang`, default `ja`) directly under its
English counterpart in the original document, preserving all original structure, CSS and MathML.

```bash
uv run bilingual-paper arxiv 2307.01412
uv run bilingual-paper arxiv https://arxiv.org/abs/2307.01412v4
uv run bilingual-paper arxiv path/to/saved-arxiv-page.html --target-lang ko
```

`SOURCE` may be a bare arXiv ID, an `arxiv.org/abs|pdf|html/...` URL (fetched directly from arXiv), or a local
HTML file already saved from an arXiv HTML page. Output defaults to `outputs/<slug>-<lang>-bilingual.html` and the
translation checkpoint to `work/<slug>/<lang>/translations.json` (both overridable with `--output`/`--checkpoint`), so
runs in different languages never overwrite or reuse each other; see
`--help` for all options. See `docs/arxiv-bilingual-html.md` for the full procedure this script implements.

## License

MIT — see [LICENSE](LICENSE).
