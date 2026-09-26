from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from . import gemini
from .io import load, load_cleaned_ids, save
from .models import Paragraph, validate


SCHEMA = {
    "type": "object",
    "properties": {
        "cleaned": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "english": {"type": "string"}},
                "required": ["id", "english"],
            },
        }
    },
    "required": ["cleaned"],
}

INSTRUCTIONS = (
    "Each segment is English text extracted from a PDF's text layer, which can contain "
    "extraction artifacts: mojibake/garbled characters, missing or duplicated characters, "
    "and sentences or words split apart by line or column breaks. Reconstruct clean, readable "
    "English prose for every segment: fix garbled characters, restore obviously missing text "
    "where you can infer it with confidence, and join sentence fragments that were only broken "
    "by extraction. Preserve the actual wording, meaning, formulas, symbols, citations, numbering, "
    "and names exactly. Do not summarize, translate, paraphrase, add commentary, or drop content.\n\n"
    "Pay special attention to mathematical/scientific text: subscript, superscript, and symbol "
    "glyphs are frequently mis-decoded into unrelated ASCII characters because the PDF's font "
    "encoding table is broken for them, not just plain typos. Recognize and repair patterns like: "
    "a run of digit-like subscripts fused onto a variable letter (e.g. a corrupted 'slsZ * * * s,' "
    "or 'Sl.72 * * * s,' both mean the standard sequence notation 's1 s2 · · · sn'); a run of "
    "asterisks, hyphens, or semicolons standing in for a centered ellipsis ('· · ·', as in "
    "'i = 1, 2, . . . , m'); a trailing curly/double quote standing in for a superscript, especially "
    "Kleene-star closure or an exponent (e.g. 'A\"' meaning 'A*', 'a\"' meaning 'a^n'); and a bare "
    "capital 'E' standing in for the set-membership symbol '∈' between a variable and a set (e.g. "
    "'S E A*' meaning 'S ∈ A*'). Use the surrounding sentence and the paper's own consistent usage "
    "to decide the correct reading — the same corrupted pattern should be resolved the same way "
    "throughout a passage — and never guess a reading that changes the mathematical meaning; leave "
    "text as-is if you are not reasonably confident how to fix it.\n\n"
    "Return exactly one output entry per input ID: never split one ID's segment into multiple output "
    "entries and never invent an ID that was not supplied, even if a segment reads as multiple "
    "unrelated fragments — return it as one combined non-empty entry for that single ID instead. "
    "The set of output IDs must equal the set of input IDs exactly, with no additions, omissions, "
    "or duplicates."
)


def apply_batch(paragraphs: list[Paragraph], cleaned: list[dict]) -> list[Paragraph]:
    expected = {p.id for p in paragraphs}
    received = [item["id"] for item in cleaned]
    if len(received) != len(set(received)) or set(received) != expected:
        raise ValueError("Cleanup response IDs do not exactly match the requested IDs.")
    values = {item["id"]: item["english"].strip() for item in cleaned}
    if any(not value for value in values.values()):
        raise ValueError("Cleanup response contains an empty segment.")
    return [replace(p, english=values[p.id]) for p in paragraphs]


def resume_checkpoint(source: list[Paragraph], output: Path) -> tuple[list[Paragraph], set[str]]:
    """Reuse already-cleaned segments; a checkpoint may only alter the English text.

    Returns the resumed paragraphs and the IDs already cleaned. Segments the model returned
    unchanged are tracked by ID so they are not re-sent on every run.
    """
    if not output.exists():
        return list(source), set()
    prior = load(output)
    if len(prior) != len(source) or [p.id for p in prior] != [p.id for p in source]:
        raise ValueError("Cleanup checkpoint does not match the source inventory.")
    cleaned = load_cleaned_ids(output)
    if cleaned is None:  # checkpoint written before cleaned_ids was recorded
        cleaned = {c.id for c, s in zip(prior, source, strict=True) if c.english != s.english}
    return list(prior), cleaned


def clean_inventory(
    paragraphs: list[Paragraph], output: Path, *, model: str = gemini.DEFAULT_MODEL,
    batch_size: int = 12, client=None
) -> list[Paragraph]:
    validate(paragraphs)
    completed, cleaned_ids = resume_checkpoint(paragraphs, output)
    api = client or gemini.client()

    def run(batch: list[Paragraph]) -> list[Paragraph]:
        payload = [{"id": p.id, "english": p.english} for p in batch]
        response = gemini.generate_json(
            api, model=model, instructions=INSTRUCTIONS,
            payload=json.dumps(payload, ensure_ascii=False), schema=SCHEMA,
        )
        expected_ids = {p.id for p in batch}
        cleaned = gemini.merge_invented_ids(expected_ids, response["cleaned"], "english")
        return apply_batch(batch, cleaned)

    for start in range(0, len(completed), batch_size):
        source_batch = paragraphs[start : start + batch_size]
        if all(p.id in cleaned_ids for p in source_batch):
            continue
        completed[start : start + batch_size] = gemini.run_with_single_fallback(source_batch, run)
        cleaned_ids.update(p.id for p in source_batch)
        save(output, completed, cleaned_ids=cleaned_ids)
    validate(completed)
    return completed
