from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class PdfProcessingError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stringify_meta(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_pdf_info(data: bytes) -> dict[str, str]:
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfProcessingError("Could not read PDF file.") from exc
    meta = reader.metadata or {}
    info: dict[str, str] = {}
    for key, value in dict(meta).items():
        rendered = _stringify_meta(value)
        if rendered is None:
            continue
        info[str(key).lstrip("/")] = rendered
    return info


def extract_text(data: bytes, max_pages: int = 25) -> str:
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfProcessingError("Could not read PDF file.") from exc
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(chunks).strip()


def _page_content_bytes(page) -> bytes:
    contents = page.get_contents()
    if contents is None:
        return b""
    if isinstance(contents, list):
        return b"".join(item.get_data() for item in contents)
    return contents.get_data()


def document_content_hash(data: bytes) -> str:
    """Hash page streams so Info metadata can be stamped without forking the document."""
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfProcessingError("Could not read PDF file.") from exc
    digest = hashlib.sha256()
    digest.update(str(len(reader.pages)).encode())
    for page in reader.pages:
        digest.update(_page_content_bytes(page))
    return digest.hexdigest()


def apply_metadata(
    data: bytes,
    *,
    title: str,
    author: str,
    tags: list[str],
    document_id: str,
    document_hash: str,
    country: str = "",
    ai_used: bool = False,
) -> bytes:
    try:
        reader = PdfReader(BytesIO(data))
        writer = PdfWriter()
        writer.append(reader)
        writer.add_metadata(
            {
                "/Title": title,
                "/Author": author,
                "/Keywords": ", ".join(tags),
                "/document_id": document_id,
                "/document_hash": document_hash,
                "/Country": country,
                "/AIUsed": "yes" if ai_used else "no",
                "/Producer": "TectonicAlmateurs",
            }
        )
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    except PdfReadError as exc:
        raise PdfProcessingError("Could not write PDF metadata.") from exc
