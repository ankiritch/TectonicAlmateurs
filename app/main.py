from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import current_author, identify_author, set_author_session
from app.config import BASE_DIR, ensure_data_dirs, session_secret, upload_dir
from app.db import (
    Author,
    Document,
    all_author_tags,
    get_document_by_hash,
    get_document_by_public_id,
    get_session,
    init_db,
    parse_tags,
    search_documents,
    set_author_chain,
    tags_to_json,
    upsert_fts,
    validate_content_tags,
)
from app.pdf_utils import (
    PdfProcessingError,
    apply_metadata,
    document_content_hash,
    extract_text,
    read_pdf_info,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    init_db()
    yield


app = FastAPI(title="TectonicAlmateurs Document Library", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def require_author(request: Request, db: Session = Depends(get_session)) -> Author:
    author = current_author(request, db)
    if author is None:
        raise HTTPException(status_code=401, detail="Identify yourself first.")
    return author


def document_payload(document: Document) -> dict:
    return {
        "id": document.id,
        "document_id": document.document_id,
        "document_hash": document.document_hash,
        "filename": document.filename,
        "title": document.title,
        "content_tags": document.tags_list(),
        "pdf_metadata": document.metadata_dict(),
        "upload_date": document.upload_date.isoformat(),
        "share_points": document.share_points,
        "predecessor_id": document.predecessor.document_id if document.predecessor else None,
        "authors": [
            {
                "id": author.id,
                "name": author.name,
                "content_tags": author.tags_list(),
            }
            for author in document.authors()
        ],
        "download_url": f"/api/documents/{document.document_id}/file",
    }


def upload_context(author: Author, db: Session, extra: dict | None = None) -> dict:
    context = {
        "author": author,
        "available_tags": all_author_tags(db),
        "selected_tags": author.tags_list(),
    }
    if extra:
        context.update(extra)
    return context


@app.get("/", response_class=HTMLResponse)
def identity_page(request: Request, db: Session = Depends(get_session)):
    author = current_author(request, db)
    return templates.TemplateResponse(
        request,
        "identity.html",
        {
            "author": author,
            "tag_value": ", ".join(author.tags_list()) if author else "",
        },
    )


@app.post("/auth")
def submit_identity(
    request: Request,
    name: str = Form(...),
    content_tags: str = Form(""),
    db: Session = Depends(get_session),
):
    try:
        author = identify_author(db, name, parse_tags(content_tags))
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "identity.html",
            {"author": None, "tag_value": content_tags, "error": str(exc), "name_value": name},
            status_code=400,
        )
    set_author_session(request, author)
    return RedirectResponse("/library", status_code=303)


@app.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    tag: str = "",
    author: str = "",
    db: Session = Depends(get_session),
):
    current = current_author(request, db)
    if current is None:
        return RedirectResponse("/", status_code=303)
    documents = search_documents(db, query=q, tag=tag or None, author=author or None)
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "author": current,
            "documents": documents,
            "q": q,
            "tag": tag,
            "author_filter": author,
        },
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, db: Session = Depends(get_session)):
    current = current_author(request, db)
    if current is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "upload.html", upload_context(current, db))


@app.post("/upload")
async def upload_from_form(
    request: Request,
    content_tags: list[str] = Form(default=[]),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    current = current_author(request, db)
    if current is None:
        return RedirectResponse("/", status_code=303)
    try:
        document = await store_upload(file, current, content_tags, db)
    except HttpishError as exc:
        return templates.TemplateResponse(
            request,
            "upload.html",
            upload_context(current, db, {"error": exc.detail, "selected_tags": content_tags}),
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/library?q={document.title}", status_code=303)


@app.get("/api/documents")
def api_search(
    q: str = "",
    tag: str = "",
    author: str = "",
    db: Session = Depends(get_session),
    _user: Author = Depends(require_author),
):
    documents = search_documents(db, query=q, tag=tag or None, author=author or None)
    return [document_payload(item) for item in documents]


@app.get("/api/documents/{document_id}")
def api_metadata(
    document_id: str,
    db: Session = Depends(get_session),
    _user: Author = Depends(require_author),
):
    document = get_document_by_public_id(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document_payload(document)


@app.get("/api/documents/{document_id}/file")
def api_download(
    document_id: str,
    db: Session = Depends(get_session),
    _user: Author = Depends(require_author),
):
    document = get_document_by_public_id(db, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    path = Path(document.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored file is missing.")
    document.share_points += 1
    db.commit()
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{Path(document.filename).stem}-{document.document_id}.pdf",
    )


@app.post("/api/documents")
async def api_upload(
    content_tags: list[str] = Form(default=[]),
    extra_tags: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    author: Author = Depends(require_author),
):
    tags = list(content_tags) + parse_tags(extra_tags)
    try:
        document = await store_upload(file, author, tags, db)
    except HttpishError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return document_payload(document)


class HttpishError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _extract_search_text(data: bytes, info: dict[str, str]) -> str:
    page_text = extract_text(data)
    return "\n".join(part for part in [page_text, " ".join(info.values())] if part)


async def store_upload(
    file: UploadFile,
    author: Author,
    selected_tags: list[str],
    db: Session,
) -> Document:
    filename = Path(file.filename or "document.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HttpishError(400, "Only PDF files are accepted.")
    data = await file.read()
    if not data:
        raise HttpishError(400, "Uploaded file is empty.")
    if file.content_type and file.content_type not in {
        "application/pdf",
        "application/x-pdf",
        "binary/octet-stream",
        "application/octet-stream",
    }:
        raise HttpishError(400, "Only PDF files are accepted.")

    try:
        info = read_pdf_info(data)
        incoming_hash = document_content_hash(data)
        body_text = _extract_search_text(data, info)
    except PdfProcessingError as exc:
        raise HttpishError(400, str(exc)) from exc

    try:
        tags = validate_content_tags(db, selected_tags)
    except ValueError as exc:
        raise HttpishError(400, str(exc)) from exc

    title = info.get("Title") or Path(filename).stem
    incoming_id = (info.get("document_id") or "").strip()
    existing = get_document_by_public_id(db, incoming_id) if incoming_id else None
    if existing is None:
        existing = get_document_by_hash(db, incoming_hash)

    if existing is not None and existing.document_hash == incoming_hash:
        chain = existing.authors()
        if all(item.id != author.id for item in chain):
            chain = chain + [author]
            set_author_chain(existing, chain)
            db.commit()
            db.refresh(existing)
            upsert_fts(db, existing)
            db.commit()
        return existing

    predecessor = None
    chain = [author]
    if existing is not None and existing.document_hash != incoming_hash:
        predecessor = existing
        chain = existing.authors()
        if all(item.id != author.id for item in chain):
            chain = chain + [author]

    public_id = str(uuid.uuid4())
    author_label = ", ".join(item.name for item in chain)
    stamped = apply_metadata(
        data,
        title=title,
        author=author_label,
        tags=tags,
        document_id=public_id,
        document_hash=incoming_hash,
    )
    stored_name = f"{public_id}.pdf"
    stored_path = upload_dir() / stored_name
    stored_path.write_bytes(stamped)
    stored_info = read_pdf_info(stamped)

    document = Document(
        document_id=public_id,
        document_hash=incoming_hash,
        filename=filename,
        stored_path=str(stored_path),
        title=title,
        content_tags=tags_to_json(tags),
        pdf_metadata=json.dumps(stored_info),
        extracted_text=body_text,
        predecessor=predecessor,
    )
    db.add(document)
    db.flush()
    set_author_chain(document, chain)
    db.commit()
    db.refresh(document)
    upsert_fts(db, document)
    db.commit()
    return document
