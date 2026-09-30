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
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.config import db_path, ensure_data_dirs


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

    documents: Mapped[list["Document"]] = relationship(back_populates="author")

    def tags_list(self) -> list[str]:
        try:
            value = json.loads(self.content_tags)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), nullable=False)
    content_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pdf_metadata: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    author: Mapped[Author] = relationship(back_populates="documents")

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


engine: Engine | None = None
SessionLocal: sessionmaker | None = None


FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    filename,
    tags,
    author_name,
    extracted_text,
    content=''
);
"""


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


def reset_engine() -> None:
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None


def upsert_fts(db, document: Document, author_name: str) -> None:
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
            "author_name": author_name,
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


def search_documents(db, query: str = "", tag: str | None = None, author: str | None = None):
    stmt = select(Document).join(Author)
    if query.strip():
        match = fts_match_query(query)
        if match:
            fts_ids = db.execute(
                text("SELECT rowid FROM documents_fts WHERE documents_fts MATCH :q"),
                {"q": match},
            ).scalars().all()
            stmt = stmt.where(Document.id.in_(fts_ids or [-1]))
    if tag:
        tag_needle = tag.strip().casefold()
        stmt = stmt.where(func.lower(Document.content_tags).like(f"%{tag_needle}%"))
    if author:
        stmt = stmt.where(Author.name_key == normalize_name(author))
    stmt = stmt.order_by(Document.uploaded_at.desc())
    return db.execute(stmt).scalars().all()
