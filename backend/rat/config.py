"""Application settings.

Everything is overridable through environment variables so the app works both
locally (`uvicorn rat.api:app`) and inside Docker without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# backend/rat/config.py -> project root is two levels up from the `rat` package.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Runtime configuration for the RAT."""

    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("RAT_DATA_DIR", str(DEFAULT_DATA_DIR)))
    )
    # Zip upload guards (defence against zip bombs / runaway extraction).
    max_upload_bytes: int = field(default_factory=lambda: _env_int("RAT_MAX_UPLOAD_BYTES", 2 * 1024**3))
    max_zip_entries: int = field(default_factory=lambda: _env_int("RAT_MAX_ZIP_ENTRIES", 1_000_000))
    max_zip_uncompressed: int = field(
        default_factory=lambda: _env_int("RAT_MAX_ZIP_UNCOMPRESSED", 10 * 1024**3)
    )
    # How many commits are buffered before being flushed to SQLite.
    ingest_batch_commits: int = field(default_factory=lambda: _env_int("RAT_INGEST_BATCH_COMMITS", 2000))
    # Upper bound on the number of commits accepted in an explicit commit-list filter.
    max_commit_list: int = field(default_factory=lambda: _env_int("RAT_MAX_COMMIT_LIST", 5000))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rat.db"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    def repo_dir(self, repo_id: int) -> Path:
        return self.repos_dir / str(repo_id)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.repos_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
