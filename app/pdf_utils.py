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
            pass
        for annot in _page_annotations(page):
            obj = annot.get_object() if hasattr(annot, "get_object") else annot
            contents = obj.get("/Contents") if hasattr(obj, "get") else None
            if contents:
                chunks.append(str(contents))
    return "\n".join(part for part in chunks if part).strip()


def _page_content_bytes(page) -> bytes:
    contents = page.get_contents()
    if contents is None:
        return b""
    if isinstance(contents, list):
        return b"".join(item.get_data() for item in contents)
    return contents.get_data()


# Visible markup. /P is the page back-reference and changes when the file is rewritten.
_ANNOTATION_KEYS = ("/Subtype", "/Contents", "/Rect", "/DS", "/DA", "/InkList", "/QuadPoints", "/Name", "/T")


def _page_annotations(page) -> list:
    annots = page.get("/Annots")
    if not annots:
        return []
    try:
        return list(annots)
    except TypeError:
        return []


def _annotation_fingerprint(annot) -> bytes:
    obj = annot.get_object() if hasattr(annot, "get_object") else annot
    parts: list[bytes] = []
    for key in _ANNOTATION_KEYS:
        if key not in obj:
            continue
        parts.append(f"{key}={obj[key]}".encode("utf-8", errors="replace"))
    appearance = obj.get("/AP")
    if appearance is not None:
        stream = appearance.get_object() if hasattr(appearance, "get_object") else appearance
        if hasattr(stream, "get_data"):
            try:
                parts.append(b"AP=" + stream.get_data())
            except Exception:
                parts.append(b"AP=" + str(stream).encode("utf-8", errors="replace"))
        else:
            parts.append(b"AP=" + str(stream).encode("utf-8", errors="replace"))
    return b"\n".join(parts)


def document_content_hash(data: bytes) -> str:
    """Hash page streams and markup. Info metadata can change without forking the document."""
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfProcessingError("Could not read PDF file.") from exc
    digest = hashlib.sha256()
    digest.update(str(len(reader.pages)).encode())
    for page in reader.pages:
        digest.update(_page_content_bytes(page))
        marks = sorted(_annotation_fingerprint(annot) for annot in _page_annotations(page))
        digest.update(str(len(marks)).encode())
        for mark in marks:
            digest.update(mark)
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
