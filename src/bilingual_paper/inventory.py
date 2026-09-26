from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from .models import Paragraph


# Lines that stand alone as their own block: roman-numeral section headings ("II. RESULTS")
# and the usual unnumbered back-matter headings.
_HEADING = re.compile(r"^(?:[IVX]+\.\s+|(?i:acknowledge?ments?|references|bibliography|appendix)$)")
_TERMINAL = re.compile(r"(?:[.!?][’”\"']?|\.)\s*$")


def paragraphize_plain_text(text: str) -> list[str]:
    """Group the PDF's own reading-order lines without rewriting their text."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]

    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append("\n".join(current))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if _HEADING.match(stripped) or stripped.isdigit():  # headings and bare page numbers
            flush()
            blocks.append(line)
            continue
        current.append(line)
        if _TERMINAL.search(stripped):
            flush()
    flush()
    return blocks


def _paragraphs_from_column(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(re.sub(r"\s+", " ", cleaned))
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def split_two_column_layout(text: str) -> list[str]:
    """Split pypdf layout text into reading-order blocks for a two-column paper."""
    lines = text.splitlines()
    content = [line for line in lines if line.strip()]
    if not content:
        return []
    width = max(len(line) for line in content)
    midpoint = width // 2
    left = [line[:midpoint].rstrip() for line in lines]
    right = [line[midpoint:].rstrip() for line in lines]
    blocks = _paragraphs_from_column(left) + _paragraphs_from_column(right)
    return [block for block in blocks if block]


def extract(pdf: Path) -> list[Paragraph]:
    """Extract tentative paragraphs in page/column reading order.

    This preserves all extracted source text but is still a draft inventory. Scanned
    or OCR-backed PDFs must be compared with rendered pages before translation.
    """
    result: list[Paragraph] = []
    for page_number, page in enumerate(PdfReader(str(pdf)).pages, start=1):
        blocks = paragraphize_plain_text(page.extract_text() or "")
        for index, block in enumerate(blocks, start=1):
            result.append(Paragraph(id=f"p{page_number:02d}-{index:03d}", page=page_number, english=block))
    return result
