import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))


def upload_dir() -> Path:
    return data_dir() / "uploads"


def db_path() -> Path:
    return Path(os.environ.get("DATABASE_PATH", data_dir() / "library.db"))


def session_secret() -> str:
    return os.environ.get("SESSION_SECRET", "dev-secret-change-me")


def ensure_data_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    upload_dir().mkdir(parents=True, exist_ok=True)
