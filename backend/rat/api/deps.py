"""Shared FastAPI dependencies: DB connection, repo loading, filter parsing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from fastapi import Depends, HTTPException, Query

from ..config import settings
from ..db import connect
from ..metrics import CommitSet


def db_conn() -> Iterator:
    """One SQLite connection per request (fastapi caches it per request)."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def load_repo(repo_id: int, conn=Depends(db_conn)):
    row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Repository {repo_id} not found.")
    return row


def require_ready(repo) -> None:
    if repo["status"] == "ready":
        return
    if repo["status"] == "error":
        raise HTTPException(
            status_code=409,
            detail=f"Repository ingestion failed: {repo['error'] or 'unknown error'}",
        )
    raise HTTPException(
        status_code=409,
        detail=f"Repository is not ready yet (status: {repo['status']}).",
    )


def _parse_ts(value: str | None, field: str) -> int | None:
    """Accept unix seconds or ISO-8601 (date or datetime; naive = UTC)."""
    if value is None or value.strip() == "":
        return None
    raw = value.strip()
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        if len(raw) == 10:  # YYYY-MM-DD
            dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid '{field}' value {raw!r}: expected unix seconds or ISO-8601.",
        )


def commit_set_filter(
    ref: str | None = Query(None, description="Reference commit h_r (default HEAD)"),
    since: str | None = Query(None, description="Inclusive start: unix seconds or ISO-8601"),
    until: str | None = Query(None, description="Exclusive end: unix seconds or ISO-8601"),
    commits: str | None = Query(
        None, description="Comma-separated commit shas; takes precedence over since/until"
    ),
    authors: str | None = Query(None, description="Comma-separated effective author ids"),
) -> CommitSet:
    """Parse the common commit-set query parameters into a :class:`CommitSet`."""
    shas = None
    if commits is not None and commits.strip():
        shas = [s.strip() for s in commits.split(",") if s.strip()]
        if len(shas) > settings.max_commit_list:
            raise HTTPException(
                status_code=422,
                detail=f"Commit list too large ({len(shas)}); limit is {settings.max_commit_list}.",
            )
    author_ids = None
    if authors is not None and authors.strip():
        try:
            author_ids = [int(a) for a in authors.split(",") if a.strip()]
        except ValueError:
            raise HTTPException(status_code=422, detail="'authors' must be a comma-separated list of ids.")
    return CommitSet(
        ref=ref,
        since=_parse_ts(since, "since"),
        until=_parse_ts(until, "until"),
        shas=shas,
        author_ids=author_ids,
    )
