# TectonicAlmateurs

Prototype of an internal PDF document library: authors identify themselves, upload PDFs, search the archive, and download files. The server stores author and document records in SQLite, stamps PDF metadata, and keeps a SHA-256 hash of each stored file.

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

- Identity page: name + content tags, stored as an author row and a signed session cookie (no passwords yet)
- Upload: PDF only; metadata is assigned (title, author, keywords) and read back into the database
- Search: SQLite FTS5 over title, tags, author name, and extracted PDF text
- Download: original stored PDF by document id
- Trustworthiness (light): SHA-256 of the stored file, author attribution, upload timestamp
