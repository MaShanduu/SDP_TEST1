"""SQLite schema and connection helpers.

Design notes
------------
* Everything lives in ONE SQLite database file with a ``repo_id`` discriminator
  column: multi-repository support without cross-file joins, and WAL mode lets
  the ingestion worker write while API requests read concurrently.
* ``commits`` stores one row per non-merge commit (:math:`\\bar{H}` reachable
  from HEAD), including mailmapped **and** raw author identity, plus the
  commit's aggregate line counts.
* ``changes`` stores one row per (commit, file) pair -- exactly the unit that
  the spec's file metrics (``l+``, ``l-``, ``delta``, ``lambda``) are defined
  on. Directory / repository / author metrics are set-based rollups of this
  table, so they are computed in SQL (O(rows)) instead of per-commit recursion.
* ``paths`` holds the file/directory universe of the repo (HEAD tree plus every
  historical path that was ever touched), used by the path picker.
* ``ancestry`` caches ``git rev-list`` results for analysis rooted at a
  reference commit :math:`h_r` other than HEAD.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS repos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    source          TEXT    NOT NULL CHECK (source IN ('url', 'zip')),
    source_ref      TEXT    NOT NULL,               -- clone URL or uploaded filename
    root_path       TEXT    NOT NULL,               -- on-disk path usable with `git -C`
    head_sha        TEXT,
    branch          TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    error           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ingested_at     TEXT,
    n_commits       INTEGER NOT NULL DEFAULT 0,
    n_files         INTEGER NOT NULL DEFAULT 0,
    n_dirs          INTEGER NOT NULL DEFAULT 0,
    n_identities    INTEGER NOT NULL DEFAULT 0,
    n_authors       INTEGER NOT NULL DEFAULT 0,
    total_added     INTEGER NOT NULL DEFAULT 0,
    total_removed   INTEGER NOT NULL DEFAULT 0,
    first_ct        INTEGER,
    last_ct         INTEGER
);

CREATE TABLE IF NOT EXISTS commits (
    repo_id         INTEGER NOT NULL,
    seq             INTEGER NOT NULL,               -- ingestion order (stable tie-break)
    sha             TEXT    NOT NULL,
    parent_sha      TEXT,                           -- first parent (NULL for root commit)
    subject         TEXT    NOT NULL DEFAULT '',
    identity_id     INTEGER NOT NULL,               -- post-mailmap identity
    author_id       INTEGER NOT NULL,               -- effective author (after manual merges)
    ct              INTEGER NOT NULL,               -- committer date, unix seconds
    added           INTEGER NOT NULL DEFAULT 0,
    removed         INTEGER NOT NULL DEFAULT 0,
    n_files         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo_id, sha)
);

CREATE TABLE IF NOT EXISTS changes (
    repo_id         INTEGER NOT NULL,
    sha             TEXT    NOT NULL,
    seq             INTEGER NOT NULL,               -- order inside the commit
    path            TEXT    NOT NULL,               -- post-rename (new) path
    old_path        TEXT,                           -- pre-rename path, NULL if not a rename
    added           INTEGER NOT NULL DEFAULT 0,
    removed         INTEGER NOT NULL DEFAULT 0,
    is_binary       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo_id, sha, seq)
);

CREATE TABLE IF NOT EXISTS paths (
    repo_id         INTEGER NOT NULL,
    path            TEXT    NOT NULL,
    kind            TEXT    NOT NULL CHECK (kind IN ('file', 'dir')),
    binary          INTEGER NOT NULL DEFAULT 0,     -- observed as binary in some diff
    at_head         INTEGER NOT NULL DEFAULT 0,     -- present in the HEAD tree
    PRIMARY KEY (repo_id, path)
);

CREATE TABLE IF NOT EXISTS authors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    email           TEXT    NOT NULL,
    alias_of        INTEGER,                        -- NULL = effective author, else merged into
    is_manual       INTEGER NOT NULL DEFAULT 0      -- display name edited by the user
);

CREATE TABLE IF NOT EXISTS raw_identities (
    repo_id         INTEGER NOT NULL,
    identity_id     INTEGER NOT NULL,
    raw_name        TEXT    NOT NULL,
    raw_email       TEXT    NOT NULL,
    n_commits       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ancestry (
    repo_id         INTEGER NOT NULL,
    ref_sha         TEXT    NOT NULL,
    sha             TEXT    NOT NULL,
    PRIMARY KEY (repo_id, ref_sha, sha)
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id         INTEGER,
    kind            TEXT    NOT NULL,               -- ingest_url | ingest_zip
    status          TEXT    NOT NULL DEFAULT 'queued',
    phase           TEXT,
    progress        REAL    NOT NULL DEFAULT 0,
    message         TEXT,
    error           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_commits_ct       ON commits (repo_id, ct);
CREATE INDEX IF NOT EXISTS idx_commits_seq      ON commits (repo_id, seq);
CREATE INDEX IF NOT EXISTS idx_commits_author   ON commits (repo_id, author_id);
CREATE INDEX IF NOT EXISTS idx_commits_identity ON commits (repo_id, identity_id);
-- Covering index for path-scoped aggregates (dir/file metrics).
CREATE INDEX IF NOT EXISTS idx_changes_path     ON changes (repo_id, path, added, removed, sha);
CREATE INDEX IF NOT EXISTS idx_changes_sha      ON changes (repo_id, sha);
CREATE INDEX IF NOT EXISTS idx_paths_parent     ON paths (repo_id, kind, path);
CREATE INDEX IF NOT EXISTS idx_authors_repo     ON authors (repo_id, alias_of);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with sane defaults (one per request / thread).

    ``check_same_thread=False`` is required because FastAPI runs sync
    dependencies and endpoints in a threadpool: the same per-request connection
    may be created, used and closed from different worker threads (sequentially,
    never concurrently), which SQLite otherwise rejects.
    """
    path = db_path or settings.db_path
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create the schema if it does not exist yet."""
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def txn(conn: sqlite3.Connection):
    """Transaction scope for a connection."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
