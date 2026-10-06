"""Metric computation layer.

Everything here is set-based SQL over the ``commits`` / ``changes`` tables.
This is a deliberate architectural choice: the spec defines file metrics on a
(commit, file) pair and directory/author/commit-set metrics as *sums over
those pairs*, so a single indexed scan per query replaces per-commit tree
recursion -- O(rows) instead of O(commits x tree size).

Spec mapping (section numbers from the brief):

* scope object ``o`` is a file (``c.path = o``) or a directory, where for a
  directory we select every descendant via a lexicographic range on the path
  (``>= d || '/'`` and ``< d || '0'``), which is index-friendly.
* commit sets: ``H_t`` = ``ct >= t``, ``H_{i,j}`` = ``i <= ct < j``, explicit
  commit lists, and ``H-bar`` rooted at a reference commit ``h_r`` other than
  HEAD (cached ``rev-list`` in the ``ancestry`` table).
* ``n_{H,o}`` (modifications) counts commits with ``lambda_{h,o} > 0``;
  directory values are recursive, so a commit counts once per directory
  regardless of how many descendant files changed -- hence the
  ``COUNT(DISTINCT sha)`` pattern throughout.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from . import gitio
from .config import settings
from .db import txn


class MetricsError(Exception):
    """User-facing metric query error (invalid path/ref/commit list)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class CommitSet:
    """A commit-set filter: H subset of H-bar (spec §2)."""

    ref: str | None = None  # h_r; None/HEAD = everything indexed
    since: int | None = None  # inclusive (H_t / H_{i,j})
    until: int | None = None  # exclusive
    shas: list[str] | None = None  # explicit commit list (takes precedence)
    author_ids: list[int] | None = None  # restrict to commits by these authors


@dataclass
class Scope:
    path: str = ""
    kind: str = "repo"  # repo | dir | file
    name: str = ""


# ---------------------------------------------------------------------------
# Filter plumbing
# ---------------------------------------------------------------------------


def resolve_scope(conn, repo_id: int, path: str) -> Scope:
    """Turn a user-supplied path into a validated scope object."""
    path = (path or "").strip()
    while path.startswith("./"):
        path = path[2:]
    path = path.strip("/")
    if path in ("", "."):
        return Scope("", "repo", "")
    row = conn.execute(
        "SELECT kind FROM paths WHERE repo_id = ? AND path = ?", (repo_id, path)
    ).fetchone()
    if row is not None:
        return Scope(path, row["kind"], path.rsplit("/", 1)[-1])
    probe = conn.execute(
        "SELECT 1 FROM paths WHERE repo_id = ? AND path >= ? AND path < ? LIMIT 1",
        (repo_id, path + "/", path + "0"),
    ).fetchone()
    if probe is not None:
        return Scope(path, "dir", path.rsplit("/", 1)[-1])
    raise MetricsError(f"Path '{path}' was not found in this repository.", 404)


def ensure_ancestry(conn, repo, ref: str | None) -> str | None:
    """Resolve + cache the non-merge ancestor set of h_r (spec §2).

    Returns the resolved reference sha, or None when no restriction is needed
    (no ref given, ``HEAD``, or a ref equal to the ingested HEAD).
    """
    if not ref or ref.strip().upper() == "HEAD":
        return None
    sha = gitio.resolve_commit(repo["root_path"], ref.strip())
    if sha is None:
        raise MetricsError(f"Reference '{ref}' could not be resolved to a commit.")
    if sha == repo["head_sha"]:
        return None
    cached = conn.execute(
        "SELECT 1 FROM ancestry WHERE repo_id = ? AND ref_sha = ? LIMIT 1",
        (repo["id"], sha),
    ).fetchone()
    if cached is None:
        shas = gitio.rev_list_no_merges(repo["root_path"], sha)
        with txn(conn):
            conn.executemany(
                "INSERT OR IGNORE INTO ancestry (repo_id, ref_sha, sha) VALUES (?,?,?)",
                [(repo["id"], sha, s) for s in shas],
            )
    return sha


def commit_conditions(
    conn, repo, cs: CommitSet, alias: str = "m", include_authors: bool = True
) -> tuple[list[str], dict]:
    """SQL conditions for the commit set (without scope conditions)."""
    conds = [f"{alias}.repo_id = :rid"]
    params: dict = {"rid": repo["id"]}

    ref_sha = ensure_ancestry(conn, repo, cs.ref)
    if ref_sha:
        conds.append(
            f"{alias}.sha IN (SELECT sha FROM ancestry WHERE repo_id = :rid AND ref_sha = :ref_sha)"
        )
        params["ref_sha"] = ref_sha

    if cs.shas:
        if len(cs.shas) > settings.max_commit_list:
            raise MetricsError(
                f"Commit list too large ({len(cs.shas)}); the limit is {settings.max_commit_list}."
            )
        names = [f"cs{i}" for i in range(len(cs.shas))]
        conds.append(f"{alias}.sha IN ({', '.join(':' + n for n in names)})")
        params.update({n: s for n, s in zip(names, cs.shas)})
    else:
        if cs.since is not None:
            conds.append(f"{alias}.ct >= :since")
            params["since"] = cs.since
        if cs.until is not None:
            conds.append(f"{alias}.ct < :until")
            params["until"] = cs.until

    if include_authors and cs.author_ids:
        names = [f"au{i}" for i in range(len(cs.author_ids))]
        conds.append(f"{alias}.author_id IN ({', '.join(':' + n for n in names)})")
        params.update({n: a for n, a in zip(names, cs.author_ids)})
    return conds, params


def scope_conditions(scope: Scope, alias: str = "c") -> tuple[list[str], dict]:
    """SQL conditions selecting the scope object's rows in ``changes``."""
    if scope.kind == "file":
        return [f"{alias}.path = :scope_path"], {"scope_path": scope.path}
    if scope.kind == "dir" and scope.path:
        # Lexicographic range covering every descendant path. '/' (0x2f) is
        # immediately below '0' (0x30), so [d+'/', d+'0') is exactly the
        # subtree of d. This uses the (repo_id, path, ...) index directly.
        return (
            [f"{alias}.path >= :scope_lo", f"{alias}.path < :scope_hi"],
            {"scope_lo": scope.path + "/", "scope_hi": scope.path + "0"},
        )
    return [], {}


def _join_changes() -> str:
    return "JOIN commits m ON m.repo_id = c.repo_id AND m.sha = c.sha"


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Metric bundles
# ---------------------------------------------------------------------------


def metrics_bundle(conn, repo, scope: Scope, cs: CommitSet) -> dict:
    """All scalar metrics for one object over one commit set (spec §2.1-2.4)."""
    cc, cp = commit_conditions(conn, repo, cs)
    sc, sp = scope_conditions(scope)
    params = {**cp, **sp}
    where = " AND ".join(cc + sc)

    n_commits = conn.execute(
        f"SELECT COUNT(*) FROM commits m WHERE {' AND '.join(cc)}", cp
    ).fetchone()[0]

    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(c.added), 0)   AS added,
               COALESCE(SUM(c.removed), 0) AS removed,
               COUNT(DISTINCT c.path)      AS n_files
        FROM changes c {_join_changes()}
        WHERE {where}
        """,
        params,
    ).fetchone()

    modifications = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM changes c {_join_changes()}
            WHERE {where} AND (c.added + c.removed) > 0
            GROUP BY m.sha
        )
        """,
        params,
    ).fetchone()[0]

    added = row["added"]
    removed = row["removed"]
    churn = added + removed
    return {
        "scope": {"path": scope.path, "kind": scope.kind, "name": scope.name},
        "n_commits": n_commits,
        "added": added,
        "removed": removed,
        "growth": added - removed,
        "churn": churn,
        "n_files": row["n_files"],
        "modifications": modifications,
        "modification_frequency": (modifications / n_commits) if n_commits else 0.0,
        "churn_rate": (churn / n_commits) if n_commits else 0.0,
    }


def contributors(conn, repo, scope: Scope, cs: CommitSet) -> list[dict]:
    """Per-author metrics over the set (spec §2.5), sorted by churn.

    The author filter of ``cs`` is intentionally ignored here so the panel can
    show every contributor's share (ownership) of the filtered object.
    """
    cc, cp = commit_conditions(conn, repo, cs, include_authors=False)
    sc, sp = scope_conditions(scope)
    on_scope = (" AND " + " AND ".join(sc)) if sc else ""

    n_commits = conn.execute(
        f"SELECT COUNT(*) FROM commits m WHERE {' AND '.join(cc)}", cp
    ).fetchone()[0]

    rows = conn.execute(
        f"""
        SELECT m.author_id AS author_id,
               COALESCE(SUM(c.added), 0)   AS added,
               COALESCE(SUM(c.removed), 0) AS removed,
               COUNT(DISTINCT m.sha)       AS n_commits,
               COUNT(DISTINCT CASE WHEN (c.added + c.removed) > 0 THEN m.sha END) AS modifications
        FROM commits m
        LEFT JOIN changes c
               ON c.repo_id = m.repo_id AND c.sha = m.sha{on_scope}
        WHERE {' AND '.join(cc)}
        GROUP BY m.author_id
        ORDER BY (COALESCE(SUM(c.added), 0) + COALESCE(SUM(c.removed), 0)) DESC
        """,
        {**cp, **sp},
    ).fetchall()

    authors = _author_map(conn, repo["id"])
    total_churn = sum(r["added"] + r["removed"] for r in rows)
    out = []
    for r in rows:
        a = authors.get(r["author_id"], {"name": "?", "email": ""})
        churn = r["added"] + r["removed"]
        out.append(
            {
                "author_id": r["author_id"],
                "name": a["name"],
                "email": a["email"],
                "added": r["added"],
                "removed": r["removed"],
                "growth": r["added"] - r["removed"],
                "churn": churn,
                "n_commits": r["n_commits"],
                "modifications": r["modifications"],
                "modification_frequency": (r["modifications"] / n_commits) if n_commits else 0.0,
                "churn_rate": (churn / n_commits) if n_commits else 0.0,
                "ownership": (churn / total_churn) if total_churn else 0.0,
            }
        )
    return out


def _author_map(conn, repo_id: int) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT id, name, email FROM authors WHERE repo_id = ?", (repo_id,)
    ).fetchall()
    return {r["id"]: {"name": r["name"], "email": r["email"]} for r in rows}


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------


def _bucket_start(d: date, bucket: str) -> date:
    if bucket == "week":
        return d - timedelta(days=d.weekday())
    if bucket == "month":
        return d.replace(day=1)
    return d


def _next_bucket(d: date, bucket: str) -> date:
    if bucket == "week":
        return d + timedelta(days=7)
    if bucket == "month":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d + timedelta(days=1)


def _bucket_ts(d: date) -> int:
    return calendar.timegm(d.timetuple())


def _bucket_label(d: date, bucket: str) -> str:
    if bucket == "month":
        return d.strftime("%Y-%m")
    return d.isoformat()


def series(conn, repo, scope: Scope, cs: CommitSet, bucket: str = "auto") -> dict:
    """Bucketed change/commit activity over the set (for charts)."""
    cc, cp = commit_conditions(conn, repo, cs)
    sc, sp = scope_conditions(scope)
    where = " AND ".join(cc + sc)
    params = {**cp, **sp}

    n_commits = conn.execute(
        f"SELECT COUNT(*) FROM commits m WHERE {' AND '.join(cc)}", cp
    ).fetchone()[0]

    if bucket == "commit":
        if n_commits <= 3000:
            return _series_by_commit(conn, cc, cp, sc, sp, scope)
        bucket = "auto"

    if bucket == "auto":
        span = conn.execute(
            f"SELECT MIN(m.ct) AS a, MAX(m.ct) AS b FROM commits m WHERE {' AND '.join(cc)}",
            cp,
        ).fetchone()
        if span["a"] is None:
            return {"bucket": "day", "points": []}
        span_days = (span["b"] - span["a"]) / 86400
        bucket = "day" if span_days <= 100 else "week" if span_days <= 800 else "month"

    change_rows = conn.execute(
        f"""
        SELECT m.ct / 86400 AS day, SUM(c.added) AS added, SUM(c.removed) AS removed
        FROM changes c {_join_changes()}
        WHERE {where}
        GROUP BY day
        """,
        params,
    ).fetchall()

    mod_rows = conn.execute(
        f"""
        SELECT day, COUNT(*) AS n FROM (
            SELECT m.ct / 86400 AS day, m.sha
            FROM changes c {_join_changes()}
            WHERE {where} AND (c.added + c.removed) > 0
            GROUP BY day, m.sha
        ) GROUP BY day
        """,
        params,
    ).fetchall()

    commit_rows = conn.execute(
        f"""
        SELECT m.ct / 86400 AS day, COUNT(*) AS n
        FROM commits m WHERE {' AND '.join(cc)}
        GROUP BY day
        """,
        cp,
    ).fetchall()

    per_day: dict[int, dict] = {}
    for r in change_rows:
        per_day.setdefault(r["day"], {})["added"] = r["added"]
        per_day[r["day"]]["removed"] = r["removed"]
    for r in mod_rows:
        per_day.setdefault(r["day"], {})["modifications"] = r["n"]
    for r in commit_rows:
        per_day.setdefault(r["day"], {})["commits"] = r["n"]

    if not per_day:
        return {"bucket": bucket, "points": []}

    days = sorted(per_day)
    buckets: dict[int, dict] = {}
    for day in days:
        d = date.fromtimestamp(day * 86400)
        start = _bucket_start(d, bucket)
        key = _bucket_ts(start)
        agg = buckets.setdefault(
            key,
            {"t": key, "label": _bucket_label(start, bucket), "added": 0, "removed": 0,
             "modifications": 0, "commits": 0},
        )
        agg["added"] += per_day[day].get("added", 0)
        agg["removed"] += per_day[day].get("removed", 0)
        agg["modifications"] += per_day[day].get("modifications", 0)
        agg["commits"] += per_day[day].get("commits", 0)

    # Fill gaps so charts render a continuous axis.
    first = _bucket_start(date.fromtimestamp(days[0] * 86400), bucket)
    last = _bucket_start(date.fromtimestamp(days[-1] * 86400), bucket)
    points = []
    cur = first
    while cur <= last:
        key = _bucket_ts(cur)
        agg = buckets.get(key)
        if agg is None:
            agg = {"t": key, "label": _bucket_label(cur, bucket), "added": 0, "removed": 0,
                   "modifications": 0, "commits": 0}
        agg["growth"] = agg["added"] - agg["removed"]
        agg["churn"] = agg["added"] + agg["removed"]
        points.append(agg)
        cur = _next_bucket(cur, bucket)

    return {"bucket": bucket, "points": points}


def _series_by_commit(conn, cc, cp, sc, sp, scope: Scope) -> dict:
    """Per-commit series (small sets only), ordered chronologically."""
    on_scope = (" AND " + " AND ".join(sc)) if sc else ""
    rows = conn.execute(
        f"""
        SELECT m.sha, m.ct,
               COALESCE(SUM(c.added), 0)   AS added,
               COALESCE(SUM(c.removed), 0) AS removed
        FROM commits m
        LEFT JOIN changes c ON c.repo_id = m.repo_id AND c.sha = m.sha{on_scope}
        WHERE {' AND '.join(cc)}
        GROUP BY m.sha
        ORDER BY m.ct ASC, m.seq ASC
        """,
        {**cp, **sp},
    ).fetchall()
    points = []
    for r in rows:
        added, removed = r["added"], r["removed"]
        points.append(
            {
                "t": r["ct"],
                "label": r["sha"][:8],
                "sha": r["sha"],
                "added": added,
                "removed": removed,
                "growth": added - removed,
                "churn": added + removed,
                "commits": 1,
                "modifications": 1 if (added + removed) > 0 else 0,
            }
        )
    return {"bucket": "commit", "points": points}


# ---------------------------------------------------------------------------
# File / directory listings
# ---------------------------------------------------------------------------

_SORT_COLUMNS = {
    "churn": "churn",
    "added": "added",
    "removed": "removed",
    "mods": "mods",
    "commits": "commits",
    "path": "path",
}


def file_rows(
    conn,
    repo,
    scope: Scope,
    cs: CommitSet,
    sort: str = "churn",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Per-file aggregates under the scope, for leaderboards and tables."""
    sort_key = _SORT_COLUMNS.get(sort)
    if sort_key is None:
        raise MetricsError(f"Unknown sort key '{sort}'.")
    cc, cp = commit_conditions(conn, repo, cs)
    sc, sp = scope_conditions(scope)
    params = {**cp, **sp}
    where = " AND ".join(cc + sc)

    n_commits = conn.execute(
        f"SELECT COUNT(*) FROM commits m WHERE {' AND '.join(cc)}", cp
    ).fetchone()[0]

    base = f"""
        FROM changes c {_join_changes()}
        WHERE {where}
        GROUP BY c.path
    """
    total = conn.execute(f"SELECT COUNT(*) FROM (SELECT c.path {base})", params).fetchone()[0]

    direction = "ASC" if sort_key == "path" else "DESC"
    select = f"""
        SELECT c.path AS path,
               SUM(c.added)   AS added,
               SUM(c.removed) AS removed,
               (SUM(c.added) + SUM(c.removed)) AS churn,
               COUNT(DISTINCT m.sha) AS commits,
               COUNT(DISTINCT CASE WHEN (c.added + c.removed) > 0 THEN m.sha END) AS mods,
               MAX(c.is_binary) AS is_binary,
               MAX(CASE WHEN c.old_path IS NOT NULL THEN 1 ELSE 0 END) AS renamed
        {base}
        ORDER BY {sort_key} {direction}
        LIMIT :lim OFFSET :off
    """
    rows = conn.execute(select, {**params, "lim": limit, "off": offset}).fetchall()

    page_paths = [r["path"] for r in rows]
    owners, authors = _owners_for_paths(conn, repo, where, params, page_paths)

    out = []
    for r in rows:
        churn = r["churn"]
        out.append(
            {
                "path": r["path"],
                "name": r["path"].rsplit("/", 1)[-1],
                "added": r["added"],
                "removed": r["removed"],
                "growth": r["added"] - r["removed"],
                "churn": churn,
                "commits": r["commits"],
                "modifications": r["mods"],
                "modification_frequency": (r["mods"] / n_commits) if n_commits else 0.0,
                "churn_rate": (churn / n_commits) if n_commits else 0.0,
                "is_binary": bool(r["is_binary"]),
                "renamed": bool(r["renamed"]),
                "owners": [
                    {
                        "author_id": o["author_id"],
                        "name": authors.get(o["author_id"], {}).get("name", "?"),
                        "churn": o["churn"],
                        "share": (o["churn"] / churn) if churn else 0.0,
                    }
                    for o in owners.get(r["path"], [])
                ],
            }
        )
    return {"total": total, "n_commits": n_commits, "rows": out}


def _owners_for_paths(conn, repo, where: str, params: dict, paths: list[str]) -> tuple[dict, dict]:
    """Top-3 churn owners per file, for the ownership mini-bars."""
    if not paths:
        return {}, {}
    names = [f"p{i}" for i in range(len(paths))]
    in_clause = ", ".join(":" + n for n in names)
    rows = conn.execute(
        f"""
        SELECT path, author_id, churn FROM (
            SELECT c.path AS path, m.author_id AS author_id,
                   SUM(c.added + c.removed) AS churn,
                   ROW_NUMBER() OVER (
                       PARTITION BY c.path ORDER BY SUM(c.added + c.removed) DESC, m.author_id
                   ) AS rn
            FROM changes c {_join_changes()}
            WHERE {where} AND c.path IN ({in_clause})
            GROUP BY c.path, m.author_id
        ) WHERE rn <= 3
        """,
        {**params, **{n: p for n, p in zip(names, paths)}},
    ).fetchall()
    owners: dict[str, list[dict]] = {}
    for r in rows:
        owners.setdefault(r["path"], []).append({"author_id": r["author_id"], "churn": r["churn"]})
    return owners, _author_map(conn, repo["id"])


def tree(conn, repo, scope: Scope, cs: CommitSet) -> dict:
    """Immediate children of a directory with rolled-up metrics per child.

    One GROUP BY query computes the per-child sums (the child key is derived
    from the path with SQL string functions), and the ``paths`` table supplies
    child kind / file counts -- no per-child queries.
    """
    if scope.kind == "file":
        return {"path": scope.path, "kind": "file", "children": [], "n_commits": 0}

    cc, cp = commit_conditions(conn, repo, cs)
    sc, sp = scope_conditions(scope)
    where = " AND ".join(cc + sc)
    params = {**cp, **sp}

    n_commits = conn.execute(
        f"SELECT COUNT(*) FROM commits m WHERE {' AND '.join(cc)}", cp
    ).fetchone()[0]

    if scope.path:
        rest_expr = "substr(c.path, :plen)"
        params["plen"] = len(scope.path) + 2  # 1-based substr, skip 'dir/'
    else:
        rest_expr = "c.path"
    child_expr = (
        f"CASE WHEN instr({rest_expr}, '/') > 0 "
        f"THEN substr({rest_expr}, 1, instr({rest_expr}, '/') - 1) "
        f"ELSE {rest_expr} END"
    )

    agg_rows = conn.execute(
        f"""
        SELECT {child_expr} AS child,
               SUM(c.added)   AS added,
               SUM(c.removed) AS removed,
               COUNT(DISTINCT m.sha) AS commits,
               COUNT(DISTINCT CASE WHEN (c.added + c.removed) > 0 THEN m.sha END) AS mods
        FROM changes c {_join_changes()}
        WHERE {where}
        GROUP BY child
        """,
        params,
    ).fetchall()

    children: dict[str, dict] = {}
    for r in agg_rows:
        children[r["child"]] = {
            "name": r["child"],
            "kind": "dir",
            "added": r["added"],
            "removed": r["removed"],
            "commits": r["commits"],
            "modifications": r["mods"],
            "n_files": 0,
            "n_files_head": 0,
            "binary": False,
            "at_head": False,
        }

    # Metadata from the paths universe.
    if scope.path:
        meta_rows = conn.execute(
            "SELECT path, kind, binary, at_head FROM paths WHERE repo_id = :repo_id "
            "AND path >= :lo AND path < :hi",
            {"repo_id": repo["id"], "lo": scope.path + "/", "hi": scope.path + "0"},
        ).fetchall()
    else:
        meta_rows = conn.execute(
            "SELECT path, kind, binary, at_head FROM paths WHERE repo_id = ?",
            (repo["id"],),
        ).fetchall()

    prefix_len = len(scope.path) + 1 if scope.path else 0
    for m in meta_rows:
        rel = m["path"][prefix_len:]
        seg, sep, _ = rel.partition("/")
        if not seg:
            continue
        child = children.setdefault(
            seg,
            {"name": seg, "kind": "file", "added": 0, "removed": 0, "commits": 0,
             "modifications": 0, "n_files": 0, "n_files_head": 0, "binary": False, "at_head": False},
        )
        if sep:  # a descendant -> child is a directory
            child["kind"] = "dir"
        if m["kind"] == "file":
            child["n_files"] += 1
            if m["at_head"]:
                child["n_files_head"] += 1
            if m["binary"]:
                child["binary"] = True
        if not sep:
            # The child's own row: it carries the authoritative kind.
            child["kind"] = m["kind"]
            child["at_head"] = bool(m["at_head"])

    out = []
    for child in children.values():
        added, removed = child["added"], child["removed"]
        churn = added + removed
        path = f"{scope.path}/{child['name']}" if scope.path else child["name"]
        out.append(
            {
                "path": path,
                "name": child["name"],
                "kind": child["kind"],
                "binary": child["binary"],
                "at_head": child["at_head"],
                "n_files": child["n_files"],
                "n_files_head": child["n_files_head"],
                "added": added,
                "removed": removed,
                "growth": added - removed,
                "churn": churn,
                "commits": child["commits"],
                "modifications": child["modifications"],
                "modification_frequency": (child["modifications"] / n_commits) if n_commits else 0.0,
                "churn_rate": (churn / n_commits) if n_commits else 0.0,
            }
        )
    out.sort(key=lambda c: (c["kind"] != "dir", c["name"].lower()))
    return {"path": scope.path, "kind": scope.kind, "n_commits": n_commits, "children": out}


# ---------------------------------------------------------------------------
# Commit explorer
# ---------------------------------------------------------------------------


def commits_page(
    conn,
    repo,
    cs: CommitSet,
    *,
    scope: Scope | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    newest_first: bool = True,
) -> dict:
    """Paginated commit list with per-commit (optionally scoped) line counts."""
    cc, cp = commit_conditions(conn, repo, cs)
    conds = list(cc)
    params = dict(cp)
    if q:
        conds.append("(m.subject LIKE :q ESCAPE '\\' OR m.sha LIKE :qsha)")
        params["q"] = f"%{_like_escape(q)}%"
        params["qsha"] = f"{_like_escape(q)}%"

    total = conn.execute(
        f"SELECT COUNT(*) FROM commits m WHERE {' AND '.join(conds)}", params
    ).fetchone()[0]

    order = "DESC" if newest_first else "ASC"
    inner = f"""
        SELECT m.sha, m.seq, m.ct, m.parent_sha, m.subject, m.author_id,
               m.added, m.removed, m.n_files
        FROM commits m
        WHERE {' AND '.join(conds)}
        ORDER BY m.ct {order}, m.seq {order}
        LIMIT :lim OFFSET :off
    """

    if scope is not None and scope.kind != "repo":
        sc, sp = scope_conditions(scope)
        rows = conn.execute(
            f"""
            SELECT r.*,
                   COALESCE(SUM(c.added), 0)   AS s_added,
                   COALESCE(SUM(c.removed), 0) AS s_removed,
                   COUNT(c.sha)                AS s_files
            FROM ({inner}) r
            LEFT JOIN changes c
                   ON c.repo_id = :rid AND c.sha = r.sha AND {' AND '.join(sc)}
            GROUP BY r.sha
            ORDER BY r.ct {order}, r.seq {order}
            """,
            {**params, **sp, "lim": limit, "off": offset},
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT sha, seq, ct, parent_sha, subject, author_id,
                   added, removed, n_files,
                   added AS s_added, removed AS s_removed, n_files AS s_files
            FROM ({inner})
            """,
            {**params, "lim": limit, "off": offset},
        ).fetchall()

    authors = _author_map(conn, repo["id"])
    return {
        "total": total,
        "rows": [
            {
                "sha": r["sha"],
                "ct": r["ct"],
                "subject": r["subject"],
                "parent_sha": r["parent_sha"],
                "author_id": r["author_id"],
                "author_name": authors.get(r["author_id"], {}).get("name", "?"),
                "added": r["s_added"],
                "removed": r["s_removed"],
                "churn": r["s_added"] + r["s_removed"],
                "n_files": r["s_files"],
                "total_added": r["added"],
                "total_removed": r["removed"],
                "total_n_files": r["n_files"],
            }
            for r in rows
        ],
    }


def commit_detail(conn, repo, sha: str) -> dict:
    """Everything about one commit: header, author, changed files."""
    row = conn.execute(
        "SELECT * FROM commits WHERE repo_id = ? AND sha = ?", (repo["id"], sha)
    ).fetchone()
    if row is None:
        raise MetricsError(f"Commit '{sha}' is not part of the indexed history.", 404)

    author = conn.execute(
        "SELECT id, name, email FROM authors WHERE id = ?", (row["author_id"],)
    ).fetchone()
    identity = conn.execute(
        "SELECT id, name, email FROM authors WHERE id = ?", (row["identity_id"],)
    ).fetchone()

    change_rows = conn.execute(
        "SELECT path, old_path, added, removed, is_binary FROM changes "
        "WHERE repo_id = ? AND sha = ? ORDER BY seq",
        (repo["id"], sha),
    ).fetchall()

    return {
        "sha": row["sha"],
        "ct": row["ct"],
        "subject": row["subject"],
        "parent_sha": row["parent_sha"],
        "author_id": row["author_id"],
        "author_name": author["name"] if author else "?",
        "author_email": author["email"] if author else "",
        "identity_name": identity["name"] if identity else None,
        "identity_email": identity["email"] if identity else None,
        "added": row["added"],
        "removed": row["removed"],
        "growth": row["added"] - row["removed"],
        "churn": row["added"] + row["removed"],
        "n_files": row["n_files"],
        "files": [
            {
                "path": c["path"],
                "old_path": c["old_path"],
                "added": c["added"],
                "removed": c["removed"],
                "is_binary": bool(c["is_binary"]),
                "renamed": c["old_path"] is not None,
            }
            for c in change_rows
        ],
    }


def search_paths(conn, repo_id: int, q: str, limit: int = 50) -> list[dict]:
    """Path autocomplete for the picker (matches basename or path substring)."""
    q = (q or "").strip()
    if q:
        rows = conn.execute(
            "SELECT path, kind FROM paths WHERE repo_id = :repo_id AND path LIKE :q ESCAPE '\\' "
            "ORDER BY length(path), path LIMIT :lim",
            {"repo_id": repo_id, "q": f"%{_like_escape(q)}%", "lim": limit},
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT path, kind FROM paths WHERE repo_id = :repo_id ORDER BY length(path), path LIMIT :lim",
            {"repo_id": repo_id, "lim": limit},
        ).fetchall()
    return [{"path": r["path"], "kind": r["kind"]} for r in rows]
