from __future__ import annotations

import json

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


def identify_author(
    db: Session,
    name: str,
    tags: list[str],
    verified: bool = False,
    verified_tags: list[str] | None = None,
) -> Author:
    cleaned_name = " ".join(name.strip().split())
    if not cleaned_name:
        raise ValueError("Name is required.")
    content = json.loads(tags_to_json(tags))
    verified_list = json.loads(tags_to_json(verified_tags or []))
    content_keys = {tag.casefold() for tag in content}
    for tag in verified_list:
        if tag.casefold() not in content_keys:
            content.append(tag)
            content_keys.add(tag.casefold())
    verified_only = [tag for tag in content if tag.casefold() in {item.casefold() for item in verified_list}]
    name_key = normalize_name(cleaned_name)
    author = db.execute(select(Author).where(Author.name_key == name_key)).scalar_one_or_none()
    if author is None:
        author = Author(
            name=cleaned_name,
            name_key=name_key,
            content_tags=tags_to_json(content),
            verified_tags=tags_to_json(verified_only),
            verified=verified,
        )
        db.add(author)
    else:
        author.name = cleaned_name
        author.content_tags = tags_to_json(content)
        author.verified_tags = tags_to_json(verified_only)
        author.verified = verified
    db.commit()
    db.refresh(author)
    return author
