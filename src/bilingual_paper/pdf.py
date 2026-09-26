"""`bilingual-paper pdf`: build a bilingual (English/target-language) HTML page from a PDF paper.

A PDF has no reusable structure, so an external layout-analysis tool first converts it to Markdown
with LaTeX math (MinerU by default, or docling): it recovers reading order across columns,
paragraphs split by column/page breaks, headings, lists, code listings, tables, figures, and inline
and display formulas. That Markdown is rendered to HTML, and the arXiv pipeline's translation step
is reused unchanged: each heading, paragraph and list item is one translation unit, with math,
inline code, links and sub/superscripts protected as opaque placeholder tokens. Formulas are
typeset in the browser by MathJax.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from . import gemini
from .arxiv import (
    STYLE,
    TOKEN_RE,
    apply_translations,
    default_paths,
    extract_segment,
    translate_units,
)
from .languages import DEFAULT_LANG, LANGUAGES, language_name


def mineru_arguments(pdf: Path, out_dir: Path) -> list[str]:
    # Local parse (never --remote); MinerU only parses the first 10 pages unless told otherwise.
    return ["parse", str(pdf), "-o", str(out_dir), "-p", "all"]


def docling_arguments(pdf: Path, out_dir: Path) -> list[str]:
    # pypdfium2 keeps inter-word spaces that the default docling-parse backend drops for tightly
    # kerned LaTeX fonts; --enrich-formula turns formula regions into LaTeX.
    return [
        "convert",
        str(pdf),
        "--to",
        "md",
        "--pdf-backend",
        "pypdfium2",
        "--enrich-formula",
        "--image-export-mode",
        "embedded",
        "--output",
        str(out_dir),
    ]


@dataclass(frozen=True)
class Converter:
    """An external PDF -> Markdown (+ LaTeX math) command that writes `<out_dir>/<pdf stem>.md`."""

    command: str
    install_hint: str
    arguments: Callable[[Path, Path], list[str]]


CONVERTERS: dict[str, Converter] = {
    "mineru": Converter(
        "mineru-kit", 'uv tool install "mineru[core]"', mineru_arguments
    ),
    "docling": Converter("docling", "uv tool install docling", docling_arguments),
}
DEFAULT_CONVERTER = "mineru"

CAPTION_RE = re.compile(
    r"^(?:Figure|Fig\.|Table|Listing|Algorithm)\s*[A-Z]?\d+[.:]", re.IGNORECASE
)
LETTER_RE = re.compile(r"[^\W\d_]")
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
ABSTRACT_RE = re.compile(r"^\W*abstract\W*$", re.IGNORECASE)
REFERENCES_RE = re.compile(
    r"^[\W\d]*(?:references|bibliography|works cited)\W*$", re.IGNORECASE
)
UNSAFE_TAGS = ("script", "style", "iframe", "object", "embed", "form")

MATHJAX = """
<script>
MathJax = {tex: {inlineMath: [["\\\\(", "\\\\)"]], displayMath: [["\\\\[", "\\\\]"]], tags: "ams"}};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
"""

PAGE_STYLE = """
body {
  max-width: 52rem;
  margin: 2rem auto;
  padding: 0 1rem;
  font-family: "Times New Roman", serif;
  line-height: 1.55;
  color: #1b1b1b;
}
img { max-width: 100%; }
pre, .docvortex-algorithm {
  background: #f6f6f6;
  padding: .75em;
  overflow-x: auto;
  font-size: .9em;
}
table { border-collapse: collapse; margin: 1em 0; font-size: .9em; }
td, th { border: 1px solid #ccc; padding: .2em .45em; }
.math.block { overflow-x: auto; }
"""


def convert_pdf(
    pdf: Path,
    work_dir: Path,
    *,
    converter: str,
    command: str | None = None,
    extra_args: tuple[str, ...] = (),
    reconvert: bool = False,
) -> str:
    """Return the converter's Markdown for `pdf`, running it only if no cached output exists."""
    out_dir = work_dir / converter
    markdown_path = out_dir / f"{pdf.stem}.md"
    if markdown_path.exists() and not reconvert:
        print(
            f"reusing {markdown_path} (pass --reconvert to convert again)",
            file=sys.stderr,
        )
        return markdown_path.read_text(encoding="utf-8")

    spec = CONVERTERS[converter]
    executable = command or shutil.which(spec.command)
    if executable is None:
        raise ValueError(
            f"{spec.command!r} ({converter}) is not on PATH; install it with `{spec.install_hint}` "
            "or pass --converter-bin with the path to the executable."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"converting {pdf} with {converter} ...", file=sys.stderr)
    result = subprocess.run(
        [executable, *spec.arguments(pdf, out_dir), *extra_args], check=False
    )
    if result.returncode != 0:
        raise ValueError(f"{converter} failed with exit code {result.returncode}")
    if not markdown_path.exists():
        raise ValueError(f"{converter} finished but did not write {markdown_path}")
    return markdown_path.read_text(encoding="utf-8")


def _render_tex(content: str, options: dict) -> str:
    if options.get("display_mode"):
        return escape(f"\\[{content}\\]")
    return escape(f"\\({content}\\)")


def markdown_to_html(markdown: str) -> str:
    renderer = (
        MarkdownIt("commonmark", {"html": True})
        .enable("table")
        .use(dollarmath_plugin, renderer=_render_tex)
    )
    return renderer.render(markdown)


def sanitize(soup: BeautifulSoup) -> None:
    """Drop active content that raw HTML passed through from the converter could carry."""
    for tag in soup.find_all(UNSAFE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            value = str(tag.attrs[attr]).strip().lower()
            if attr.lower().startswith("on") or value.startswith("javascript:"):
                del tag.attrs[attr]


def is_opaque(tag: Tag) -> bool:
    if tag.name in ("code", "a", "img", "sup", "sub"):
        return True
    return "math" in (tag.get("class") or [])


def collect_units(root: Tag) -> list[dict]:
    """Headings, paragraphs and list items in document order; code, tables and math stay as-is.

    Author/affiliation lines before an "Abstract" heading and the entries of a References section
    are left untranslated: they are names and bibliographic data, which translation only garbles.
    """
    tags = [
        tag
        for tag in root.find_all([*HEADINGS, "p", "li"])
        if not tag.find_parent(["pre", "table"])
    ]
    in_front_matter = any(
        tag.name in HEADINGS and ABSTRACT_RE.match(tag.get_text(" ", strip=True))
        for tag in tags
    )
    in_references = False
    units: list[dict] = []
    for tag in tags:
        if tag.name in HEADINGS:
            heading = tag.get_text(" ", strip=True)
            if ABSTRACT_RE.match(heading):
                in_front_matter = False
            in_references = bool(REFERENCES_RE.match(heading))
        elif in_front_matter or in_references:
            continue
        # A loose list item's text is in its own <p>s; one wrapping a nested list would duplicate it.
        if tag.name == "li" and tag.find(["p", "ul", "ol"]):
            continue
        text, placeholders = extract_segment(tag, is_opaque)
        if not LETTER_RE.search(TOKEN_RE.sub("", text)):
            continue  # nothing to translate: bare numbers, symbols, or only math
        if tag.name in HEADINGS:
            css_class = "tr-heading"
        elif CAPTION_RE.match(text):
            css_class = "tr-caption"
        else:
            css_class = "tr-para"
        units.append(
            {
                "id": f"u{len(units) + 1:04d}",
                "anchor": tag,
                "css_class": css_class,
                "english": text,
                "placeholders": placeholders,
            }
        )
    return units


def build_document(markdown: str, fallback_title: str) -> BeautifulSoup:
    body = BeautifulSoup(markdown_to_html(markdown), "html.parser")
    sanitize(body)
    heading = body.find(["h1", "h2"])
    title = heading.get_text(" ", strip=True) if heading else fallback_title
    soup = BeautifulSoup(
        "<!DOCTYPE html>\n"
        f'<html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title>'
        f"<style>{PAGE_STYLE}{STYLE}</style>{MATHJAX}</head>"
        "<body><article></article></body></html>",
        "html.parser",
    )
    article = soup.find("article")
    assert article is not None
    article.extend(list(body.contents))
    return soup


def build_bilingual_html(
    markdown: str,
    output: Path,
    *,
    title: str,
    model: str,
    batch_size: int,
    checkpoint: Path,
    target_lang: str = DEFAULT_LANG,
) -> None:
    language_name(target_lang)  # fail fast on an unknown code, before any API call
    soup = build_document(markdown, title)
    article = soup.find("article")
    assert article is not None
    units = collect_units(article)
    print(f"found {len(units)} translation units", file=sys.stderr)
    translations = translate_units(
        units, checkpoint, model=model, batch_size=batch_size, target_lang=target_lang
    )
    apply_translations(soup, units, translations, target_lang)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(str(soup), encoding="utf-8")
    print(f"wrote {output}", file=sys.stderr)


DESCRIPTION = (
    "Translate a PDF paper into a bilingual (English/target-language) HTML page. The PDF is first "
    "converted to Markdown with LaTeX math by an external layout-analysis tool (MinerU by default, "
    "or docling), which must be installed separately."
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pdf", type=Path)
    parser.add_argument(
        "--converter",
        default=DEFAULT_CONVERTER,
        choices=sorted(CONVERTERS),
        help=f"PDF -> Markdown converter (default {DEFAULT_CONVERTER})",
    )
    parser.add_argument(
        "--converter-bin",
        default=None,
        help="path to the converter executable (default: mineru-kit / docling on PATH)",
    )
    parser.add_argument(
        "--converter-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument passed through to the converter, repeatable "
        "(e.g. --converter-arg=--ocr-mode --converter-arg=ocr for a scanned PDF with MinerU)",
    )
    parser.add_argument(
        "--reconvert",
        action="store_true",
        help="run the converter again even if work/<slug>/<converter>/ already has its output "
        "(needed after changing --converter-arg)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="default: outputs/<slug>-<lang>-bilingual.html",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="default: work/<slug>/<lang>/translations.json",
    )
    parser.add_argument("--model", default=gemini.DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument(
        "--target-lang",
        default=DEFAULT_LANG,
        choices=sorted(LANGUAGES),
        metavar="CODE",
        help=f"target language code (default {DEFAULT_LANG}); one of: {', '.join(sorted(LANGUAGES))}",
    )


def run(args: argparse.Namespace) -> None:
    try:
        pdf: Path = args.pdf
        if not pdf.is_file():
            raise ValueError(f"{pdf} is not a file")
        language_name(args.target_lang)
        slug = pdf.stem
        default_output, default_checkpoint = default_paths(slug, args.target_lang)
        markdown = convert_pdf(
            pdf,
            Path("work") / slug,
            converter=args.converter,
            command=args.converter_bin,
            extra_args=tuple(args.converter_arg),
            reconvert=args.reconvert,
        )
        build_bilingual_html(
            markdown,
            args.output or default_output,
            title=slug,
            model=args.model,
            batch_size=args.batch_size,
            checkpoint=args.checkpoint or default_checkpoint,
            target_lang=args.target_lang,
        )
    except ValueError as error:
        raise SystemExit(f"error: {error}") from None
