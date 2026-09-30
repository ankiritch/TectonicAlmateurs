from datetime import datetime, timedelta, timezone

from app.ranking import SearchCriteria, linear_score, rank_documents, target_age_days
from app.regions import region_match


class _Author:
    def __init__(self, verified: bool):
        self.verified = verified


class _Doc:
    def __init__(self, **kwargs):
        self.upload_date = kwargs.get("upload_date", datetime.now(timezone.utc))
        self.share_points = kwargs.get("share_points", 0)
        self.country = kwargs.get("country", "")
        self.ai_used = kwargs.get("ai_used", False)
        self._authors = kwargs.get("authors", [])

    def authors(self):
        return self._authors


def test_region_match_country_inside_continent():
    assert region_match("France", "Europe") == 1.0
    assert region_match("France", "France") == 1.0
    assert region_match("Japan", "Europe") == 0.0
    assert region_match("Worldwide", "France") == 0.5
    assert region_match("Europe", "France") == 0.5


def test_age_cursor_endpoints():
    assert target_age_days(0) == 3
    assert target_age_days(100) > 29 * 365


def test_linear_model_is_weighted_average():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    verified = _Doc(authors=[_Author(True)], share_points=0, upload_date=now)
    popular = _Doc(authors=[_Author(False)], share_points=8, upload_date=now)
    criteria = SearchCriteria(w_verified=10, w_popularity=10, prefer_verified="yes")
    verified_score = linear_score(verified, criteria, max_share_points=8, now=now)
    popular_score = linear_score(popular, criteria, max_share_points=8, now=now)
    assert verified_score == 0.5
    assert popular_score == 0.5

    only_verified = SearchCriteria(w_verified=10, prefer_verified="yes")
    ranked = rank_documents([popular, verified], only_verified, now=now)
    assert ranked[0] is verified
    assert ranked[0].rank_score == 1.0


def test_age_weight_prefers_old_documents():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    recent = _Doc(upload_date=now - timedelta(days=3))
    archive = _Doc(upload_date=now - timedelta(days=30 * 365))
    criteria = SearchCriteria(w_age=10, age=100)
    ranked = rank_documents([recent, archive], criteria, now=now)
    assert ranked[0] is archive
    assert ranked[0].rank_score > ranked[1].rank_score


def test_zero_weights_do_not_use_share_points():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    older = _Doc(share_points=9, upload_date=now - timedelta(days=10))
    newer = _Doc(share_points=0, upload_date=now)
    ranked = rank_documents([older, newer], SearchCriteria(), now=now)
    assert ranked[0] is newer
    assert ranked[0].rank_score == 0.0
