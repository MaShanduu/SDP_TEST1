"""Read-only metric endpoints (file / directory / repository / set / author)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import metrics as m
from .deps import commit_set_filter, db_conn, load_repo, require_ready

router = APIRouter(prefix="/repos/{repo_id}", tags=["metrics"])


@router.get("/metrics")
def get_metrics(
    repo_id: int,
    path: str = Query("", description="Scope object: repository root, directory or file path"),
    cs: m.CommitSet = Depends(commit_set_filter),
    conn=Depends(db_conn),
) -> dict:
    """All scalar metrics (added/removed/growth/churn/modifications/rates) for an object over a commit set."""
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    scope = m.resolve_scope(conn, repo_id, path)
    data = m.metrics_bundle(conn, repo, scope, cs)
    if not data["scope"]["name"]:
        data["scope"]["name"] = repo["name"]
    return data


@router.get("/series")
def get_series(
    repo_id: int,
    path: str = Query(""),
    bucket: str = Query("auto", pattern="^(auto|day|week|month|commit)$"),
    cs: m.CommitSet = Depends(commit_set_filter),
    conn=Depends(db_conn),
) -> dict:
    """Bucketed added/removed/churn/commit series for charts."""
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    scope = m.resolve_scope(conn, repo_id, path)
    return m.series(conn, repo, scope, cs, bucket)


@router.get("/contributors")
def get_contributors(
    repo_id: int,
    path: str = Query(""),
    cs: m.CommitSet = Depends(commit_set_filter),
    conn=Depends(db_conn),
) -> list[dict]:
    """Per-author churn / modifications / ownership (spec §2.5) for an object over a commit set.

    The author filter is deliberately ignored so ownership shares remain meaningful.
    """
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    scope = m.resolve_scope(conn, repo_id, path)
    return m.contributors(conn, repo, scope, cs)


@router.get("/files")
def get_files(
    repo_id: int,
    path: str = Query(""),
    sort: str = Query("churn", pattern="^(churn|added|removed|mods|commits|path)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    cs: m.CommitSet = Depends(commit_set_filter),
    conn=Depends(db_conn),
) -> dict:
    """Per-file metric rows under a scope (leaderboard tables)."""
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    scope = m.resolve_scope(conn, repo_id, path)
    return m.file_rows(conn, repo, scope, cs, sort=sort, limit=limit, offset=offset)


@router.get("/tree")
def get_tree(
    repo_id: int,
    path: str = Query("", description="Directory whose immediate children are returned"),
    cs: m.CommitSet = Depends(commit_set_filter),
    conn=Depends(db_conn),
) -> dict:
    """Immediate children of a directory with rolled-up metrics per child."""
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    scope = m.resolve_scope(conn, repo_id, path)
    return m.tree(conn, repo, scope, cs)


@router.get("/paths")
def get_paths(
    repo_id: int,
    q: str = Query("", description="Substring match on the path"),
    limit: int = Query(50, ge=1, le=500),
    conn=Depends(db_conn),
) -> list[dict]:
    """Path autocomplete for the file/directory picker."""
    load_repo(repo_id, conn)
    return m.search_paths(conn, repo_id, q, limit)


@router.get("/commits")
def get_commits(
    repo_id: int,
    q: str | None = Query(None, description="Search in subject or sha prefix"),
    path: str = Query("", description="Restrict per-commit line counts to this scope"),
    order: str = Query("newest", pattern="^(newest|oldest)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    cs: m.CommitSet = Depends(commit_set_filter),
    conn=Depends(db_conn),
) -> dict:
    """Paginated commit list, filtered by the commit set and optional search."""
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    scope = m.resolve_scope(conn, repo_id, path)
    return m.commits_page(
        conn, repo, cs, scope=scope, q=q, limit=limit, offset=offset, newest_first=(order == "newest")
    )


@router.get("/commits/{sha}")
def get_commit(repo_id: int, sha: str, conn=Depends(db_conn)) -> dict:
    """A single commit with its per-file line counts (rename-aware)."""
    repo = load_repo(repo_id, conn)
    require_ready(repo)
    return m.commit_detail(conn, repo, sha)
