"""Repository management endpoints: create (url/zip), list, detail, delete, jobs."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import gitio, ingest
from ..config import settings
from ..db import txn
from ..models import RepoPatchIn, UrlRepoIn, job_dict, repo_dict
from .deps import db_conn, load_repo

router = APIRouter(tags=["repositories"])


def _latest_jobs(conn) -> dict[int, object]:
    rows = conn.execute(
        """
        SELECT j.* FROM jobs j
        JOIN (SELECT repo_id, MAX(id) AS mid FROM jobs GROUP BY repo_id) t ON t.mid = j.id
        """
    ).fetchall()
    return {r["repo_id"]: r for r in rows}


def _new_repo(conn, *, name: str, source: str, source_ref: str, job_kind: str) -> tuple[int, int]:
    cur = conn.execute(
        "INSERT INTO repos (name, source, source_ref, root_path, status) VALUES (?,?,?,'','pending')",
        (name, source, source_ref),
    )
    repo_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO jobs (repo_id, kind, status) VALUES (?,?, 'queued')", (repo_id, job_kind)
    )
    conn.commit()
    return repo_id, cur.lastrowid


def _repo_name_from_url(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1] or "repository"
    return tail[:-4] if tail.endswith(".git") else tail


@router.post("/repos/url", status_code=202)
def create_repo_from_url(body: UrlRepoIn, conn=Depends(db_conn)) -> dict:
    """Clone a remote repository (deep, bare) and ingest it in the background."""
    try:
        url = gitio.validate_clone_url(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    name = (body.name or "").strip() or _repo_name_from_url(url)
    repo_id, job_id = _new_repo(conn, name=name, source="url", source_ref=url, job_kind="ingest_url")
    root = settings.repo_dir(repo_id) / "repo"
    with txn(conn):
        conn.execute("UPDATE repos SET root_path = ? WHERE id = ?", (str(root), repo_id))
    ingest.enqueue_job(job_id)
    row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return {"repo": repo_dict(row, job), "job_id": job_id}


@router.post("/repos/zip", status_code=202)
def create_repo_from_zip(
    file: UploadFile = File(..., description="Zip archive containing the repository (.git included)"),
    name: str | None = Form(None),
    conn=Depends(db_conn),
) -> dict:
    """Upload a repository zip (must include the ``.git`` directory) for ingestion."""
    filename = (file.filename or "upload.zip").strip()
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip archives are supported.")

    repo_id, job_id = _new_repo(
        conn,
        name=(name or "").strip() or filename[: -len(".zip")] or "repository",
        source="zip",
        source_ref=filename,
        job_kind="ingest_zip",
    )
    repo_dir = settings.repo_dir(repo_id)
    repo_dir.mkdir(parents=True, exist_ok=True)
    upload_path = repo_dir / "upload.zip"

    total = 0
    limit = settings.max_upload_bytes
    try:
        with open(upload_path, "wb") as out:
            while True:
                chunk = file.file.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds the {limit // (1024 * 1024)} MiB limit.",
                    )
                out.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    except HTTPException:
        shutil.rmtree(repo_dir, ignore_errors=True)
        with txn(conn):
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        raise

    root = repo_dir / "extracted"
    with txn(conn):
        conn.execute("UPDATE repos SET root_path = ? WHERE id = ?", (str(root), repo_id))
    ingest.enqueue_job(job_id)
    row = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return {"repo": repo_dict(row, job), "job_id": job_id}


@router.get("/repos")
def list_repos(conn=Depends(db_conn)) -> list[dict]:
    """All repositories with their latest ingestion job."""
    jobs = _latest_jobs(conn)
    rows = conn.execute("SELECT * FROM repos ORDER BY id DESC").fetchall()
    return [repo_dict(r, jobs.get(r["id"])) for r in rows]


@router.get("/repos/{repo_id}")
def get_repo(repo_id: int, conn=Depends(db_conn)) -> dict:
    repo = load_repo(repo_id, conn)
    job = conn.execute(
        "SELECT * FROM jobs WHERE repo_id = ? ORDER BY id DESC LIMIT 1", (repo_id,)
    ).fetchone()
    return repo_dict(repo, job)


@router.patch("/repos/{repo_id}")
def patch_repo(repo_id: int, body: RepoPatchIn, conn=Depends(db_conn)) -> dict:
    load_repo(repo_id, conn)
    with txn(conn):
        conn.execute("UPDATE repos SET name = ? WHERE id = ?", (body.name.strip(), repo_id))
    return get_repo(repo_id, conn)


@router.delete("/repos/{repo_id}", status_code=204)
def delete_repo(repo_id: int, conn=Depends(db_conn)) -> None:
    """Remove a repository: database rows and all files on disk."""
    repo = load_repo(repo_id, conn)
    active = conn.execute(
        "SELECT 1 FROM jobs WHERE repo_id = ? AND status IN ('queued','running') LIMIT 1",
        (repo_id,),
    ).fetchone()
    if active is not None:
        raise HTTPException(status_code=409, detail="Cannot delete a repository while ingestion is running.")
    with txn(conn):
        for table in ("commits", "changes", "paths", "authors", "raw_identities", "ancestry", "jobs"):
            conn.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))
        conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
    shutil.rmtree(settings.repo_dir(repo["id"]), ignore_errors=True)


@router.get("/repos/{repo_id}/jobs")
def repo_jobs(repo_id: int, conn=Depends(db_conn)) -> list[dict]:
    load_repo(repo_id, conn)
    rows = conn.execute(
        "SELECT * FROM jobs WHERE repo_id = ? ORDER BY id DESC LIMIT 20", (repo_id,)
    ).fetchall()
    return [job_dict(r) for r in rows]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, conn=Depends(db_conn)) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return job_dict(row)
