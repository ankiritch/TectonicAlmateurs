from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Author, normalize_name, tags_to_json


SESSION_AUTHOR_KEY = "author_id"


def current_author(request: Request, db: Session) -> Author | None:
    author_id = request.session.get(SESSION_AUTHOR_KEY)
    if not author_id:
        return None
    return db.get(Author, int(author_id))


def set_author_session(request: Request, author: Author) -> None:
    request.session[SESSION_AUTHOR_KEY] = author.id


def identify_author(db: Session, name: str, tags: list[str]) -> Author:
    cleaned_name = " ".join(name.strip().split())
    if not cleaned_name:
        raise ValueError("Name is required.")
    name_key = normalize_name(cleaned_name)
    author = db.execute(select(Author).where(Author.name_key == name_key)).scalar_one_or_none()
    if author is None:
        author = Author(
            name=cleaned_name,
            name_key=name_key,
            content_tags=tags_to_json(tags),
        )
        db.add(author)
    else:
        author.name = cleaned_name
        author.content_tags = tags_to_json(tags)
    db.commit()
    db.refresh(author)
    return author
