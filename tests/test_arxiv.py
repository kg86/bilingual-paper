import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup

from bilingual_paper.arxiv import (
    CHECKPOINT_SCHEMA,
    apply_translations,
    collect_units,
    default_paths,
    detect_arxiv_id_in_page,
    extract_segment,
    find_arxiv_id,
    load_checkpoint,
    missing_tokens,
    reconstruct,
    resolve_source,
    save_checkpoint,
)

SAMPLE = """
<article>
  <h1 class="ltx_title_document">A Sample Paper</h1>
  <h2 class="ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
  <div class="ltx_para">
    <p class="ltx_p">Let <math alttext="n"><mi>n</mi></math> be a positive integer, see
    <a class="ltx_ref" href="#S1">Section 1</a> and <cite class="ltx_cite">[1]</cite>.</p>
  </div>
  <figure>
    <figcaption>Figure 1: An example with <math alttext="d"><mi>d</mi></math>.</figcaption>
  </figure>
</article>
"""


class HtmlPaperTests(unittest.TestCase):
    def test_find_arxiv_id_from_various_forms(self):
        self.assertEqual(find_arxiv_id("2307.01412"), "2307.01412")
        self.assertEqual(find_arxiv_id("https://arxiv.org/abs/2307.01412v4"), "2307.01412v4")
        self.assertEqual(find_arxiv_id("arXiv:2307.01412v4 [cs.DS] 15 Jul 2026"), "2307.01412v4")
        self.assertIsNone(find_arxiv_id("no id here"))

    def test_resolve_source_rejects_unrecognized_string(self):
        with self.assertRaisesRegex(ValueError, "arXiv URL/ID"):
            resolve_source("not-a-real-path-or-id")

    def test_extract_segment_protects_math_refs_and_citations_as_opaque_tokens(self):
        soup = BeautifulSoup(SAMPLE, "html.parser")
        p = soup.find("p")
        text, placeholders = extract_segment(p)
        self.assertEqual(text, "Let @@0@@ be a positive integer, see @@1@@ and @@2@@.")
        self.assertEqual(set(placeholders), {"@@0@@", "@@1@@", "@@2@@"})
        self.assertIn("<math", placeholders["@@0@@"])
        self.assertIn('href="#S1"', placeholders["@@1@@"])
        self.assertIn("ltx_cite", placeholders["@@2@@"])

    def test_extract_segment_keeps_heading_number_tag_opaque(self):
        soup = BeautifulSoup(SAMPLE, "html.parser")
        h2 = soup.find("h2")
        text, placeholders = extract_segment(h2)
        self.assertEqual(text, "@@0@@Introduction")
        self.assertIn("ltx_tag_section", placeholders["@@0@@"])

    def test_collect_units_covers_title_heading_paragraph_and_caption(self):
        soup = BeautifulSoup(SAMPLE, "html.parser")
        units = collect_units(soup)
        classes = [u["css_class"] for u in units]
        self.assertEqual(classes, ["tr-heading", "tr-heading", "tr-para", "tr-caption"])
        self.assertEqual(units[0]["english"], "A Sample Paper")

    def test_missing_tokens_ignored_for_headings_but_enforced_for_paragraphs(self):
        heading_unit = {"css_class": "tr-heading", "placeholders": {"@@0@@": "<x/>"}}
        para_unit = {"css_class": "tr-para", "placeholders": {"@@0@@": "<x/>"}}
        self.assertEqual(missing_tokens(heading_unit, "no token here"), [])
        self.assertEqual(missing_tokens(para_unit, "no token here"), ["@@0@@"])
        self.assertEqual(missing_tokens(para_unit, "has @@0@@ here"), [])

    def test_reconstruct_reinserts_placeholder_html_and_escapes_the_rest(self):
        fragment = reconstruct("n は @@0@@ とする <b>", {"@@0@@": "<math>X</math>"})
        rendered = str(fragment)
        self.assertIn("<math>X</math>", rendered)
        self.assertIn("&lt;b&gt;", rendered)

    def test_detect_arxiv_id_in_page_prefers_watermark_over_cited_ids(self):
        page = (
            '<html><body><div id="watermark-tr">\narXiv:2307.01412v4 [cs.DS] 15 Jul 2026</div>'
            "<article><p>See arXiv:1234.56789 for details.</p></article></body></html>"
        )
        self.assertEqual(detect_arxiv_id_in_page(page), "2307.01412v4")

    def test_detect_arxiv_id_in_page_ignores_ids_only_cited_in_the_article(self):
        page = "<html><body><article><p>Version 2024.12345 of arXiv:1234.56789</p></article></body></html>"
        self.assertIsNone(detect_arxiv_id_in_page(page))

    def test_collect_units_keeps_lead_in_paragraph_before_nested_list(self):
        html = """<article><div class="ltx_para"><p class="ltx_p">We have the following:</p>
          <ul class="ltx_itemize"><li class="ltx_item"><div class="ltx_para"><p class="ltx_p">first item</p>
          </div></li></ul></div></article>"""
        units = collect_units(BeautifulSoup(html, "html.parser"))
        self.assertEqual([u["english"] for u in units], ["We have the following:", "first item"])

    def test_footnote_is_kept_out_of_its_sentence_and_translated_separately(self):
        html = """<article><div class="ltx_para"><p class="ltx_p">Main text<span class="ltx_note ltx_role_footnote">
          <sup class="ltx_note_mark">1</sup><span class="ltx_note_outer"><span class="ltx_note_content">
          <sup class="ltx_note_mark">1</sup><span class="ltx_tag ltx_tag_note">1</span>Footnote body here.</span>
          </span></span> continues.</p></div></article>"""
        units = collect_units(BeautifulSoup(html, "html.parser"))
        self.assertEqual(units[0]["english"], "Main text@@0@@ continues.")
        self.assertIn("ltx_note", units[0]["placeholders"]["@@0@@"])
        self.assertEqual(units[1]["css_class"], "tr-note")
        self.assertEqual(TOKEN_FREE(units[1]["english"]), "Footnote body here.")

    def test_collect_units_rejects_pages_without_article(self):
        with self.assertRaisesRegex(ValueError, "<article>"):
            collect_units(BeautifulSoup("<html><body><p>hi</p></body></html>", "html.parser"))

    def test_default_paths_are_per_language(self):
        ja_output, ja_checkpoint = default_paths("2307.01412", "ja")
        ko_output, ko_checkpoint = default_paths("2307.01412", "ko")
        self.assertNotEqual(ja_output, ko_output)
        self.assertNotEqual(ja_checkpoint, ko_checkpoint)
        self.assertEqual(ko_checkpoint, Path("work/2307.01412/ko/translations.json"))

    def test_checkpoint_round_trip_rejects_other_language_and_drops_stale_entries(self):
        units = [{"id": "u0001", "english": "Hello"}, {"id": "u0002", "english": "World"}]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "translations.json"
            save_checkpoint(path, units, {"u0001": "こんにちは", "u0002": "世界"}, "ja")
            self.assertEqual(load_checkpoint(units, path, "ja"), {"u0001": "こんにちは", "u0002": "世界"})
            with self.assertRaisesRegex(ValueError, "'ja' translations, not 'ko'"):
                load_checkpoint(units, path, "ko")
            changed = [{"id": "u0001", "english": "Hello"}, {"id": "u0002", "english": "Changed"}]
            self.assertEqual(load_checkpoint(changed, path, "ja"), {"u0001": "こんにちは"})

    def test_checkpoint_rejects_legacy_flat_format(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "translations.json"
            path.write_text(json.dumps({"u0001": "訳"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, CHECKPOINT_SCHEMA):
                load_checkpoint([{"id": "u0001", "english": "x"}], path, "ja")

    def test_apply_translations_marks_language_and_direction(self):
        soup = BeautifulSoup(SAMPLE, "html.parser")
        units = collect_units(soup)
        apply_translations(soup, units, {u["id"]: "نص" for u in units}, "ar")
        inserted = soup.find_all(class_="tr-para")
        self.assertEqual(len(inserted), 1)
        self.assertEqual((inserted[0]["lang"], inserted[0]["dir"]), ("ar", "rtl"))


def TOKEN_FREE(text: str) -> str:
    return re.sub(r"@@\d+@@", "", text).strip()


if __name__ == "__main__":
    unittest.main()
