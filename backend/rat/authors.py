"""Author identity management.

Git distinguishes *identities* (post-mailmap ``(name, email)`` pairs) from
*effective authors*: the RAT lets users merge several identities into one
author, exactly like a mailmap does, but interactively.  Implementation:

* every identity is an ``authors`` row with ``alias_of IS NULL``;
* merging sets ``alias_of`` on the merged rows and re-points
  ``commits.author_id`` (``identity_id`` is kept, which makes merges and
  detaches a single indexed UPDATE);
* domain knowledge from ``.mailmap`` (if present) is applied by git itself via
  ``--use-mailmap`` at ingestion time, and the raw pre-mailmap identities are
  retained in ``raw_identities`` for display.
"""

from __future__ import annotations

from .db import txn


class AuthorError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _root_author(conn, repo_id: int, author_id: int) -> int:
    """Follow the alias chain to the effective author id."""
    seen = set()
    cur = author_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        row = conn.execute(
            "SELECT alias_of FROM authors WHERE id = ? AND repo_id = ?", (cur, repo_id)
        ).fetchone()
        if row is None:
            raise AuthorError(f"Author {author_id} not found.", 404)
        if row["alias_of"] is None:
            return cur
        cur = row["alias_of"]
    raise AuthorError("Corrupt alias chain.", 500)


def list_authors(conn, repo_id: int) -> list[dict]:
    """Effective authors with their merged identities and raw variants."""
    identity_counts = {
        r["identity_id"]: r["n"]
        for r in conn.execute(
            "SELECT identity_id, COUNT(*) AS n FROM commits WHERE repo_id = ? GROUP BY identity_id",
            (repo_id,),
        ).fetchall()
    }
    raw_by_identity: dict[int, list[dict]] = {}
    for r in conn.execute(
        "SELECT identity_id, raw_name, raw_email, n_commits FROM raw_identities WHERE repo_id = ? "
        "ORDER BY n_commits DESC",
        (repo_id,),
    ).fetchall():
        raw_by_identity.setdefault(r["identity_id"], []).append(
            {"name": r["raw_name"], "email": r["raw_email"], "n_commits": r["n_commits"]}
        )

    rows = conn.execute(
        "SELECT id, name, email, alias_of, is_manual FROM authors WHERE repo_id = ? ORDER BY id",
        (repo_id,),
    ).fetchall()

    out: list[dict] = []
    by_id: dict[int, dict] = {}
    for r in rows:
        if r["alias_of"] is None:
            entry = {
                "id": r["id"],
                "name": r["name"],
                "email": r["email"],
                "is_manual": bool(r["is_manual"]),
                "n_commits": identity_counts.get(r["id"], 0),
                "raw": raw_by_identity.get(r["id"], []),
                "aliases": [],
            }
            by_id[r["id"]] = entry
            out.append(entry)

    for r in rows:
        if r["alias_of"] is not None:
            parent = by_id.get(r["alias_of"])
            if parent is None:
                continue
            n = identity_counts.get(r["id"], 0)
            parent["aliases"].append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "email": r["email"],
                    "n_commits": n,
                    "raw": raw_by_identity.get(r["id"], []),
                }
            )
            parent["n_commits"] += n

    out.sort(key=lambda a: (-a["n_commits"], a["name"].lower()))
    return out


def merge_authors(conn, repo_id: int, target_id: int, source_ids: list[int]) -> dict:
    """Merge identities/authors into ``target_id`` (idempotent)."""
    target = _root_author(conn, repo_id, target_id)
    # Collect every row that will be folded in: the sources plus their aliases.
    move: set[int] = set()
    for sid in source_ids:
        if sid == target:
            continue
        root = _root_author(conn, repo_id, sid)
        if root == target:
            continue
        move.add(root)
        for r in conn.execute(
            "SELECT id FROM authors WHERE repo_id = ? AND alias_of = ?", (repo_id, root)
        ).fetchall():
            move.add(r["id"])
    if not move:
        return {"merged": 0, "target_id": target}

    ids = sorted(move)
    placeholders = ", ".join("?" for _ in ids)
    with txn(conn):
        conn.execute(
            f"UPDATE authors SET alias_of = ? WHERE repo_id = ? AND id IN ({placeholders})",
            (target, repo_id, *ids),
        )
        conn.execute(
            f"UPDATE commits SET author_id = ? WHERE repo_id = ? AND identity_id IN ({placeholders})",
            (target, repo_id, *ids),
        )
        _refresh_author_count(conn, repo_id)
    return {"merged": len(ids), "target_id": target}


def detach_identity(conn, repo_id: int, identity_id: int) -> dict:
    """Un-merge one identity back into a standalone author."""
    row = conn.execute(
        "SELECT alias_of FROM authors WHERE repo_id = ? AND id = ?", (repo_id, identity_id)
    ).fetchone()
    if row is None:
        raise AuthorError(f"Identity {identity_id} not found.", 404)
    if row["alias_of"] is None:
        raise AuthorError("This author is not merged into another author.")
    with txn(conn):
        conn.execute("UPDATE authors SET alias_of = NULL WHERE id = ?", (identity_id,))
        conn.execute(
            "UPDATE commits SET author_id = ? WHERE repo_id = ? AND identity_id = ?",
            (identity_id, repo_id, identity_id),
        )
        _refresh_author_count(conn, repo_id)
    return {"detached": identity_id}


def rename_author(conn, repo_id: int, author_id: int, name: str | None, email: str | None) -> dict:
    """Set a custom display name / email on an effective author."""
    row = conn.execute(
        "SELECT alias_of FROM authors WHERE repo_id = ? AND id = ?", (repo_id, author_id)
    ).fetchone()
    if row is None:
        raise AuthorError(f"Author {author_id} not found.", 404)
    if row["alias_of"] is not None:
        raise AuthorError("Only effective authors can be renamed.")
    fields = []
    params: list = []
    if name is not None:
        name = name.strip()
        if not name:
            raise AuthorError("Name must not be empty.")
        fields.append("name = ?")
        params.append(name)
    if email is not None:
        fields.append("email = ?")
        params.append(email.strip())
    if not fields:
        return {"updated": 0}
    params.extend([author_id, repo_id])
    with txn(conn):
        conn.execute(f"UPDATE authors SET {', '.join(fields)}, is_manual = 1 WHERE id = ? AND repo_id = ?", params)
    return {"updated": 1}


def _refresh_author_count(conn, repo_id: int) -> None:
    conn.execute(
        "UPDATE repos SET n_authors = "
        "(SELECT COUNT(*) FROM authors WHERE repo_id = ? AND alias_of IS NULL) WHERE id = ?",
        (repo_id, repo_id),
    )


def _normalise(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def suggestions(conn, repo_id: int) -> list[dict]:
    """Heuristic merge suggestions from the raw identity data.

    Two rules: identities sharing an email address, and identities whose names
    normalise to the same string (case/punctuation-insensitive).  Identities
    already merged together are never suggested.
    """
    rows = conn.execute(
        "SELECT id, name, email, alias_of FROM authors WHERE repo_id = ?", (repo_id,)
    ).fetchall()
    counts = {
        r["identity_id"]: r["n"]
        for r in conn.execute(
            "SELECT identity_id, COUNT(*) AS n FROM commits WHERE repo_id = ? GROUP BY identity_id",
            (repo_id,),
        ).fetchall()
    }

    def root(aid: int, alias: dict[int, int | None]) -> int:
        seen = set()
        while alias.get(aid) is not None and aid not in seen:
            seen.add(aid)
            aid = alias[aid]  # type: ignore[assignment]
        return aid

    alias = {r["id"]: r["alias_of"] for r in rows}
    groups: dict[tuple[str, str], list] = {}
    for r in rows:
        rid = root(r["id"], alias)
        if r["email"]:
            groups.setdefault(("email", r["email"].lower()), []).append((rid, r))
        norm = _normalise(r["name"])
        if norm:
            groups.setdefault(("name", norm), []).append((rid, r))

    out: list[dict] = []
    seen_keys: set[tuple] = set()
    for (kind, _key), members in groups.items():
        roots = {rid for rid, _ in members}
        if len(roots) < 2:
            continue
        key = tuple(sorted(roots))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            {
                "reason": "Same email address" if kind == "email" else "Similar name",
                "identities": [
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "email": r["email"],
                        "n_commits": counts.get(r["id"], 0),
                        "effective_author_id": root(r["id"], alias),
                    }
                    for _, r in members
                ],
            }
        )
    out.sort(key=lambda s: -sum(i["n_commits"] for i in s["identities"]))
    return out[:50]
