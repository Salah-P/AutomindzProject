"""Extract plain text from candidate CV uploads (PDF / DOCX)."""

from __future__ import annotations

import io
from pathlib import Path


class CvParseError(ValueError):
    """Raised when a CV cannot be read or has no extractable text."""


def extract_text(filename: str, content: bytes, content_type: str | None = None) -> str:
    name = (Path(filename).name or "").lower()
    ctype = (content_type or "").lower()

    if name.endswith(".pdf") or "pdf" in ctype:
        return _from_pdf(content)
    if name.endswith(".docx") or "wordprocessingml" in ctype or name.endswith(".doc"):
        if name.endswith(".doc") and not name.endswith(".docx"):
            raise CvParseError("Legacy .doc is not supported — please upload PDF or DOCX")
        return _from_docx(content)

    raise CvParseError("Unsupported file type — upload a PDF or DOCX")


def _from_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise CvParseError("pypdf is not installed") from exc

    reader = PdfReader(io.BytesIO(content))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    joined = "\n".join(parts).strip()
    if not joined:
        raise CvParseError("No text found in PDF (it may be image-only)")
    return joined


def _from_docx(content: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover
        raise CvParseError("python-docx is not installed") from exc

    doc = Document(io.BytesIO(content))
    parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    joined = "\n".join(parts).strip()
    if not joined:
        raise CvParseError("No text found in DOCX")
    return joined
