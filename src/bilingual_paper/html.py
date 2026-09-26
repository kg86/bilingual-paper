from __future__ import annotations

from html import escape
from pathlib import Path

from .languages import DEFAULT_LANG, language_name, text_direction
from .models import Paragraph, validate


def render(
    paragraphs: list[Paragraph], output: Path, title: str = "Bilingual PDF Reader",
    *, target_lang: str = DEFAULT_LANG,
) -> None:
    validate(paragraphs, require_translation=True)
    label = f"{language_name(target_lang)} translation"
    direction = text_direction(target_lang)
    cards = "\n".join(
        f'''<section class="pair" id="{escape(p.id)}" data-source-sha256="{p.source_hash}">
  <article lang="en"><h2>English source · {escape(p.id)}</h2><p>{escape(p.english)}</p></article>
  <article lang="{escape(target_lang)}" dir="{direction}"><h2>{escape(label)} · {escape(p.id)}</h2><p>{escape(p.translation)}</p></article>
</section>'''
        for p in paragraphs
    )
    output.write_text(f'''<!doctype html><html lang="{escape(target_lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>
body{{margin:0;background:#edf2f7;color:#172033;font-family:ui-serif,serif;line-height:1.7}}main{{max-width:1180px;margin:auto;padding:3rem;background:#fff}}.pair{{display:grid;grid-template-columns:1fr 1fr;margin:1rem 0;border:1px solid #d6dee8;border-radius:.5rem;overflow:hidden}}article{{padding:1rem 1.25rem}}article:first-child{{background:#f6f9fd;border-right:1px solid #d6dee8}}h2{{font:700 .75rem ui-sans-serif,sans-serif;color:#52677f;letter-spacing:.06em;margin:0 0 .6rem}}p{{white-space:pre-wrap;margin:0}}@media(max-width:760px){{main{{padding:1rem}}.pair{{grid-template-columns:1fr}}article:first-child{{border-right:0;border-bottom:1px solid #d6dee8}}}}@media print{{body{{background:#fff}}main{{padding:0}}}}
</style></head><body><main><h1>{escape(title)}</h1><p>English source text is preserved verbatim from the reviewed inventory.</p>{cards}</main></body></html>''', encoding="utf-8")
