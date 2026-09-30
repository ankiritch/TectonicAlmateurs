from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter


def build_pdf(*, title: str, body: str, extra_pages: int = 0) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    for _ in range(extra_pages):
        writer.add_blank_page(width=400, height=400)
    writer.add_metadata({"/Title": title, "/Subject": body})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def with_changed_pages(data: bytes) -> bytes:
    reader = PdfReader(BytesIO(data))
    writer = PdfWriter()
    writer.append(reader)
    writer.add_blank_page(width=400, height=400)
    if reader.metadata:
        writer.add_metadata(dict(reader.metadata))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "library.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    from app.config import ensure_data_dirs
    from app.db import init_db, reset_engine

    reset_engine()
    ensure_data_dirs()
    init_db()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    reset_engine()


def identify(client: TestClient, name="Ada Geologist", tags="basalt, internal"):
    response = client.post("/auth", data={"name": name, "content_tags": tags}, follow_redirects=False)
    assert response.status_code == 303
    return response


def test_identity_required_for_library(client: TestClient):
    response = client.get("/library", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_upload_search_download_flow(client: TestClient):
    identify(client)
    pdf = build_pdf(title="Core Sample Report", body="olivine crystals in basalt")
    upload = client.post(
        "/api/documents",
        files={"file": ("core.pdf", pdf, "application/pdf")},
        data={"extra_tags": "basalt, internal"},
    )
    assert upload.status_code == 200, upload.text
    payload = upload.json()
    assert payload["title"] == "Core Sample Report"
    assert payload["authors"][0]["name"] == "Ada Geologist"
    assert payload["content_tags"] == ["basalt", "internal"]
    assert payload["pdf_metadata"]["document_id"] == payload["document_id"]
    assert payload["pdf_metadata"]["document_hash"] == payload["document_hash"]
    assert payload["share_points"] == 0
    assert len(payload["document_hash"]) == 64

    found = client.get("/api/documents", params={"q": "Core"})
    assert found.status_code == 200
    assert found.json()[0]["document_id"] == payload["document_id"]

    tagged = client.get("/api/documents", params={"q": "basalt"})
    assert tagged.json()[0]["document_id"] == payload["document_id"]

    meta = client.get(f"/api/documents/{payload['document_id']}")
    assert meta.json()["document_hash"] == payload["document_hash"]

    download = client.get(f"/api/documents/{payload['document_id']}/file")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content[:4] == b"%PDF"

    after = client.get(f"/api/documents/{payload['document_id']}")
    assert after.json()["share_points"] == 1


def test_rejects_non_pdf(client: TestClient):
    identify(client)
    response = client.post(
        "/api/documents",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400


def test_accepts_tags_without_an_expert(client: TestClient):
    identify(client, name="Ada Geologist", tags="basalt")
    pdf = build_pdf(title="Memo", body="notes")
    response = client.post(
        "/api/documents",
        files={"file": ("memo.pdf", pdf, "application/pdf")},
        data={"new_tags": "olivine, field-notes"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["content_tags"] == ["olivine", "field-notes"]
    assert response.json()["verified_content_tags"] == []


def test_same_document_id_adds_author_when_hash_matches(client: TestClient):
    identify(client, name="Ada Geologist", tags="basalt")
    pdf = build_pdf(title="Shared Report", body="field notes")
    first = client.post(
        "/api/documents",
        files={"file": ("shared.pdf", pdf, "application/pdf")},
        data={"content_tags": "basalt"},
    )
    assert first.status_code == 200, first.text
    stored = client.get(f"/api/documents/{first.json()['document_id']}/file")
    identify(client, name="Bea Mapper", tags="maps")
    second = client.post(
        "/api/documents",
        files={"file": ("shared.pdf", stored.content, "application/pdf")},
        data={"content_tags": "maps"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["document_id"] == first.json()["document_id"]
    names = [item["name"] for item in second.json()["authors"]]
    assert names == ["Ada Geologist", "Bea Mapper"]


def test_changed_file_keeps_author_chain_with_new_id(client: TestClient):
    identify(client, name="Ada Geologist", tags="basalt")
    pdf = build_pdf(title="Evolving Report", body="version one")
    first = client.post(
        "/api/documents",
        files={"file": ("evo.pdf", pdf, "application/pdf")},
        data={"content_tags": "basalt"},
    )
    stored = client.get(f"/api/documents/{first.json()['document_id']}/file")
    changed = with_changed_pages(stored.content)
    identify(client, name="Bea Mapper", tags="maps")
    second = client.post(
        "/api/documents",
        files={"file": ("evo.pdf", changed, "application/pdf")},
        data={"content_tags": "maps"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["document_id"] != first.json()["document_id"]
    assert second.json()["document_hash"] != first.json()["document_hash"]
    assert second.json()["predecessor_id"] == first.json()["document_id"]
    names = [item["name"] for item in second.json()["authors"]]
    assert names == ["Ada Geologist", "Bea Mapper"]
    assert second.json()["content_tags"] == ["maps"]


def test_writing_over_a_file_stores_a_new_version(client: TestClient):
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import FreeText

    identify(client, name="Ada Geologist", tags="basalt")
    pdf = build_pdf(title="Marked Report", body="version one")
    first = client.post(
        "/api/documents",
        files={"file": ("marked.pdf", pdf, "application/pdf")},
        data={"content_tags": "basalt"},
    )
    assert first.status_code == 200, first.text
    stored = client.get(f"/api/documents/{first.json()['document_id']}/file")
    reader = PdfReader(BytesIO(stored.content))
    writer = PdfWriter()
    writer.append(reader)
    if reader.metadata:
        writer.add_metadata({key: value for key, value in reader.metadata.items()})
    writer.add_annotation(
        page_number=0,
        annotation=FreeText(text="correction in the margin", rect=(30, 30, 180, 80)),
    )
    edited = BytesIO()
    writer.write(edited)
    second = client.post(
        "/api/documents",
        files={"file": ("marked.pdf", edited.getvalue(), "application/pdf")},
        data={"content_tags": "basalt"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["document_id"] != first.json()["document_id"]
    assert second.json()["predecessor_id"] == first.json()["document_id"]
    assert [item["name"] for item in second.json()["authors"]] == ["Ada Geologist"]
    found = client.get("/api/documents", params={"q": "correction"})
    assert found.json()[0]["document_id"] == second.json()["document_id"]
    page = client.get("/library", params={"q": "Marked"})
    assert "New version of Marked Report" in page.text


def test_same_content_without_document_id_adds_author(client: TestClient):
    identify(client, name="Ada Geologist", tags="basalt")
    pdf = build_pdf(title="Unstamped Report", body="field notes")
    first = client.post(
        "/api/documents",
        files={"file": ("unstamped.pdf", pdf, "application/pdf")},
        data={"content_tags": "basalt"},
    )
    assert first.status_code == 200, first.text
    identify(client, name="Bea Mapper", tags="maps")
    second = client.post(
        "/api/documents",
        files={"file": ("unstamped.pdf", pdf, "application/pdf")},
        data={"content_tags": "maps"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["document_id"] == first.json()["document_id"]
    names = [item["name"] for item in second.json()["authors"]]
    assert names == ["Ada Geologist", "Bea Mapper"]


def test_search_ranks_by_share_points(client: TestClient):
    identify(client, tags="basalt")
    first = client.post(
        "/api/documents",
        files={"file": ("a.pdf", build_pdf(title="Alpha Note", body="shared topic"), "application/pdf")},
        data={"content_tags": "basalt"},
    )
    second = client.post(
        "/api/documents",
        files={"file": ("b.pdf", build_pdf(title="Beta Note", body="shared topic", extra_pages=1), "application/pdf")},
        data={"content_tags": "basalt"},
    )
    client.get(f"/api/documents/{first.json()['document_id']}/file")
    client.get(f"/api/documents/{first.json()['document_id']}/file")
    neutral = client.get("/api/documents", params={"q": "shared"})
    assert [item["document_id"] for item in neutral.json()][0] == second.json()["document_id"]
    ranked = client.get("/api/documents", params={"q": "shared", "w_popularity": 10})
    ids = [item["document_id"] for item in ranked.json()]
    assert ids[0] == first.json()["document_id"]
    assert ranked.json()[0]["rank_score"] > ranked.json()[1]["rank_score"]


def test_filters_only_existing_tags_and_authors(client: TestClient):
    identify(client, name="Ada Geologist", tags="basalt")
    client.post(
        "/api/documents",
        files={"file": ("a.pdf", build_pdf(title="Tagged", body="notes"), "application/pdf")},
        data={"content_tags": "basalt"},
    )
    page = client.get("/library")
    assert '<select name="tag"' in page.text
    assert '<select name="author"' in page.text
    assert ">basalt<" in page.text
    assert ">Ada Geologist<" in page.text
    assert 'type="text" name="tag"' not in page.text
    missing = client.get("/api/documents", params={"tag": "not-a-real-tag"})
    assert missing.json() == []
    unknown_author = client.get("/api/documents", params={"author": "Nobody"})
    assert unknown_author.json() == []


def test_weighted_verified_country_and_ai(client: TestClient):
    identify(client, name="Ada Geologist", tags="basalt")
    client.post(
        "/auth",
        data={"name": "Ada Geologist", "content_tags": "basalt", "verified": "yes"},
        follow_redirects=False,
    )
    verified = client.post(
        "/api/documents",
        files={"file": ("ada.pdf", build_pdf(title="Ada Note", body="shared quarry"), "application/pdf")},
        data={"content_tags": "basalt", "country": "France", "ai_used": "yes"},
    )
    identify(client, name="Bea Mapper", tags="basalt")
    other = client.post(
        "/api/documents",
        files={"file": ("bea.pdf", build_pdf(title="Bea Note", body="shared quarry", extra_pages=1), "application/pdf")},
        data={"content_tags": "basalt", "country": "Japan", "ai_used": "no"},
    )
    by_verified = client.get(
        "/api/documents",
        params={"q": "quarry", "prefer_verified": "yes", "w_verified": 10},
    )
    assert by_verified.json()[0]["document_id"] == verified.json()["document_id"]
    by_country = client.get(
        "/api/documents",
        params={"q": "quarry", "country": "Japan", "w_country": 10},
    )
    assert by_country.json()[0]["document_id"] == other.json()["document_id"]
    by_ai = client.get(
        "/api/documents",
        params={"q": "quarry", "prefer_ai": "no", "w_ai": 10},
    )
    assert by_ai.json()[0]["document_id"] == other.json()["document_id"]
    assert "AI" in verified.json()["content_tags"]
    assert verified.json()["country"] == "France"
    assert verified.json()["ai_used"] is True


def test_per_tag_verification_ranks_covered_tags(client: TestClient):
    client.post(
        "/auth",
        data={
            "name": "Ada Geologist",
            "content_tags": "basalt, maps",
            "verified_tags": "basalt",
        },
        follow_redirects=False,
    )
    covered = client.post(
        "/api/documents",
        files={"file": ("covered.pdf", build_pdf(title="Covered Note", body="shared ledge"), "application/pdf")},
        data={"new_tags": "basalt"},
    )
    identify(client, name="Bea Mapper", tags="")
    bare = client.post(
        "/api/documents",
        files={"file": ("bare.pdf", build_pdf(title="Bare Note", body="shared ledge", extra_pages=1), "application/pdf")},
        data={"new_tags": "olivine"},
    )
    ranked = client.get(
        "/api/documents",
        params={"q": "ledge", "prefer_tag_verified": "yes", "w_tag_verified": 10},
    )
    assert ranked.json()[0]["document_id"] == covered.json()["document_id"]
    assert "basalt" in ranked.json()[0]["verified_content_tags"]
    assert bare.json()["verified_content_tags"] == []
    assert ranked.json()[0]["authors"][0]["verified_tags"] == ["basalt"]
