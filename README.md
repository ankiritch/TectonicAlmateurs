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

- Identity: name, content tags, per-tag verification, and a separate verified-author flag
- Upload: PDF only. Pick existing tags or type new ones (no expert is required). Also set a place label (country, continent, or Worldwide) and whether AI was used. The PDF is stamped with `document_id` and `document_hash`
- Same `document_id` and hash: the uploader is added to the document’s author list
- Same `document_id` but a different hash: a new document is stored (new id, hash, upload date, tags) with the previous author chain
- If `document_id` is missing but the content hash uniquely matches, the uploader is still added to that author list
- Server record: authors, `document_hash`, `document_id`, `upload_date`, content tags, share points
- Search: match content and content tags. Tag and author filters are dropdowns of values that already exist
- Ranking: a side panel weights verified author, per-tag verification, document age, place, AI use, and share points from 0 to 10. The score is the weighted average of those criteria (`app/ranking.py`). Weights stay in the page and in session storage until Reset
- Download: awards one share point
