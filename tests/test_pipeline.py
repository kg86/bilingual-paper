import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

from bilingual_paper.audit import audit, audit_source
from bilingual_paper.clean import apply_batch as apply_clean_batch
from bilingual_paper.clean import clean_inventory
from bilingual_paper.clean import resume_checkpoint as resume_clean_checkpoint
from bilingual_paper.gemini import generate_json, merge_invented_ids, run_with_single_fallback
from bilingual_paper.html import render
from bilingual_paper.inventory import paragraphize_plain_text, split_two_column_layout
from bilingual_paper.io import load, load_target_lang, save
from bilingual_paper.models import Paragraph
from bilingual_paper.translate import apply_batch, resume_checkpoint, translate_inventory


class FakeClient:
    """Stands in for genai.Client: `reply(payload_items)` returns the response dict (or raw text)."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[list[dict]] = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        items = json.loads(contents) if contents.startswith("[") else contents
        self.calls.append(items)
        result = self.reply(items)
        return SimpleNamespace(text=result if isinstance(result, str) or result is None else json.dumps(result))


class PipelineTests(unittest.TestCase):
    def test_renderer_preserves_source_and_audit_passes(self):
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            source = [Paragraph("p01-001", 1, "A prefix Q is proper.", "接頭辞 Q は真である。")]
            inventory = tmp_path / "source.json"; html = tmp_path / "reader.html"
            save(inventory, source)
            render(load(inventory, require_translation=True), html)
            self.assertIn("A prefix Q is proper.", html.read_text(encoding="utf-8"))
            self.assertEqual(audit(source, html), [])

    def test_audit_detects_omitted_pair(self):
        with TemporaryDirectory() as directory:
            html = Path(directory) / "broken.html"; html.write_text("<html></html>", encoding="utf-8")
            self.assertTrue(audit([Paragraph("p01-001", 1, "English", "日本語")], html))

    def test_loader_rejects_missing_translation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            paragraph = Paragraph("p01-001", 1, "English", "")
            path.write_text(json.dumps({"schema":"pdf-bilingual-inventory/v1","paragraphs":[paragraph.to_dict()]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "translation is empty"):
                load(path, require_translation=True)

    def test_loader_rejects_source_mutation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            paragraph = Paragraph("p01-001", 1, "Original English", "日本語")
            save(path, [paragraph])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["paragraphs"][0]["english"] = "Rewritten English"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified after inventory"):
                load(path)

    def test_two_column_layout_uses_column_reading_order(self):
        layout = (
            "Left paragraph line one.        Right paragraph line one.\n"
            "Left paragraph line two.        Right paragraph line two.\n"
            "\n"
            "Second left paragraph.          Second right paragraph.\n"
        )
        blocks = split_two_column_layout(layout)
        self.assertIn("Left paragraph line one. Left paragraph line two.", blocks)
        self.assertIn("Right paragraph line one. Right paragraph line two.", blocks)
        self.assertLess(blocks.index("Second left paragraph."), blocks.index("Right paragraph line one. Right paragraph line two."))

    def test_plain_text_paragraphizer_keeps_every_line_once(self):
        source = "First line\ncontinues here.\nSecond paragraph.\n"
        blocks = paragraphize_plain_text(source)
        self.assertEqual(blocks, ["First line\ncontinues here.", "Second paragraph."])
        self.assertEqual("\n".join(blocks), source.strip())

    def test_translation_batch_requires_exact_ids(self):
        source = [Paragraph("p01-001", 1, "English")]
        translated = apply_batch(source, [{"id": "p01-001", "translation": "日本語"}])
        self.assertEqual(translated[0].english, "English")
        self.assertEqual(translated[0].translation, "日本語")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            apply_batch(source, [{"id": "wrong", "translation": "日本語"}])

    def test_resume_checkpoint_rejects_changed_source(self):
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "translated.json"
            save(checkpoint, [Paragraph("p01-001", 1, "Changed", "訳")])
            with self.assertRaisesRegex(ValueError, "changed or reordered"):
                resume_checkpoint([Paragraph("p01-001", 1, "Original")], checkpoint)

    def test_clean_batch_requires_exact_ids(self):
        source = [Paragraph("p01-001", 1, "Gar bled te xt")]
        cleaned = apply_clean_batch(source, [{"id": "p01-001", "english": "Garbled text"}])
        self.assertEqual(cleaned[0].english, "Garbled text")
        with self.assertRaisesRegex(ValueError, "exactly match"):
            apply_clean_batch(source, [{"id": "wrong", "english": "Garbled text"}])

    def test_merge_invented_ids_folds_extra_entry_into_predecessor(self):
        entries = [
            {"id": "p05-024", "translation": "前半の訳。"},
            {"id": "p05-025", "translation": "後半の訳。"},
            {"id": "p06-001", "translation": "次の段落。"},
        ]
        merged = merge_invented_ids({"p05-024", "p06-001"}, entries, "translation")
        self.assertEqual([e["id"] for e in merged], ["p05-024", "p06-001"])
        self.assertEqual(merged[0]["translation"], "前半の訳。 後半の訳。")

    def test_clean_resume_checkpoint_reuses_cleaned_text(self):
        with TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "cleaned.json"
            save(checkpoint, [Paragraph("p01-001", 1, "Fixed text")])
            resumed, cleaned_ids = resume_clean_checkpoint([Paragraph("p01-001", 1, "Gar bled")], checkpoint)
            self.assertEqual(resumed[0].english, "Fixed text")
            self.assertEqual(cleaned_ids, {"p01-001"})

    def test_clean_does_not_resend_segments_the_model_left_unchanged(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "cleaned.json"
            source = [Paragraph("p01-001", 1, "Already clean.")]
            echo = FakeClient(lambda items: {"cleaned": items})
            clean_inventory(source, output, client=echo)
            clean_inventory(source, output, client=echo)
            self.assertEqual(len(echo.calls), 1)

    def test_translate_defaults_to_language_recorded_by_template(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "work.json"
            source = [Paragraph("p01-001", 1, "Hello.")]
            save(output, source, target_lang="ko")
            fake = FakeClient(lambda items: {"translations": [{"id": i["id"], "translation": "안녕"} for i in items]})
            translate_inventory(source, output, client=fake)
            self.assertEqual(load_target_lang(output), "ko")

    def test_translate_refuses_to_mix_languages_in_one_checkpoint(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "work.json"
            save(output, [Paragraph("p01-001", 1, "Hello.", "こんにちは")], target_lang="ja")
            with self.assertRaisesRegex(ValueError, "refusing to mix"):
                translate_inventory([Paragraph("p01-001", 1, "Hello.")], output, target_lang="ko", client=FakeClient(None))

    def test_translate_falls_back_to_single_items_when_batch_drops_an_id(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "work.json"
            source = [Paragraph("p01-001", 1, "One."), Paragraph("p01-002", 1, "Two.")]
            # Multi-item requests lose the second ID; single-item requests succeed.
            fake = FakeClient(lambda items: {"translations": [{"id": items[0]["id"], "translation": "訳"}]})
            result = translate_inventory(source, output, client=fake)
            self.assertEqual([p.translation for p in result], ["訳", "訳"])
            self.assertEqual([len(c) for c in fake.calls], [2, 1, 1])

    def test_run_with_single_fallback_gives_up_after_repeated_failures(self):
        def run(batch):
            raise ValueError("bad")
        with self.assertRaisesRegex(ValueError, "bad"):
            run_with_single_fallback(["a"], run, attempts=2)

    def test_generate_json_retries_empty_response(self):
        replies = iter([None, {"ok": True}])
        fake = FakeClient(lambda items: next(replies))
        self.assertEqual(generate_json(fake, model="m", instructions="i", payload="[]", schema={}, backoff=0), {"ok": True})

    def test_renderer_sets_rtl_direction_for_arabic(self):
        with TemporaryDirectory() as directory:
            html = Path(directory) / "reader.html"
            render([Paragraph("p01-001", 1, "Hello.", "مرحبا")], html, target_lang="ar")
            self.assertIn('lang="ar" dir="rtl"', html.read_text(encoding="utf-8"))
