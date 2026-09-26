from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Paragraph:
    id: str
    page: int
    english: str
    translation: str = ""

    @property
    def source_hash(self) -> str:
        return sha256(self.english.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["source_hash"] = self.source_hash
        return value


def validate(paragraphs: list[Paragraph], *, require_translation: bool = False) -> None:
    ids = [p.id for p in paragraphs]
    if not paragraphs:
        raise ValueError("The inventory has no paragraphs.")
    if len(set(ids)) != len(ids):
        raise ValueError("Paragraph IDs must be unique.")
    for paragraph in paragraphs:
        if not paragraph.english.strip():
            raise ValueError(f"{paragraph.id}: English source is empty.")
        if require_translation and not paragraph.translation.strip():
            raise ValueError(f"{paragraph.id}: translation is empty.")
