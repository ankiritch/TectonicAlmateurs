# TectonicAlmateurs

Prototype of an internal PDF document library: authors identify themselves, upload PDFs, search the archive, and download files.

Author **content tags** mean that person is reasonably reliable for those topics. Documents may only use tags that already exist on some author.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 — set your name and content tags, then use **Upload** and **Library**.

Data is written to `data/` (SQLite database + uploaded PDFs). Override with `DATA_DIR` and `DATABASE_PATH` if needed.

## Tests

```bash
pytest
```

## What this prototype does

- Identity: name + content tags (domains of reliability), stored as an author row and a signed session cookie
- Upload: PDF only; you pick content tags from existing authors. The PDF is stamped with `document_id` and `document_hash`
- Same `document_id` and hash: the uploader is added to the document’s author list
- Same `document_id` but a different hash: a new document is stored (new id, hash, upload date, tags) with the previous author chain
- Server record: authors, `document_hash`, `document_id`, `upload_date`, content tags, share points
- Search: match content and content tags, then rank by share points (see `app/ranking.py` to plug in trustworthiness later)
- Download: awards one share point
