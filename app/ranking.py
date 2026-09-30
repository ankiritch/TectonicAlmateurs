"""Linear ranking for a search.

Each criterion is a score in [0, 1]. The document score is the weighted
average of the criteria the user marked as important:

    score = (w1*s1 + ... + wn*sn) / (w1 + ... + wn)

Weights are integers from 0 (ignore) to 10 (essential). A weight of 0 drops
that term. When every weight is 0, every document scores 0 and newer uploads
sort first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence

from app.regions import region_match

if TYPE_CHECKING:
    from app.db import Document

MIN_AGE_DAYS = 3.0
MAX_AGE_DAYS = 30 * 365.25


@dataclass
class SearchCriteria:
    w_verified: int = 0
    w_tag_verified: int = 0
    w_age: int = 0
    w_country: int = 0
    w_ai: int = 0
    w_popularity: int = 0
    prefer_verified: str = "any"
    prefer_tag_verified: str = "any"
    age: int = 0
    country: str = ""
    prefer_ai: str = "any"


def clamp_weight(value: object) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, number))


def clamp_age(value: object) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, number))


def choice(value: object, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else default


def target_age_days(slider: int) -> float:
    """Map the age cursor: 0 is a few days, 100 is 30+ years."""
    t = clamp_age(slider) / 100.0
    return MIN_AGE_DAYS * (MAX_AGE_DAYS / MIN_AGE_DAYS) ** t


def age_label(slider: int) -> str:
    days = target_age_days(slider)
    if days < 14:
        return "a few days"
    if days < 45:
        return "about a month"
    if days < 300:
        return "several months"
    years = days / 365.25
    if years < 1.4:
        return "about a year"
    if years >= 25:
        return "30+ years"
    return f"about {round(years)} years"


def _age_closeness(age_days: float, target_days: float) -> float:
    age_days = max(age_days, 0.5)
    target_days = max(target_days, 0.5)
    distance = abs(math.log(age_days) - math.log(target_days))
    return max(0.0, 1.0 - distance / math.log(10))


def _document_age_days(document: Document, now: datetime) -> float:
    uploaded = document.upload_date
    if uploaded is None:
        return 0.0
    if uploaded.tzinfo is None:
        uploaded = uploaded.replace(tzinfo=timezone.utc)
    return max(0.0, (now - uploaded).total_seconds() / 86400)


def verified_score(document: Document, prefer: str) -> float:
    authors = document.authors()
    verified = any(author.verified for author in authors) if authors else False
    if prefer == "yes":
        return 1.0 if verified else 0.0
    if prefer == "no":
        return 0.0 if verified else 1.0
    return 0.0


def tag_verification_score(document: Document, prefer: str) -> float:
    """Share of the document's tags that some author on the chain is verified for."""
    tags = [tag for tag in document.tags_list() if tag.casefold() != "ai"]
    if not tags:
        covered = 0.0
    else:
        verified = {
            tag.casefold()
            for author in document.authors()
            for tag in author.verified_tags_list()
        }
        covered = sum(1 for tag in tags if tag.casefold() in verified) / len(tags)
    if prefer == "yes":
        return covered
    if prefer == "no":
        return 1.0 - covered
    return 0.0


def ai_score(document: Document, prefer: str) -> float:
    used = bool(document.ai_used)
    if prefer == "yes":
        return 1.0 if used else 0.0
    if prefer == "no":
        return 0.0 if used else 1.0
    return 0.0


def linear_score(
    document: Document,
    criteria: SearchCriteria,
    max_share_points: int,
    now: datetime | None = None,
) -> float:
    now = now or datetime.now(timezone.utc)
    popularity = 0.0
    if max_share_points > 0:
        popularity = document.share_points / max_share_points
    terms = [
        (criteria.w_verified, verified_score(document, criteria.prefer_verified)),
        (criteria.w_tag_verified, tag_verification_score(document, criteria.prefer_tag_verified)),
        (criteria.w_age, _age_closeness(_document_age_days(document, now), target_age_days(criteria.age))),
        (criteria.w_country, region_match(document.country or "", criteria.country)),
        (criteria.w_ai, ai_score(document, criteria.prefer_ai)),
        (criteria.w_popularity, popularity),
    ]
    weight_sum = sum(weight for weight, _score in terms)
    if weight_sum <= 0:
        return 0.0
    return sum(weight * score for weight, score in terms) / weight_sum


def rank_documents(
    documents: Sequence[Document],
    criteria: SearchCriteria | None = None,
    now: datetime | None = None,
) -> list[Document]:
    criteria = criteria or SearchCriteria()
    now = now or datetime.now(timezone.utc)
    max_shares = max((document.share_points for document in documents), default=0)
    ranked = list(documents)
    for document in ranked:
        document.rank_score = linear_score(document, criteria, max_shares, now)

    def sort_key(document: Document) -> tuple[float, float]:
        uploaded = document.upload_date
        if uploaded is None:
            stamp = 0.0
        else:
            if uploaded.tzinfo is None:
                uploaded = uploaded.replace(tzinfo=timezone.utc)
            stamp = uploaded.timestamp()
        return (getattr(document, "rank_score", 0.0), stamp)

    ranked.sort(key=sort_key, reverse=True)
    return ranked
