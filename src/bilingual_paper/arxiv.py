"""`bilingual-paper arxiv`: build a bilingual (English/target-language) HTML page from an arXiv HTML paper.

The source is already well-structured HTML (LaTeXML output), so paragraphs, headings and
captions are extracted straight from the DOM, including inline MathML. Each translatable
text unit is translated once via Gemini (with math/citations/reference-numbers protected as
opaque placeholder tokens) and the translated result is inserted directly after its English
counterpart in the original document, so all original structure/CSS/MathML is preserved.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from . import gemini
from .languages import DEFAULT_LANG, LANGUAGES, language_name, text_direction

TOKEN_RE = re.compile(r"@@(\d+)@@")  # @@N@@
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
# The paper's own ID in a saved arXiv HTML page: the "arXiv:<id> [cs.XX] <date>" watermark, or an
# arxiv.org link. Only the part before <article> is searched, so IDs cited in the body are ignored.
WATERMARK_ID_RE = re.compile(
    r'id="watermark-tr"[^>]*>\s*arXiv:(\d{4}\.\d{4,5}(?:v\d+)?)'
)
PAGE_ID_RE = re.compile(
    r"(?:arXiv:|arxiv\.org/(?:abs|html|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE
)
ARXIV_HTML_BASE_HREF = "https://arxiv.org/html/"
CHECKPOINT_SCHEMA = "arxiv-bilingual-translations/v1"
# Units whose placeholder tokens are cosmetic numbering (section numbers, footnote marks) that the
# English original right next to the translation already shows; dropping them loses nothing.
COSMETIC_TOKEN_CLASSES = ("tr-heading", "tr-note")


def find_arxiv_id(text: str) -> str | None:
    match = ARXIV_ID_RE.search(text)
    return match.group(0) if match else None


def detect_arxiv_id_in_page(html_text: str) -> str | None:
    """Find the paper's own arXiv ID in a saved arXiv HTML page (not IDs it merely cites)."""
    match = WATERMARK_ID_RE.search(html_text)
    if match:
        return match.group(1)
    article_start = html_text.find("<article")
    match = PAGE_ID_RE.search(
        html_text if article_start < 0 else html_text[:article_start]
    )
    return match.group(1) if match else None


def fetch_arxiv_html(arxiv_id: str) -> str:
    url = f"{ARXIV_HTML_BASE_HREF}{arxiv_id}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "bilingual-paper/1.0 (research tool)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError(
                f"arXiv has no HTML version of {arxiv_id} ({url} returned 404). HTML is only generated for "
                "papers arXiv could convert from LaTeX; this tool cannot translate papers without one."
            ) from None
        raise


def resolve_source(source: str) -> tuple[str, str, str | None]:
    """Return (html_text, slug, base_href) for a local file path, an arXiv URL, or a bare arXiv ID."""
    path = Path(source)
    if path.exists():
        html_text = path.read_text(encoding="utf-8")
        arxiv_id = detect_arxiv_id_in_page(html_text)
        slug = arxiv_id or path.stem
        base_href = ARXIV_HTML_BASE_HREF if arxiv_id else None
        return html_text, slug, base_href

    arxiv_id = find_arxiv_id(source)
    if arxiv_id is None:
        raise ValueError(
            f"{source!r} is neither an existing file nor a recognizable arXiv URL/ID "
            "(expected something like 2307.01412, 2307.01412v4, or an arxiv.org/abs|pdf|html/... URL)."
        )
    html_text = fetch_arxiv_html(arxiv_id)
    return html_text, arxiv_id, ARXIV_HTML_BASE_HREF


def is_opaque(tag: Tag) -> bool:
    if tag.name in ("math", "cite", "a"):
        return True
    classes = tag.get("class") or []
    # Footnotes (ltx_note) are inline spans holding the whole footnote body; keep them out of the
    # surrounding sentence -- their content is translated as its own unit.
    return (
        "ltx_note" in classes
        or "ltx_note_mark" in classes
        or any("ltx_tag" in c for c in classes)
    )


def strip_ids(tag: Tag) -> str:
    clone = copy.copy(tag)
    if clone.has_attr("id"):
        del clone["id"]
    for descendant in clone.find_all(True):
        if descendant.has_attr("id"):
            del descendant["id"]
    return str(clone)


def extract_segment(tag: Tag) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}
    counter = [0]

    def walk(node) -> str:
        parts: list[str] = []
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif is_opaque(child):
                token = f"@@{counter[0]}@@"
                counter[0] += 1
                placeholders[token] = strip_ids(child)
                parts.append(token)
            else:
                parts.append(walk(child))
        return "".join(parts)

    text = re.sub(r"\s+", " ", walk(tag)).strip()
    return text, placeholders


def collect_units(soup: BeautifulSoup) -> list[dict]:
    article = soup.find("article")
    if article is None:
        raise ValueError(
            "No <article> element found; is this an arXiv (LaTeXML) HTML paper page?"
        )
    units: list[dict] = []
    seen: set[int] = set()

    def add(tag: Tag, css_class: str) -> None:
        if id(tag) in seen:
            return
        seen.add(id(tag))
        text, placeholders = extract_segment(tag)
        if not TOKEN_RE.sub("", text).strip():
            return
        units.append(
            {
                "id": f"u{len(units) + 1:04d}",
                "anchor": tag,
                "css_class": css_class,
                "english": text,
                "placeholders": placeholders,
            }
        )

    title = article.find("h1", class_="ltx_title_document")
    if title:
        add(title, "tr-heading")

    for tag in article.find_all(["h2", "h3", "h4"]):
        add(tag, "tr-heading")

    abstract = article.find("div", class_="ltx_abstract")
    if abstract:
        for p in abstract.find_all("p", class_="ltx_p", recursive=False):
            add(p, "tr-para")

    # Only direct <p> children, so a paragraph that wraps a list/theorem (whose items are nested
    # ltx_para divs of their own) contributes its lead-in text without duplicating the items.
    for para_div in article.find_all("div", class_="ltx_para"):
        for p in para_div.find_all("p", class_="ltx_p", recursive=False):
            add(p, "tr-para")

    for figcaption in article.find_all("figcaption"):
        add(figcaption, "tr-caption")

    for note_content in article.find_all("span", class_="ltx_note_content"):
        add(note_content, "tr-note")

    return units


def load_checkpoint(
    units: list[dict], checkpoint_path: Path, target_lang: str
) -> dict[str, str]:
    """Return {unit id: translation} for saved translations whose English still matches the unit."""
    if not checkpoint_path.exists():
        return {}
    saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if saved.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError(
            f"{checkpoint_path} is not a {CHECKPOINT_SCHEMA} checkpoint (probably written by an older "
            "version that did not record the language or source text); delete it or pass another --checkpoint."
        )
    if saved["target_lang"] != target_lang:
        raise ValueError(
            f"{checkpoint_path} holds {saved['target_lang']!r} translations, not {target_lang!r}; "
            "pass a different --checkpoint for each language."
        )
    english_by_id = {u["id"]: u["english"] for u in units}
    done: dict[str, str] = {}
    stale = 0
    for unit_id, entry in saved["units"].items():
        if english_by_id.get(unit_id) == entry["english"]:
            done[unit_id] = entry["translation"]
        else:
            stale += 1
    if stale:
        print(
            f"warning: ignoring {stale} saved translation(s) whose English source no longer matches",
            file=sys.stderr,
        )
    return done


def save_checkpoint(
    checkpoint_path: Path, units: list[dict], done: dict[str, str], target_lang: str
) -> None:
    payload = {
        "schema": CHECKPOINT_SCHEMA,
        "target_lang": target_lang,
        "units": {
            u["id"]: {"english": u["english"], "translation": done[u["id"]]}
            for u in units
            if u["id"] in done
        },
    }
    checkpoint_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_instructions(target_lang: str) -> str:
    name = language_name(target_lang)
    return (
        f"Translate every supplied English segment completely into natural {name} suitable for a "
        "computer science research paper. Do not summarize, omit, or comment on the English. "
        "Each segment may contain opaque placeholder tokens of the exact ASCII form @@N@@ (a decimal "
        "number N between double at-signs, e.g. @@0@@, @@12@@). Copy every placeholder token byte-for-byte, unchanged, "
        "into the translated output, in whatever position is grammatically correct for the "
        f"{name} sentence -- never translate, alter, remove, merge, or invent a placeholder token. Return "
        "exactly one translation per input ID: never split one ID's segment into multiple output "
        "entries and never invent an ID that was not supplied. The set of output IDs must equal the "
        "set of input IDs exactly, with no additions, omissions, or duplicates."
    )


SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "translation": {"type": "string"},
                },
                "required": ["id", "translation"],
            },
        }
    },
    "required": ["translations"],
}


def missing_tokens(unit: dict, translation: str) -> list[str]:
    # Heading numeral tags (e.g. "4.5") and footnote marks are cosmetic: the numbered English
    # original sits directly above the translation, so a model dropping that token there is
    # harmless, not a lost equation/citation -- only enforce full token preservation elsewhere.
    if unit["css_class"] in COSMETIC_TOKEN_CLASSES:
        return []
    return [token for token in unit["placeholders"] if token not in translation]


def retry_instructions(target_lang: str) -> str:
    return (
        build_instructions(target_lang)
        + " Your previous attempt at this exact segment dropped one or more placeholder tokens. "
        "Re-translate it and make certain every @@N@@ token present in the input also appears, "
        "verbatim, somewhere in your output."
    )


def translate_single(
    api, model: str, unit: dict, target_lang: str, attempts: int = 3
) -> str:
    translation = ""
    for _ in range(attempts):
        payload = [{"id": unit["id"], "english": unit["english"]}]
        response = gemini.generate_json(
            api,
            model=model,
            instructions=retry_instructions(target_lang),
            payload=json.dumps(payload, ensure_ascii=False),
            schema=SCHEMA,
        )
        matched = [t for t in response["translations"] if t["id"] == unit["id"]]
        if not matched:
            continue
        translation = matched[0]["translation"].strip()
        if not missing_tokens(unit, translation):
            return translation
    gone = missing_tokens(unit, translation)
    if gone:
        # Last resort: append the untranslated placeholder content so nothing is silently lost.
        translation = (translation + " " + " ".join(gone)).strip()
        print(
            f"warning: {unit['id']} kept placeholder token(s) {gone} unmerged after retries",
            file=sys.stderr,
        )
    return translation


def translate_units(
    units: list[dict],
    checkpoint_path: Path,
    *,
    model: str,
    batch_size: int,
    target_lang: str,
) -> dict[str, str]:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_checkpoint(units, checkpoint_path, target_lang)
    instructions = build_instructions(target_lang)
    pending = [u for u in units if u["id"] not in done]
    api = gemini.client() if pending else None
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        payload = [{"id": u["id"], "english": u["english"]} for u in batch]
        assert api is not None
        response = gemini.generate_json(
            api,
            model=model,
            instructions=instructions,
            payload=json.dumps(payload, ensure_ascii=False),
            schema=SCHEMA,
        )
        expected_ids = {u["id"] for u in batch}
        translated = gemini.merge_invented_ids(
            expected_ids, response["translations"], "translation"
        )
        received = {item["id"] for item in translated}
        by_result_id = {item["id"]: item["translation"].strip() for item in translated}
        for unit in batch:
            translation = by_result_id.get(unit["id"], "")
            if (
                unit["id"] not in received
                or not translation
                or missing_tokens(unit, translation)
            ):
                translation = translate_single(api, model, unit, target_lang)
            done[unit["id"]] = translation
        save_checkpoint(checkpoint_path, units, done, target_lang)
        print(
            f"translated {min(start + batch_size, len(pending))}/{len(pending)}",
            file=sys.stderr,
        )
    missing = [
        u["id"] for u in units if u["id"] not in done or not done[u["id"]].strip()
    ]
    if missing:
        raise ValueError(f"Missing translations for: {missing}")
    return done


def reconstruct(translated_text: str, placeholders: dict[str, str]) -> BeautifulSoup:
    # re.split with a capturing group alternates [text, digit, text, digit, ..., text]
    chunks = TOKEN_RE.split(translated_text)
    html_parts = [
        escape(chunk) if i % 2 == 0 else placeholders.get(f"@@{chunk}@@", "")
        for i, chunk in enumerate(chunks)
    ]
    return BeautifulSoup("".join(html_parts), "html.parser")


def apply_translations(
    soup: BeautifulSoup,
    units: list[dict],
    translations: dict[str, str],
    target_lang: str,
) -> None:
    attrs = {"lang": target_lang, "dir": text_direction(target_lang)}
    for unit in units:
        translation = translations[unit["id"]]
        fragment = reconstruct(translation, unit["placeholders"])
        anchor = unit["anchor"]
        if anchor.name == "figcaption":
            tag_name = "figcaption"
        elif unit["css_class"] == "tr-note":
            tag_name = "span"  # footnote bodies live inside inline <span>s
        else:
            tag_name = "p"
        new_tag = soup.new_tag(tag_name, attrs={"class": unit["css_class"], **attrs})
        new_tag.append(fragment)
        anchor.insert_after(new_tag)


STYLE = """
.tr-para, .tr-heading, .tr-caption, .tr-note {
  font-family: "Noto Sans", sans-serif;
  color: #1a3a2a;
  background: #f2f8f4;
  border-inline-start: 3px solid #4c8c6b;
  padding: .5em .75em;
  margin: .35em 0 .9em;
  border-start-end-radius: .25rem;
  border-end-end-radius: .25rem;
}
/* Per-language CJK stacks: a single mixed stack would render Chinese with Japanese glyph shapes. */
:is(.tr-para, .tr-heading, .tr-caption, .tr-note):lang(ja) {
  font-family: "Hiragino Sans", "Yu Gothic", "Noto Sans JP", sans-serif;
}
:is(.tr-para, .tr-heading, .tr-caption, .tr-note):lang(ko) {
  font-family: "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
}
:is(.tr-para, .tr-heading, .tr-caption, .tr-note):lang(zh-Hans) {
  font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif;
}
:is(.tr-para, .tr-heading, .tr-caption, .tr-note):lang(zh-Hant) {
  font-family: "PingFang TC", "Microsoft JhengHei", "Noto Sans TC", sans-serif;
}
.tr-heading {
  font-weight: 700;
  background: #eaf4ee;
  border-inline-start-color: #2f6b4a;
}
.tr-caption {
  font-size: .95em;
}
.tr-note {
  display: block;
  margin: .25em 0;
  padding: .25em .5em;
}
"""


def build_bilingual_html(
    html_text: str,
    output: Path,
    *,
    base_href: str | None,
    model: str,
    batch_size: int,
    checkpoint: Path,
    target_lang: str = DEFAULT_LANG,
) -> None:
    language_name(target_lang)  # fail fast on an unknown code, before any API call
    soup = BeautifulSoup(html_text, "html.parser")

    head = soup.find("head")
    if head is None:
        head = soup.new_tag("head")
        (soup.find("html") or soup).insert(0, head)
    if base_href:
        base_tag = soup.new_tag("base", href=base_href)
        head.insert(0, base_tag)
    else:
        print(
            "warning: no base href determined; relative CSS/image links may not resolve",
            file=sys.stderr,
        )
    style_tag = soup.new_tag("style")
    style_tag.string = STYLE
    head.append(style_tag)

    units = collect_units(soup)
    print(f"found {len(units)} translation units", file=sys.stderr)
    translations = translate_units(
        units, checkpoint, model=model, batch_size=batch_size, target_lang=target_lang
    )
    apply_translations(soup, units, translations, target_lang)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(str(soup), encoding="utf-8")
    print(f"wrote {output}", file=sys.stderr)


def default_paths(slug: str, target_lang: str) -> tuple[Path, Path]:
    """(output, checkpoint) defaults; both are per-language so runs in different languages never collide."""
    return (
        Path("outputs") / f"{slug}-{target_lang}-bilingual.html",
        Path("work") / slug / target_lang / "translations.json",
    )


DESCRIPTION = (
    "Translate an arXiv HTML paper into a bilingual (English/target-language) HTML page. "
    "SOURCE may be a local arXiv HTML file, an arxiv.org/abs|pdf|html/... URL, or a bare "
    "arXiv ID (e.g. 2307.01412 or 2307.01412v4) -- the latter two are fetched directly from arXiv."
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source")
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
    parser.add_argument(
        "--base-href",
        default=None,
        help="override the auto-detected <base href> for relative assets",
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
        html_text, slug, detected_base_href = resolve_source(args.source)
        default_output, default_checkpoint = default_paths(slug, args.target_lang)
        build_bilingual_html(
            html_text,
            args.output or default_output,
            base_href=args.base_href or detected_base_href,
            model=args.model,
            batch_size=args.batch_size,
            checkpoint=args.checkpoint or default_checkpoint,
            target_lang=args.target_lang,
        )
    except ValueError as error:
        raise SystemExit(f"error: {error}") from None
