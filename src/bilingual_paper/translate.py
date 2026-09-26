from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from . import gemini
from .io import load_target_lang, save
from .languages import DEFAULT_LANG, language_name
from .models import Paragraph, validate


SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "translation": {"type": "string"}},
                "required": ["id", "translation"],
            },
        }
    },
    "required": ["translations"],
}


def build_instructions(target_lang: str) -> str:
    name = language_name(target_lang)
    return (
        f"Translate every supplied English source segment completely into natural {name}. "
        "Do not summarize, omit, comment on, or rewrite the English. Preserve formulas, symbols, "
        "citations, numbering, and names. Return exactly one translation per input ID: never split one "
        "ID's segment into multiple output entries and never invent an ID that was not supplied, even if "
        "a segment reads as multiple unrelated sentences or a PDF extraction artifact — translate it as "
        f"one combined non-empty {name} text for that single ID instead. The set of output IDs must "
        "equal the set of input IDs exactly, with no additions, omissions, or duplicates."
    )


def apply_batch(paragraphs: list[Paragraph], translated: list[dict]) -> list[Paragraph]:
    expected = {p.id for p in paragraphs}
    received = [item["id"] for item in translated]
    if len(received) != len(set(received)) or set(received) != expected:
        raise ValueError("Translation response IDs do not exactly match the requested IDs.")
    values = {item["id"]: item["translation"].strip() for item in translated}
    if any(not value for value in values.values()):
        raise ValueError("Translation response contains an empty translation.")
    return [replace(p, translation=values[p.id]) for p in paragraphs]


def resume_checkpoint(source: list[Paragraph], output: Path) -> list[Paragraph]:
    """Reuse translations without allowing a checkpoint to alter source text."""
    if not output.exists():
        return list(source)
    from .io import load

    prior = load(output)
    if len(prior) != len(source):
        raise ValueError("Translation checkpoint does not match the source inventory.")
    merged: list[Paragraph] = []
    for expected, saved in zip(source, prior, strict=True):
        if (expected.id, expected.english, expected.source_hash) != (
            saved.id,
            saved.english,
            saved.source_hash,
        ):
            raise ValueError("Translation checkpoint changed or reordered source text.")
        merged.append(replace(expected, translation=saved.translation))
    return merged


def translate_inventory(
    paragraphs: list[Paragraph], output: Path, *, model: str = gemini.DEFAULT_MODEL,
    batch_size: int = 12, target_lang: str | None = None, client=None
) -> list[Paragraph]:
    """Translate into `target_lang`, defaulting to the language already recorded in `output`
    (e.g. by `translate-template --target-lang`), then to DEFAULT_LANG."""
    validate(paragraphs)
    saved_lang = load_target_lang(output) if output.exists() else None
    target_lang = target_lang or saved_lang or DEFAULT_LANG
    instructions = build_instructions(target_lang)
    completed = resume_checkpoint(paragraphs, output)
    if saved_lang and saved_lang != target_lang and any(p.translation.strip() for p in completed):
        raise ValueError(
            f"{output} already holds {saved_lang!r} translations; refusing to mix in {target_lang!r}. "
            "Write to a different output file (or delete this one) to translate into another language."
        )
    api = client or gemini.client()

    def run(batch: list[Paragraph]) -> list[Paragraph]:
        payload = [{"id": p.id, "english": p.english} for p in batch]
        response = gemini.generate_json(
            api, model=model, instructions=instructions,
            payload=json.dumps(payload, ensure_ascii=False), schema=SCHEMA,
        )
        expected_ids = {p.id for p in batch}
        translated = gemini.merge_invented_ids(expected_ids, response["translations"], "translation")
        return apply_batch(batch, translated)

    for start in range(0, len(completed), batch_size):
        batch = completed[start : start + batch_size]
        if all(p.translation.strip() for p in batch):
            continue
        completed[start : start + batch_size] = gemini.run_with_single_fallback(batch, run)
        save(output, completed, target_lang=target_lang)
    validate(completed, require_translation=True)
    return completed
