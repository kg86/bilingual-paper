# bilingual-paper

Translates arXiv HTML papers and PDF papers into bilingual (English + target language) HTML documents via Gemini.
See README.md, docs/arxiv-bilingual-html.md and docs/pdf-bilingual-html.md for the full pipelines.

## Target language

`bilingual-paper arxiv` and `bilingual-paper pdf` take `--target-lang` (default `ja`). Supported codes and their English
names are listed in `src/bilingual_paper/languages.py`.

When a user asks Claude to translate something via this project, infer the target language from the language
the request itself is written in, and pass the matching `--target-lang` — do not silently default to `ja`
unless the request was in Japanese (or the user names a language explicitly, which always wins).
