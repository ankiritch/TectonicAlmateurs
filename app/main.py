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
    known_content_tags,
    document_content_tags,
    get_document_by_hash,
    get_document_by_public_id,
    get_session,
    group_by_lineage,
    init_db,
    list_authors,
    parse_tags,
    search_documents,
    set_author_chain,
    tags_to_json,
    upsert_fts,
    validate_content_tags,
)
from app.ranking import SearchCriteria, choice, clamp_age, clamp_weight
from app.regions import canonical_region, grouped_regions
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
        "verified_content_tags": document.verified_tags_on_document(),
        "country": document.country,
        "ai_used": document.ai_used,
        "pdf_metadata": document.metadata_dict(),
        "upload_date": document.upload_date.isoformat(),
        "share_points": document.share_points,
        "rank_score": round(getattr(document, "rank_score", 0.0), 4),
        "predecessor_id": document.predecessor.document_id if document.predecessor else None,
        "authors": [
            {
                "id": author.id,
                "name": author.name,
                "content_tags": author.tags_list(),
                "verified_tags": author.verified_tags_list(),
                "verified": author.verified,
            }
            for author in document.authors()
        ],
        "download_url": f"/api/documents/{document.document_id}/file",
    }


def upload_context(author: Author, db: Session, extra: dict | None = None) -> dict:
    context = {
        "author": author,
        "available_tags": known_content_tags(db),
        "selected_tags": author.tags_list(),
        "region_groups": grouped_regions(),
        "selected_country": "Worldwide",
        "new_tags": "",
        "ai_used": False,
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
            "verified_tag_value": ", ".join(author.verified_tags_list()) if author else "",
        },
    )


@app.post("/auth")
def submit_identity(
    request: Request,
    name: str = Form(...),
    content_tags: str = Form(""),
    verified_tags: str = Form(""),
    verified: str = Form(""),
    db: Session = Depends(get_session),
):
    try:
        author = identify_author(
            db,
            name,
            parse_tags(content_tags),
            verified=verified == "yes",
            verified_tags=parse_tags(verified_tags),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "identity.html",
            {
                "author": None,
                "tag_value": content_tags,
                "verified_tag_value": verified_tags,
                "error": str(exc),
                "name_value": name,
            },
            status_code=400,
        )
    set_author_session(request, author)
    return RedirectResponse("/library", status_code=303)


def read_criteria(
    w_verified: int = 0,
    w_age: int = 0,
    w_country: int = 0,
    w_ai: int = 0,
    w_popularity: int = 0,
    prefer_verified: str = "any",
    age: int = 0,
    country: str = "",
    prefer_ai: str = "any",
    w_tag_verified: int = 0,
    prefer_tag_verified: str = "any",
) -> SearchCriteria:
    region = canonical_region(country) or ""
    return SearchCriteria(
        w_verified=clamp_weight(w_verified),
        w_tag_verified=clamp_weight(w_tag_verified),
        w_age=clamp_weight(w_age),
        w_country=clamp_weight(w_country),
        w_ai=clamp_weight(w_ai),
        w_popularity=clamp_weight(w_popularity),
        prefer_verified=choice(prefer_verified, {"yes", "no", "any"}, "any"),
        prefer_tag_verified=choice(prefer_tag_verified, {"yes", "no", "any"}, "any"),
        age=clamp_age(age),
        country=region,
        prefer_ai=choice(prefer_ai, {"yes", "no", "any"}, "any"),
    )


@app.get("/library", response_class=HTMLResponse)
def library_page(
    request: Request,
    q: str = "",
    tag: str = "",
    author: str = "",
    w_verified: int = 0,
    w_age: int = 0,
    w_country: int = 0,
    w_ai: int = 0,
    w_popularity: int = 0,
    prefer_verified: str = "any",
    age: int = 0,
    country: str = "",
    prefer_ai: str = "any",
    w_tag_verified: int = 0,
    prefer_tag_verified: str = "any",
    db: Session = Depends(get_session),
):
    current = current_author(request, db)
    if current is None:
        return RedirectResponse("/", status_code=303)
    criteria = read_criteria(
        w_verified, w_age, w_country, w_ai, w_popularity,
        prefer_verified, age, country, prefer_ai,
        w_tag_verified, prefer_tag_verified,
    )
    documents = search_documents(
        db, query=q, tag=tag or None, author=author or None, criteria=criteria
    )
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "author": current,
            "groups": group_by_lineage(db, documents),
            "matched_ids": {item.document_id for item in documents},
            "searching": bool(q.strip() or tag.strip() or author.strip()),
            "q": q,
            "tag": tag,
            "author_filter": author,
            "criteria": criteria,
            "tag_options": document_content_tags(db),
            "author_options": list_authors(db),
            "region_groups": grouped_regions(),
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
    new_tags: str = Form(""),
    country: str = Form("Worldwide"),
    ai_used: str = Form("no"),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    current = current_author(request, db)
    if current is None:
        return RedirectResponse("/", status_code=303)
    selected = list(content_tags) + parse_tags(new_tags)
    try:
        document = await store_upload(
            file, current, selected, db, country=country, ai_used=ai_used == "yes"
        )
    except HttpishError as exc:
        return templates.TemplateResponse(
            request,
            "upload.html",
            upload_context(
                current,
                db,
                {
                    "error": exc.detail,
                    "selected_tags": selected,
                    "new_tags": new_tags,
                    "selected_country": country,
                    "ai_used": ai_used == "yes",
                },
            ),
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/library?q={document.title}", status_code=303)


@app.get("/api/documents")
def api_search(
    q: str = "",
    tag: str = "",
    author: str = "",
    w_verified: int = 0,
    w_age: int = 0,
    w_country: int = 0,
    w_ai: int = 0,
    w_popularity: int = 0,
    prefer_verified: str = "any",
    age: int = 0,
    country: str = "",
    prefer_ai: str = "any",
    w_tag_verified: int = 0,
    prefer_tag_verified: str = "any",
    db: Session = Depends(get_session),
    _user: Author = Depends(require_author),
):
    criteria = read_criteria(
        w_verified, w_age, w_country, w_ai, w_popularity,
        prefer_verified, age, country, prefer_ai,
        w_tag_verified, prefer_tag_verified,
    )
    documents = search_documents(
        db, query=q, tag=tag or None, author=author or None, criteria=criteria
    )
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
    new_tags: str = Form(""),
    country: str = Form("Worldwide"),
    ai_used: str = Form("no"),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    author: Author = Depends(require_author),
):
    tags = list(content_tags) + parse_tags(extra_tags) + parse_tags(new_tags)
    try:
        document = await store_upload(
            file, author, tags, db, country=country, ai_used=ai_used == "yes"
        )
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


def _with_ai_tag(tags: list[str], ai_used: bool) -> list[str]:
    kept = [tag for tag in tags if tag.casefold() != "ai"]
    if ai_used:
        kept.append("AI")
    return kept


async def store_upload(
    file: UploadFile,
    author: Author,
    selected_tags: list[str],
    db: Session,
    country: str = "Worldwide",
    ai_used: bool = False,
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

    region = canonical_region(country)
    if region is None:
        raise HttpishError(400, "Choose a country, a continent, or Worldwide.")
    try:
        tags = _with_ai_tag(validate_content_tags(db, selected_tags), ai_used)
    except ValueError as exc:
        raise HttpishError(400, str(exc)) from exc

    title = info.get("Title") or Path(filename).stem
    incoming_id = (info.get("document_id") or "").strip()
    embedded_hash = (info.get("document_hash") or "").strip()
    existing = get_document_by_public_id(db, incoming_id) if incoming_id else None
    if existing is None and embedded_hash:
        existing = get_document_by_hash(db, embedded_hash)
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
        country=region,
        ai_used=ai_used,
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
        country=region,
        ai_used=ai_used,
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
