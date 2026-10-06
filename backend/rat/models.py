"""Pydantic request models and small serializers for API responses."""

from __future__ import annotations

import sqlite3

from pydantic import BaseModel, Field


class UrlRepoIn(BaseModel):
    url: str = Field(..., description="Remote clone URL (https://, ssh://, git:// or git@host:path)")
    name: str | None = Field(None, description="Display name (defaults to the URL's repo name)")


class RepoPatchIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class MergeIn(BaseModel):
    target_id: int = Field(..., description="Effective author to merge into")
    source_ids: list[int] = Field(..., description="Identity/author ids to fold into the target")


class AuthorPatchIn(BaseModel):
    name: str | None = None
    email: str | None = None


def job_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "repo_id": row["repo_id"],
        "kind": row["kind"],
        "status": row["status"],
        "phase": row["phase"],
        "progress": row["progress"],
        "message": row["message"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def repo_dict(row: sqlite3.Row, job: sqlite3.Row | None = None) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "source_ref": row["source_ref"],
        "status": row["status"],
        "error": row["error"],
        "branch": row["branch"],
        "head_sha": row["head_sha"],
        "created_at": row["created_at"],
        "ingested_at": row["ingested_at"],
        "n_commits": row["n_commits"],
        "n_files": row["n_files"],
        "n_dirs": row["n_dirs"],
        "n_identities": row["n_identities"],
        "n_authors": row["n_authors"],
        "total_added": row["total_added"],
        "total_removed": row["total_removed"],
        "first_ct": row["first_ct"],
        "last_ct": row["last_ct"],
        "job": job_dict(job),
    }
