"""Pytest configuration: isolated data dir + a small environment helper."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from rat import ingest  # noqa: E402
from rat.config import settings  # noqa: E402
from rat.db import connect, init_db  # noqa: E402
from rat import metrics as m  # noqa: E402
from rat import authors as authors_svc  # noqa: E402
from tests.fixtures import RepoBuilder  # noqa: E402


class Env:
    """Convenience wrapper around the RAT services for tests."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path

    # -- build / ingest ---------------------------------------------------

    def builder(self, name: str, base_ct: int = 1_700_000_000) -> RepoBuilder:
        return RepoBuilder(self.tmp / f"repo_{name}", base_ct=base_ct)

    def ingest(self, target: RepoBuilder | Path, name: str | None = None) -> int:
        path = target.path if isinstance(target, RepoBuilder) else Path(target)
        conn = connect()
        cur = conn.execute(
            "INSERT INTO repos (name, source, source_ref, root_path, status) VALUES (?,?,?,?, 'pending')",
            (name or path.name, "zip", path.name, str(path)),
        )
        repo_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO jobs (repo_id, kind, status) VALUES (?, 'ingest_zip', 'queued')", (repo_id,)
        )
        job_id = cur.lastrowid
        conn.commit()
        conn.close()
        ingest.run_local_ingest(repo_id, path, job_id)
        row = self.repo(repo_id)
        assert row["status"] == "ready", f"ingest failed: {row['error']}"
        return repo_id

    # -- queries ----------------------------------------------------------

    def repo(self, repo_id: int):
        conn = connect()
        try:
            return conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
        finally:
            conn.close()

    def _with_conn(self, fn):
        conn = connect()
        try:
            return fn(conn)
        finally:
            conn.close()

    def bundle(self, repo_id: int, path: str = "", **cs_kwargs) -> dict:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            scope = m.resolve_scope(conn, repo_id, path)
            return m.metrics_bundle(conn, repo, scope, m.CommitSet(**cs_kwargs))

        return self._with_conn(run)

    def contributors(self, repo_id: int, path: str = "", **cs_kwargs) -> list[dict]:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            scope = m.resolve_scope(conn, repo_id, path)
            return m.contributors(conn, repo, scope, m.CommitSet(**cs_kwargs))

        return self._with_conn(run)

    def files(self, repo_id: int, path: str = "", **cs_kwargs) -> dict:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            scope = m.resolve_scope(conn, repo_id, path)
            cs = m.CommitSet(**{k: v for k, v in cs_kwargs.items() if k in {"ref", "since", "until", "shas", "author_ids"}})
            kwargs = {k: v for k, v in cs_kwargs.items() if k not in {"ref", "since", "until", "shas", "author_ids"}}
            return m.file_rows(conn, repo, scope, cs, **kwargs)

        return self._with_conn(run)

    def tree(self, repo_id: int, path: str = "", **cs_kwargs) -> dict:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            scope = m.resolve_scope(conn, repo_id, path)
            return m.tree(conn, repo, scope, m.CommitSet(**cs_kwargs))

        return self._with_conn(run)

    def series(self, repo_id: int, path: str = "", bucket: str = "auto", **cs_kwargs) -> dict:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            scope = m.resolve_scope(conn, repo_id, path)
            return m.series(conn, repo, scope, m.CommitSet(**cs_kwargs), bucket)

        return self._with_conn(run)

    def commits_page(self, repo_id: int, path: str = "", **kwargs) -> dict:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            scope = m.resolve_scope(conn, repo_id, path)
            return m.commits_page(conn, repo, m.CommitSet(), scope=scope, **kwargs)

        return self._with_conn(run)

    def commit_detail(self, repo_id: int, sha: str) -> dict:
        def run(conn):
            repo = conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()
            return m.commit_detail(conn, repo, sha)

        return self._with_conn(run)

    # -- authors ----------------------------------------------------------

    def authors(self, repo_id: int) -> list[dict]:
        return self._with_conn(lambda conn: authors_svc.list_authors(conn, repo_id))

    def author_id(self, repo_id: int, name: str) -> int:
        for a in self.authors(repo_id):
            if a["name"] == name:
                return a["id"]
        raise AssertionError(f"author {name!r} not found")

    def merge(self, repo_id: int, target: int, sources: list[int]) -> dict:
        return self._with_conn(lambda conn: authors_svc.merge_authors(conn, repo_id, target, sources))

    def detach(self, repo_id: int, identity_id: int) -> dict:
        return self._with_conn(lambda conn: authors_svc.detach_identity(conn, repo_id, identity_id))


@pytest.fixture()
def rat(tmp_path, monkeypatch) -> Env:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    settings.ensure_dirs()
    init_db()
    return Env(tmp_path)
