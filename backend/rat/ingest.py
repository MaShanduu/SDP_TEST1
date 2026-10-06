"""Repository ingestion pipeline.

Two ingestion modes (spec §1):

* **remote URL** -> bare ("deep") clone with progress reporting;
* **zip upload** -> validated, path-traversal-safe extraction.

Both funnel into :func:`_index_repo`, which streams a single ``git log``
process (see :mod:`rat.gitio`) and batch-inserts rows into SQLite in one
transaction per N commits.  Memory usage is O(batch); the streaming design is
what makes ~100k-commit repositories feasible in seconds-to-minutes.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import gitio
from .config import settings
from .db import connect, txn

log = logging.getLogger("rat.ingest")


class IngestError(RuntimeError):
    """Raised for user-fixable ingestion failures (bad zip, bad URL, ...)."""


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

_job_queue: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_stop_event = threading.Event()

# Columns refreshed on the repos summary row once ingestion completes.
_SUMMARY_SQL = """
UPDATE repos SET
    status        = 'ready',
    error         = NULL,
    ingested_at   = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
    head_sha      = :head_sha,
    branch        = :branch,
    n_commits     = (SELECT COUNT(*) FROM commits      WHERE repo_id = :rid),
    n_files       = (SELECT COUNT(*) FROM paths        WHERE repo_id = :rid AND kind = 'file'),
    n_dirs        = (SELECT COUNT(*) FROM paths        WHERE repo_id = :rid AND kind = 'dir'),
    n_identities  = (SELECT COUNT(*) FROM authors      WHERE repo_id = :rid AND alias_of IS NULL),
    n_authors     = (SELECT COUNT(*) FROM authors      WHERE repo_id = :rid AND alias_of IS NULL),
    total_added   = (SELECT COALESCE(SUM(added), 0)    FROM commits WHERE repo_id = :rid),
    total_removed = (SELECT COALESCE(SUM(removed), 0)  FROM commits WHERE repo_id = :rid),
    first_ct      = (SELECT MIN(ct) FROM commits       WHERE repo_id = :rid),
    last_ct       = (SELECT MAX(ct) FROM commits       WHERE repo_id = :rid)
WHERE id = :rid
"""


def start_worker() -> None:
    """Start the single ingestion worker thread (idempotent)."""
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _stop_event.clear()
        _worker = threading.Thread(target=_worker_loop, name="rat-ingest-worker", daemon=True)
        _worker.start()


def stop_worker() -> None:
    _stop_event.set()


def enqueue_job(job_id: int) -> None:
    _job_queue.put(job_id)


def mark_interrupted_jobs() -> None:
    """Fail jobs/repos that were mid-flight when the process last exited."""
    conn = connect()
    try:
        with txn(conn):
            conn.execute(
                "UPDATE jobs SET status='error', error='Interrupted by server restart', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE status IN ('queued','running')"
            )
            conn.execute(
                "UPDATE repos SET status='error', error='Ingestion interrupted by server restart' "
                "WHERE status IN ('pending','preparing','ingesting')"
            )
    finally:
        conn.close()


def _worker_loop() -> None:
    while not _stop_event.is_set():
        try:
            job_id = _job_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            run_job(job_id)
        except Exception:  # never let the worker die
            log.exception("ingestion job %s crashed", job_id)


def _update_job(conn, job_id: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE jobs SET {sets}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
        (*fields.values(), job_id),
    )
    conn.commit()


def run_job(job_id: int) -> None:
    """Execute one queued ingestion job end-to-end."""
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None or job["status"] != "queued":
            return
        repo = conn.execute("SELECT * FROM repos WHERE id = ?", (job["repo_id"],)).fetchone()
        if repo is None:
            _update_job(conn, job_id, status="error", error="Repository row missing")
            return

        with txn(conn):
            conn.execute("UPDATE repos SET status='preparing', error=NULL WHERE id=?", (repo["id"],))
        _update_job(conn, job_id, status="running", phase="preparing", progress=0.02, message="Preparing")

        try:
            if job["kind"] == "ingest_url":
                repo_path = _prepare_url(conn, job_id, repo)
            elif job["kind"] == "ingest_zip":
                repo_path = _prepare_zip(conn, job_id, repo)
            else:
                raise IngestError(f"Unknown job kind {job['kind']!r}")
            with txn(conn):
                conn.execute("UPDATE repos SET root_path=? WHERE id=?", (str(repo_path), repo["id"]))
            _index_repo(conn, job_id, repo["id"], repo_path)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            log.warning("ingest job %s failed: %s", job_id, message)
            with txn(conn):
                conn.execute("UPDATE repos SET status='error', error=? WHERE id=?", (message, repo["id"]))
            _update_job(conn, job_id, status="error", phase="failed", message="Ingestion failed", error=message)
            return

        _update_job(conn, job_id, status="done", phase="done", progress=1.0, message="Ready")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Preparation: clone or extract
# ---------------------------------------------------------------------------


def _prepare_url(conn, job_id: int, repo) -> Path:
    from .gitio import GitError

    url = repo["source_ref"]
    dest = Path(repo["root_path"])
    _update_job(conn, job_id, phase="cloning", progress=0.05, message=f"Cloning {url}")
    last = [0.0]

    def on_progress(frac: float, phase_name: str) -> None:
        now = time.monotonic()
        if now - last[0] < 0.4:
            return
        last[0] = now
        _update_job(conn, job_id, progress=0.05 + 0.45 * frac, message=f"Cloning ({phase_name})")

    try:
        gitio.clone_bare(url, dest, on_progress)
    except GitError as exc:
        raise IngestError(str(exc)) from exc
    if not gitio.is_valid_repo(dest):
        raise IngestError("The clone did not produce a usable repository.")
    return dest


def _prepare_zip(conn, job_id: int, repo) -> Path:
    extract_dir = Path(repo["root_path"])
    upload = extract_dir.parent / "upload.zip"
    if not upload.exists():
        raise IngestError("Uploaded archive not found on disk.")

    _update_job(conn, job_id, phase="extracting", progress=0.05, message="Extracting archive")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    _extract_zip_safely(upload, extract_dir, conn, job_id)
    _update_job(conn, job_id, phase="locating", progress=0.45, message="Locating repository")

    git_root = gitio.find_git_root(extract_dir)
    if git_root is None:
        raise IngestError(
            "No usable git repository was found in the uploaded zip. "
            "Re-create the archive so that it includes the repository's .git directory."
        )
    return git_root


def _extract_zip_safely(zip_path: Path, dest: Path, conn, job_id: int) -> None:
    """Extract a zip with zip-slip protection and bomb guards."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise IngestError("Uploaded file is not a valid zip archive.") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > settings.max_zip_entries:
            raise IngestError(f"Zip contains too many entries ({len(infos)}).")
        uncompressed = sum(i.file_size for i in infos)
        if uncompressed > settings.max_zip_uncompressed:
            raise IngestError("Zip uncompressed size exceeds the configured limit.")

        dest_resolved = dest.resolve()
        for n, info in enumerate(infos):
            name = info.filename
            if not name:
                continue
            parts = PurePosixPath(name.replace("\\", "/")).parts
            if name.startswith("/") or ".." in parts:
                raise IngestError(f"Unsafe path in zip: {name!r}")
            target = (dest / name).resolve()
            if target != dest_resolved and dest_resolved not in target.parents:
                raise IngestError(f"Unsafe path in zip: {name!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Symlink entries are materialised as regular files: metric
            # computation never reads working-tree contents, only git objects.
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            if n % 500 == 0:
                _update_job(
                    conn,
                    job_id,
                    progress=0.05 + 0.4 * (n / max(len(infos), 1)),
                    message="Extracting archive",
                )


# ---------------------------------------------------------------------------
# Indexing: stream git log -> SQLite
# ---------------------------------------------------------------------------


def _identity_id(conn, cache: dict[tuple[str, str], int], repo_id: int, name: str, email: str) -> int:
    """Post-mailmap (name, email) -> authors.id, inserting on first sight."""
    key = (name, email)
    hit = cache.get(key)
    if hit is not None:
        return hit
    row = conn.execute(
        "SELECT id FROM authors WHERE repo_id = ? AND name = ? AND email = ? LIMIT 1",
        (repo_id, name, email),
    ).fetchone()
    if row is not None:
        cache[key] = row["id"]
        return row["id"]
    cur = conn.execute(
        "INSERT INTO authors (repo_id, name, email) VALUES (?, ?, ?)", (repo_id, name, email)
    )
    conn.commit()
    cache[key] = cur.lastrowid
    return cur.lastrowid


def _ancestor_dirs(path: str) -> list[str]:
    """['src', 'src/lib'] for 'src/lib/x.c' (root is implicit)."""
    dirs: list[str] = []
    idx = path.find("/")
    while idx != -1:
        dirs.append(path[:idx])
        idx = path.find("/", idx + 1)
    return dirs


def _index_repo(conn, job_id: int, repo_id: int, repo_path: Path) -> None:
    head_sha, branch = gitio.head_info(repo_path)
    if not head_sha:
        raise IngestError("Repository has no commits on HEAD; nothing to measure.")
    total = gitio.count_commits(repo_path)
    if total == 0:
        raise IngestError("Repository has no non-merge commits reachable from HEAD.")

    with txn(conn):
        conn.execute(
            "UPDATE repos SET status='ingesting', head_sha=?, branch=? WHERE id=?",
            (head_sha, branch, repo_id),
        )
    _update_job(conn, job_id, phase="ingesting", progress=0.5, message=f"Indexing {total} commits")

    # Re-ingest support: start from a clean slate for this repository.
    with txn(conn):
        for table in ("commits", "changes", "paths", "authors", "raw_identities", "ancestry"):
            conn.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))

    ins_commit = (
        "INSERT INTO commits (repo_id, seq, sha, parent_sha, subject, identity_id, author_id, ct,"
        " added, removed, n_files) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    )
    ins_change = (
        "INSERT INTO changes (repo_id, sha, seq, path, old_path, added, removed, is_binary)"
        " VALUES (?,?,?,?,?,?,?,?)"
    )

    identities: dict[tuple[str, str], int] = {}
    raw_counts: dict[tuple[int, str, str], int] = {}
    changed_paths: set[str] = set()
    binary_paths: set[str] = set()
    commit_rows: list[tuple] = []
    change_rows: list[tuple] = []

    seq = 0
    last_update = time.monotonic()

    def flush() -> None:
        if not commit_rows and not change_rows:
            return
        with txn(conn):
            conn.executemany(ins_commit, commit_rows)
            conn.executemany(ins_change, change_rows)
        commit_rows.clear()
        change_rows.clear()

    for rec in gitio.stream_log(repo_path):
        identity_id = _identity_id(conn, identities, repo_id, rec.author_name, rec.author_email)
        raw_key = (identity_id, rec.raw_name, rec.raw_email)
        raw_counts[raw_key] = raw_counts.get(raw_key, 0) + 1

        c_added = c_removed = 0
        for idx, ch in enumerate(rec.changes):
            change_rows.append(
                (
                    repo_id,
                    rec.sha,
                    idx,
                    ch.path,
                    ch.old_path,
                    ch.added,
                    ch.removed,
                    1 if ch.is_binary else 0,
                )
            )
            changed_paths.add(ch.path)
            if ch.old_path:
                changed_paths.add(ch.old_path)
            if ch.is_binary:
                binary_paths.add(ch.path)
            c_added += ch.added
            c_removed += ch.removed

        commit_rows.append(
            (
                repo_id,
                seq,
                rec.sha,
                rec.parents[0] if rec.parents else None,
                rec.subject,
                identity_id,
                identity_id,  # author_id == identity until a manual merge happens
                rec.ct,
                c_added,
                c_removed,
                len(rec.changes),
            )
        )
        seq += 1

        if len(commit_rows) >= settings.ingest_batch_commits:
            flush()
            now = time.monotonic()
            if now - last_update > 0.4:
                last_update = now
                _update_job(
                    conn,
                    job_id,
                    progress=0.5 + 0.45 * (seq / total),
                    message=f"Indexing commits ({seq:,}/{total:,})",
                )

    flush()
    _update_job(conn, job_id, phase="finalising", progress=0.97, message="Building path index")

    # ------------------------------------------------------------------
    # Path universe: HEAD tree + everything ever touched (incl. old paths).
    # ------------------------------------------------------------------
    head_files = set(gitio.list_head_files(repo_path))
    all_files = head_files | changed_paths
    dirs: set[str] = set()
    head_dirs: set[str] = set()
    for p in all_files:
        dirs.update(_ancestor_dirs(p))
    for p in head_files:
        head_dirs.update(_ancestor_dirs(p))

    ins_path = "INSERT OR REPLACE INTO paths (repo_id, path, kind, binary, at_head) VALUES (?,?,?,?,?)"
    path_rows = [(repo_id, p, "file", 1 if p in binary_paths else 0, 1 if p in head_files else 0) for p in all_files]
    path_rows += [(repo_id, d, "dir", 0, 1 if d in head_dirs else 0) for d in dirs]
    with txn(conn):
        for i in range(0, len(path_rows), 5000):
            conn.executemany(ins_path, path_rows[i : i + 5000])

    with txn(conn):
        conn.executemany(
            "INSERT INTO raw_identities (repo_id, identity_id, raw_name, raw_email, n_commits)"
            " VALUES (?,?,?,?,?)",
            [(repo_id, iid, rn, rem, n) for (iid, rn, rem), n in raw_counts.items()],
        )

    with txn(conn):
        conn.execute(_SUMMARY_SQL, {"rid": repo_id, "head_sha": head_sha, "branch": branch})

    # The query planner gets fresh stats for the aggregates that follow.
    conn.execute("ANALYZE")
    conn.commit()


def run_local_ingest(repo_id: int, repo_path: Path, job_id: int) -> None:
    """Index an already-prepared repository directory (tests / CLI helpers)."""
    conn = connect()
    try:
        _index_repo(conn, job_id, repo_id, repo_path)
    finally:
        conn.close()
