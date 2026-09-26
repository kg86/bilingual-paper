from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader

from .models import Paragraph, validate


def audit(paragraphs: list[Paragraph], html: Path) -> list[str]:
    validate(paragraphs, require_translation=True)
    rendered = html.read_text(encoding="utf-8")
    failures: list[str] = []
    for paragraph in paragraphs:
        if rendered.count(f'id="{paragraph.id}"') != 1:
            failures.append(f"{paragraph.id}: expected exactly one rendered pair")
        if rendered.count(paragraph.source_hash) != 1:
            failures.append(f"{paragraph.id}: source hash missing or duplicated")
    return failures


def audit_source(pdf: Path, paragraphs: list[Paragraph]) -> list[str]:
    """Check that every extracted PDF line occurs exactly once in the inventory."""
    validate(paragraphs)
    actual: dict[int, Counter[str]] = defaultdict(Counter)
    for paragraph in paragraphs:
        actual[paragraph.page].update(line.rstrip() for line in paragraph.english.splitlines() if line.strip())
    failures: list[str] = []
    for page_number, page in enumerate(PdfReader(str(pdf)).pages, start=1):
        expected = Counter(line.rstrip() for line in (page.extract_text() or "").splitlines() if line.strip())
        missing = expected - actual[page_number]
        extra = actual[page_number] - expected
        if missing:
            failures.append(f"page {page_number}: {sum(missing.values())} source line(s) missing")
        if extra:
            failures.append(f"page {page_number}: {sum(extra.values())} unexpected source line(s)")
    return failures
