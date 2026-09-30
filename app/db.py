from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
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
from app.ranking import SearchCriteria, rank_documents


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (UniqueConstraint("name_key", name="uq_authors_name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_key: Mapped[str] = mapped_column(String(200), nullable=False)
    content_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    verified_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    document_links: Mapped[list["DocumentAuthor"]] = relationship(back_populates="author")

    def tags_list(self) -> list[str]:
        return _json_list(self.content_tags)

    def verified_tags_list(self) -> list[str]:
        return _json_list(self.verified_tags)


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
    country: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    ai_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
        return _json_list(self.content_tags)

    def verified_tags_on_document(self) -> list[str]:
        verified = set()
        for author in self.authors():
            for tag in author.verified_tags_list():
                verified.add(tag.casefold())
        return [tag for tag in self.tags_list() if tag.casefold() in verified]

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


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    names = {row[1] for row in rows}
    if column not in names:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    eng = get_engine()
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(FTS_DDL))
        _ensure_column(conn, "authors", "verified", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "authors", "verified_tags", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "documents", "country", "VARCHAR(80) NOT NULL DEFAULT ''")
        _ensure_column(conn, "documents", "ai_used", "INTEGER NOT NULL DEFAULT 0")


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


def _json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


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


def known_content_tags(db) -> list[str]:
    seen: dict[str, str] = {}
    for tag in all_author_tags(db) + document_content_tags(db):
        if tag.casefold() == "ai":
            continue
        seen.setdefault(tag.casefold(), tag)
    return sorted(seen.values(), key=str.casefold)


def validate_content_tags(db, tags: list[str]) -> list[str]:
    """Keep any tag. Reuse the existing spelling when the tag is already known."""
    known = {tag.casefold(): tag for tag in known_content_tags(db)}
    resolved = []
    for tag in json.loads(tags_to_json(tags)):
        if tag.casefold() == "ai":
            continue
        resolved.append(known.get(tag.casefold(), tag))
    return json.loads(tags_to_json(resolved))


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


def get_document_by_hash(db, document_hash: str) -> Document | None:
    rows = (
        db.execute(
            select(Document).options(*DOCUMENT_LOAD).where(Document.document_hash == document_hash)
        )
        .unique()
        .scalars()
        .all()
    )
    if len(rows) == 1:
        return rows[0]
    return None


def document_content_tags(db) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    rows = db.execute(select(Document.content_tags)).scalars().all()
    for raw in rows:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, list):
            continue
        for item in value:
            text_item = str(item).strip()
            key = text_item.casefold()
            if not text_item or key in seen:
                continue
            seen.add(key)
            labels.append(text_item)
    return sorted(labels, key=str.casefold)


def list_authors(db) -> list[Author]:
    return list(db.execute(select(Author).order_by(Author.name)).scalars().all())


def _has_exact_tag(document: Document, tag: str) -> bool:
    needle = tag.casefold()
    return any(item.casefold() == needle for item in document.tags_list())


def search_documents(
    db,
    query: str = "",
    tag: str | None = None,
    author: str | None = None,
    criteria: SearchCriteria | None = None,
):
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
        known = {item.casefold() for item in document_content_tags(db)}
        if tag.strip().casefold() not in known:
            return []
    if author:
        if db.execute(
            select(Author.id).where(Author.name_key == normalize_name(author))
        ).first() is None:
            return []
        stmt = (
            stmt.join(DocumentAuthor)
            .join(Author)
            .where(Author.name_key == normalize_name(author))
        )
    documents = db.execute(stmt).unique().scalars().all()
    if tag:
        documents = [item for item in documents if _has_exact_tag(item, tag.strip())]
    return rank_documents(documents, criteria)


@dataclass
class DocumentVersion:
    number: int
    document: Document


@dataclass
class DocumentLineage:
    versions: list[DocumentVersion]
    score: float

    @property
    def latest(self) -> Document:
        return self.versions[-1].document

    @property
    def original(self) -> Document:
        return self.versions[0].document


def _lineage_members(root_id: int, children: dict[int, list[Document]], by_pk: dict[int, Document]) -> list[Document]:
    ordered: list[Document] = []
    seen: set[int] = set()

    def walk(pk: int) -> None:
        if pk in seen or pk not in by_pk:
            return
        seen.add(pk)
        ordered.append(by_pk[pk])
        kids = sorted(
            children.get(pk, []),
            key=lambda item: (item.upload_date, item.id),
        )
        for kid in kids:
            walk(kid.id)

    walk(root_id)
    ordered.sort(key=lambda item: (item.upload_date, item.id))
    return ordered


def group_by_lineage(db, matched: list[Document]) -> list[DocumentLineage]:
    """Collapse revisions of one original file into a single search result."""
    if not matched:
        return []
    rows = db.execute(select(Document).options(*DOCUMENT_LOAD)).unique().scalars().all()
    by_pk = {item.id: item for item in rows}
    children: dict[int, list[Document]] = defaultdict(list)
    for item in rows:
        if item.predecessor_id:
            children[item.predecessor_id].append(item)

    def root_pk(document: Document) -> int:
        seen: set[int] = set()
        current = document
        while current.predecessor_id and current.predecessor_id in by_pk and current.id not in seen:
            seen.add(current.id)
            current = by_pk[current.predecessor_id]
        return current.id

    matched_scores = {item.id: getattr(item, "rank_score", 0.0) for item in matched}
    groups: dict[int, DocumentLineage] = {}
    for item in matched:
        root = root_pk(item)
        if root in groups:
            continue
        members = _lineage_members(root, children, by_pk)
        versions = [DocumentVersion(number=index, document=member) for index, member in enumerate(members, start=1)]
        score = max((matched_scores.get(member.id, 0.0) for member in members), default=0.0)
        groups[root] = DocumentLineage(versions=versions, score=score)

    def sort_key(lineage: DocumentLineage) -> tuple[float, float]:
        uploaded = lineage.latest.upload_date
        stamp = uploaded.timestamp() if uploaded is not None else 0.0
        return (lineage.score, stamp)

    return sorted(groups.values(), key=sort_key, reverse=True)
