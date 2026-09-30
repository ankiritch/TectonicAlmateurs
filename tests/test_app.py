from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
def build_pdf(*, title: str, body: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=400)
    # Store searchable body in metadata as well; page text extraction is optional.
    writer.add_metadata({"/Title": title, "/Subject": body})
    # Best-effort text on page via /Contents is not trivial; FTS also indexes title/tags.
    output = BytesIO()
    writer.write(output)
    data = output.getvalue()
    del page
    return data


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "library.db"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    from app.db import reset_engine, init_db
    from app.config import ensure_data_dirs

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
        data={"extra_tags": "lab-notes"},
    )
    assert upload.status_code == 200, upload.text
    payload = upload.json()
    assert payload["title"] == "Core Sample Report"
    assert payload["author"]["name"] == "Ada Geologist"
    assert "basalt" in payload["content_tags"]
    assert "lab-notes" in payload["content_tags"]
    assert payload["pdf_metadata"]["Author"] == "Ada Geologist"
    assert len(payload["sha256"]) == 64

    found = client.get("/api/documents", params={"q": "Core"})
    assert found.status_code == 200
    assert found.json()[0]["id"] == payload["id"]

    tagged = client.get("/api/documents", params={"tag": "lab-notes"})
    assert tagged.json()[0]["id"] == payload["id"]

    meta = client.get(f"/api/documents/{payload['id']}")
    assert meta.json()["sha256"] == payload["sha256"]

    download = client.get(f"/api/documents/{payload['id']}/file")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content[:4] == b"%PDF"


def test_rejects_non_pdf(client: TestClient):
    identify(client)
    response = client.post(
        "/api/documents",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400
