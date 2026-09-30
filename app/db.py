from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    selectinload,
    sessionmaker,
)

from app.config import db_path, ensure_data_dirs
from app.ranking import rank_documents


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (UniqueConstraint("name_key", name="uq_authors_name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_key: Mapped[str] = mapped_column(String(200), nullable=False)
    content_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    document_links: Mapped[list["DocumentAuthor"]] = relationship(back_populates="author")

    def tags_list(self) -> list[str]:
        try:
            value = json.loads(self.content_tags)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]


class DocumentAuthor(Base):
    __tablename__ = "document_authors"

    document_pk: Mapped[int] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    document: Mapped["Document"] = relationship(back_populates="author_links")
    author: Mapped[Author] = relationship(back_populates="document_links")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pdf_metadata: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    upload_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    share_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predecessor_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    author_links: Mapped[list[DocumentAuthor]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentAuthor.position",
    )
    predecessor: Mapped["Document | None"] = relationship(remote_side=[id])

    def tags_list(self) -> list[str]:
        try:
            value = json.loads(self.content_tags)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def metadata_dict(self) -> dict[str, Any]:
        try:
            value = json.loads(self.pdf_metadata)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def authors(self) -> list[Author]:
        links = sorted(self.author_links, key=lambda link: link.position)
        return [link.author for link in links]

    def author_names(self) -> list[str]:
        return [author.name for author in self.authors()]


engine: Engine | None = None
SessionLocal: sessionmaker | None = None


FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    filename,
    tags,
    author_name,
    extracted_text
);
"""

DOCUMENT_LOAD = (
    selectinload(Document.author_links).selectinload(DocumentAuthor.author),
    selectinload(Document.predecessor),
)


def get_engine() -> Engine:
    global engine, SessionLocal
    if engine is None:
        ensure_data_dirs()
        engine = create_engine(
            f"sqlite:///{db_path()}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine


def init_db() -> None:
    eng = get_engine()
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(FTS_DDL))


def get_session():
    if SessionLocal is None:
        init_db()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def tags_to_json(tags: list[str]) -> str:
    cleaned = []
    seen = set()
    for tag in tags:
        item = " ".join(tag.strip().split())
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return json.dumps(cleaned)


def parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = [part.strip() for part in raw.replace(";", ",").split(",")]
    return [part for part in parts if part]


def all_author_tags(db) -> list[str]:
    authors = db.execute(select(Author)).scalars().all()
    merged: list[str] = []
    seen: set[str] = set()
    for author in authors:
        for tag in author.tags_list():
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tag)
    return merged


def allowed_tag_map(db) -> dict[str, str]:
    return {tag.casefold(): tag for tag in all_author_tags(db)}


def validate_content_tags(db, tags: list[str]) -> list[str]:
    allowed = allowed_tag_map(db)
    if not allowed and tags:
        raise ValueError("No author content tags exist yet. Set tags on an author identity first.")
    resolved = []
    unknown = []
    for tag in json.loads(tags_to_json(tags)):
        canonical = allowed.get(tag.casefold())
        if canonical is None:
            unknown.append(tag)
        else:
            resolved.append(canonical)
    if unknown:
        raise ValueError(
            "Content tags must come from an existing author. Unknown: " + ", ".join(unknown)
        )
    return resolved


def reset_engine() -> None:
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None


def set_author_chain(document: Document, authors: list[Author]) -> None:
    document.author_links.clear()
    seen: set[int] = set()
    position = 0
    for author in authors:
        if author.id in seen:
            continue
        seen.add(author.id)
        document.author_links.append(
            DocumentAuthor(author=author, position=position)
        )
        position += 1


def upsert_fts(db, document: Document) -> None:
    db.execute(
        text("DELETE FROM documents_fts WHERE rowid = :id"),
        {"id": document.id},
    )
    db.execute(
        text(
            """
            INSERT INTO documents_fts (rowid, title, filename, tags, author_name, extracted_text)
            VALUES (:id, :title, :filename, :tags, :author_name, :extracted_text)
            """
        ),
        {
            "id": document.id,
            "title": document.title,
            "filename": document.filename,
            "tags": " ".join(document.tags_list()),
            "author_name": " ".join(document.author_names()),
            "extracted_text": document.extracted_text,
        },
    )


def fts_match_query(query: str) -> str | None:
    terms = []
    for token in query.split():
        cleaned = token.replace('"', "").replace("*", "").strip()
        if cleaned:
            terms.append(f'"{cleaned}"')
    if not terms:
        return None
    return " AND ".join(terms)


def get_document_by_public_id(db, document_id: str) -> Document | None:
    return db.execute(
        select(Document).options(*DOCUMENT_LOAD).where(Document.document_id == document_id)
    ).scalar_one_or_none()


def search_documents(db, query: str = "", tag: str | None = None, author: str | None = None):
    stmt = select(Document).options(*DOCUMENT_LOAD)
    if query.strip():
        needle = query.strip().casefold()
        clauses = [func.lower(Document.content_tags).like(f"%{needle}%")]
        match = fts_match_query(query)
        if match:
            fts_ids = db.execute(
                text("SELECT rowid FROM documents_fts WHERE documents_fts MATCH :q"),
                {"q": match},
            ).scalars().all()
            clauses.append(Document.id.in_(fts_ids or [-1]))
        stmt = stmt.where(or_(*clauses))
    if tag:
        tag_needle = tag.strip().casefold()
        stmt = stmt.where(func.lower(Document.content_tags).like(f"%{tag_needle}%"))
    if author:
        stmt = (
            stmt.join(DocumentAuthor)
            .join(Author)
            .where(Author.name_key == normalize_name(author))
        )
    documents = db.execute(stmt).unique().scalars().all()
    return rank_documents(documents)
