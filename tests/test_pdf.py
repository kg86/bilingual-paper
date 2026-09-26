import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from bilingual_paper.arxiv import apply_translations
from bilingual_paper.pdf import (
    build_document,
    collect_units,
    convert_pdf,
    markdown_to_html,
)

SAMPLE = r"""
# A Sample Paper

**Ada Lovelace**<sup>1</sup>

<sup>1</sup>University of Somewhere

## Abstract

We study $\Sigma ^ { * }$ and cost \$5.

## 1 Introduction

Let $F _ { x }$ be a phrase, see [the code](https://example.com) and `p(I)`.

$$
R (i) = x \tag{1}
$$

1. First item with $a$.
2. Second item.

Figure 1: A smallest BMS for $T$.

<table><tr><td>Words in a cell</td></tr></table>

```prolog
% a comment
p(1).
```

702

## References

Storer, J. A. 1977. NP-completeness results.

## A Proofs

Appendix text.
"""


class MarkdownRenderingTests(unittest.TestCase):
    def test_math_is_wrapped_in_mathjax_delimiters_and_escaped(self):
        html = markdown_to_html("If $a < b$ then\n\n$$\nx < y\n$$\n")
        self.assertIn(r'<span class="math inline">\(a &lt; b\)</span>', html)
        self.assertIn(r"\[x &lt; y\]", html)

    def test_escaped_dollar_is_not_math(self):
        html = markdown_to_html(r"It costs \$5 and \$10.")
        self.assertNotIn("math", html)

    def test_build_document_strips_active_content(self):
        soup = build_document(
            '<p onclick="x()">Hi <a href="javascript:alert(1)">link</a></p>\n\n'
            "<script>alert(1)</script>\n",
            "fallback",
        )
        article = soup.find("article")
        assert article is not None
        self.assertIsNone(article.find("script"))
        self.assertNotIn("onclick", str(article))
        self.assertNotIn("javascript:", str(article))

    def test_build_document_takes_title_from_first_heading(self):
        soup = build_document(SAMPLE, "fallback")
        title = soup.find("title")
        assert title is not None
        self.assertEqual(title.get_text(), "A Sample Paper")


class CollectUnitsTests(unittest.TestCase):
    def units(self):
        soup = build_document(SAMPLE, "fallback")
        article = soup.find("article")
        assert article is not None
        return soup, collect_units(article)

    def test_translates_prose_and_skips_code_tables_numbers_front_matter_and_references(
        self,
    ):
        _, units = self.units()
        self.assertEqual(
            [u["english"] for u in units],
            [
                "A Sample Paper",
                "Abstract",
                r"We study @@0@@ and cost $5.",
                "1 Introduction",
                "Let @@0@@ be a phrase, see @@1@@ and @@2@@.",
                "First item with @@0@@.",
                "Second item.",
                "Figure 1: A smallest BMS for @@0@@.",
                "References",
                "A Proofs",
                "Appendix text.",
            ],
        )

    def test_math_links_and_code_become_placeholders(self):
        _, units = self.units()
        intro = units[4]
        self.assertEqual(
            list(intro["placeholders"].values()),
            [
                r'<span class="math inline">\(F _ { x }\)</span>',
                '<a href="https://example.com">the code</a>',
                "<code>p(I)</code>",
            ],
        )

    def test_classifies_headings_captions_and_paragraphs(self):
        _, units = self.units()
        by_text = {u["english"]: u["css_class"] for u in units}
        self.assertEqual(by_text["1 Introduction"], "tr-heading")
        self.assertEqual(by_text["Figure 1: A smallest BMS for @@0@@."], "tr-caption")
        self.assertEqual(by_text["Second item."], "tr-para")

    def test_front_matter_is_translated_when_there_is_no_abstract_heading(self):
        soup = build_document("# Title\n\nSome Author\n\n## Intro\n\nText.\n", "x")
        article = soup.find("article")
        assert article is not None
        self.assertIn("Some Author", [u["english"] for u in collect_units(article)])

    def test_list_item_translation_stays_inside_its_list_item(self):
        soup, units = self.units()
        apply_translations(soup, units, {u["id"]: "訳" for u in units}, "ja")
        for ol in soup.find_all("ol"):
            self.assertEqual(
                {child.name for child in ol.find_all(recursive=False)}, {"li"}
            )
        first_item = soup.find("li")
        assert first_item is not None
        self.assertIsNotNone(first_item.find("p", class_="tr-para"))


class ConvertPdfTests(unittest.TestCase):
    def test_reuses_cached_markdown_without_running_the_converter(self):
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "mineru").mkdir()
            (work / "mineru" / "paper.md").write_text("# Cached\n", encoding="utf-8")
            with mock.patch("bilingual_paper.pdf.subprocess.run") as run:
                markdown = convert_pdf(Path("paper.pdf"), work, converter="mineru")
            run.assert_not_called()
            self.assertEqual(markdown, "# Cached\n")

    def test_missing_converter_is_reported_with_install_hint(self):
        with (
            TemporaryDirectory() as tmp,
            mock.patch("bilingual_paper.pdf.shutil.which", return_value=None),
            self.assertRaisesRegex(ValueError, "uv tool install docling"),
        ):
            convert_pdf(Path("paper.pdf"), Path(tmp), converter="docling")

    def test_runs_converter_and_reads_its_markdown(self):
        with TemporaryDirectory() as tmp:
            # Stand-in for `mineru-kit parse PDF -o OUT_DIR -p all`: writes OUT_DIR/<stem>.md.
            fake = Path(tmp) / "fake-mineru"
            fake.write_text('#!/bin/sh\necho "# Converted" > "$4/paper.md"\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            markdown = convert_pdf(
                Path("paper.pdf"),
                Path(tmp) / "work",
                converter="mineru",
                command=str(fake),
            )
            self.assertEqual(markdown, "# Converted\n")

    def test_converter_failure_is_an_error(self):
        with (
            TemporaryDirectory() as tmp,
            self.assertRaisesRegex(ValueError, "exit code 1"),
        ):
            convert_pdf(
                Path("paper.pdf"), Path(tmp), converter="mineru", command="false"
            )


if __name__ == "__main__":
    unittest.main()
