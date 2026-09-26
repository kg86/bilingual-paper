from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .languages import DEFAULT_LANG
from .models import Paragraph, validate


def load(path: Path, *, require_translation: bool = False) -> list[Paragraph]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != "pdf-bilingual-inventory/v1":
        raise ValueError("Unsupported inventory schema.")
    paragraphs: list[Paragraph] = []
    for item in raw["paragraphs"]:
        fields = {key: item.get(key, "") for key in ("id", "page", "english")}
        # "japanese" is the pre-generalization key name; keep reading it for old checkpoints.
        fields["translation"] = item.get("translation", item.get("japanese", ""))
        paragraph = Paragraph(**fields)
        recorded_hash = item.get("source_hash")
        if not recorded_hash:
            raise ValueError(f"{paragraph.id}: source_hash is missing.")
        if recorded_hash != paragraph.source_hash:
            raise ValueError(f"{paragraph.id}: English source was modified after inventory creation.")
        paragraphs.append(paragraph)
    validate(paragraphs, require_translation=require_translation)
    return paragraphs


def load_target_lang(path: Path) -> str:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("target_lang", DEFAULT_LANG)


def load_cleaned_ids(path: Path) -> set[str] | None:
    """IDs `clean` has already processed, or None for files written before this was recorded."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    cleaned = raw.get("cleaned_ids")
    return None if cleaned is None else set(cleaned)


def save(
    path: Path, paragraphs: list[Paragraph], *, target_lang: str = DEFAULT_LANG,
    cleaned_ids: Iterable[str] | None = None,
) -> None:
    validate(paragraphs)
    payload = {
        "schema": "pdf-bilingual-inventory/v1",
        "target_lang": target_lang,
        "paragraphs": [paragraph.to_dict() for paragraph in paragraphs],
    }
    if cleaned_ids is not None:
        payload["cleaned_ids"] = sorted(cleaned_ids)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
