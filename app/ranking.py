"""Document ranking.

Search matches on content and content tags first, then orders the result
list. The current key is share points (one point per download).

`trust_score` is a no-op hook so a trustworthiness model can be mixed in
later without changing search or download code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from app.db import Document


def trust_score(document: Document) -> float:
    """Future: reliability of the author chain for this document's tags."""
    return 0.0


def ranking_key(document: Document) -> tuple:
    return (
        document.share_points,
        trust_score(document),
        document.upload_date.timestamp() if document.upload_date else 0.0,
    )


def rank_documents(documents: Sequence[Document]) -> list[Document]:
    return sorted(documents, key=ranking_key, reverse=True)
