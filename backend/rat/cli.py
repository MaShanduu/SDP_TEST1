"""Command line interface -- useful for grading and quick verification.

Examples::

    python -m rat.cli serve --port 8000
    python -m rat.cli ingest https://github.com/DaveGamble/cJSON.git
    python -m rat.cli ingest ./exports/myrepo.zip --name myrepo
    python -m rat.cli metrics 1 --path src --since 2020-01-01
    python -m rat.cli metrics 1 --commits $(git rev-parse HEAD) --path CMakeLists.txt
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import gitio, ingest, metrics as m
from .api.deps import _parse_ts
from .config import settings
from .db import connect, init_db
from .models import repo_dict


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("rat.api:app", host=args.host, port=args.port, log_level="info")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    init_db()
    conn = connect()
    source = args.source.strip()

    if source.lower().endswith(".zip"):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            print(f"error: zip not found: {path}", file=sys.stderr)
            return 1
        kind, name, source_ref = "ingest_zip", args.name or path.stem, path.name
    else:
        try:
            gitio.validate_clone_url(source)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        kind, name, source_ref = "ingest_url", args.name or source.rstrip("/").rsplit("/", 1)[-1], source
        if name.endswith(".git"):
            name = name[:-4]

    cur = conn.execute(
        "INSERT INTO repos (name, source, source_ref, root_path, status) VALUES (?,?,?,'','pending')",
        (name, "zip" if kind == "ingest_zip" else "url", source_ref),
    )
    repo_id = cur.lastrowid
    repo_dir = settings.repo_dir(repo_id)
    repo_dir.mkdir(parents=True, exist_ok=True)
    if kind == "ingest_zip":
        shutil.copy2(source, repo_dir / "upload.zip")
        root = repo_dir / "extracted"
    else:
        root = repo_dir / "repo"
    conn.execute("UPDATE repos SET root_path=? WHERE id=?", (str(root), repo_id))
    cur = conn.execute("INSERT INTO jobs (repo_id, kind, status) VALUES (?,?,'queued')", (repo_id, kind))
    job_id = cur.lastrowid
    conn.commit()

    print(f"ingesting repo {repo_id} (job {job_id}) ...")
    ingest.run_job(job_id)

    row = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    print(json.dumps({"repo": repo_dict(row, job)}, indent=2))
    conn.close()
    return 0 if row["status"] == "ready" else 1


def _cmd_metrics(args: argparse.Namespace) -> int:
    conn = connect()
    repo = conn.execute("SELECT * FROM repos WHERE id=?", (args.repo_id,)).fetchone()
    if repo is None:
        print(f"error: repo {args.repo_id} not found", file=sys.stderr)
        return 1
    scope = m.resolve_scope(conn, args.repo_id, args.path)
    cs = m.CommitSet(
        ref=args.ref,
        since=_parse_ts(args.since, "since"),
        until=_parse_ts(args.until, "until"),
        shas=[s.strip() for s in args.commits.split(",")] if args.commits else None,
    )
    bundle = m.metrics_bundle(conn, repo, scope, cs)
    bundle["contributors"] = m.contributors(conn, repo, scope, cs)
    print(json.dumps(bundle, indent=2))
    conn.close()
    return 0


def _cmd_repos(_args: argparse.Namespace) -> int:
    conn = connect()
    for row in conn.execute("SELECT * FROM repos ORDER BY id").fetchall():
        job = conn.execute(
            "SELECT * FROM jobs WHERE repo_id=? ORDER BY id DESC LIMIT 1", (row["id"],)
        ).fetchone()
        d = repo_dict(row, job)
        print(
            f"#{d['id']:<3} {d['name']:<30} {d['status']:<8} "
            f"commits={d['n_commits']:<7} added={d['total_added']:<9} removed={d['total_removed']:<9} "
            f"authors={d['n_authors']}"
        )
        if d["job"] and d["job"]["status"] == "error":
            print(f"     job error: {d['job']['error']}")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rat", description="Repo Analysis Tool CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="Run the API server (and serve the built frontend)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("ingest", help="Ingest a repository from a URL or a zip file (synchronously)")
    p.add_argument("source", help="Remote URL or path to a .zip containing .git")
    p.add_argument("--name", default=None, help="Display name for the repository")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("metrics", help="Print scalar metrics for an object over a commit set")
    p.add_argument("repo_id", type=int)
    p.add_argument("--path", default="", help="File or directory path (default: repository root)")
    p.add_argument("--ref", default=None, help="Reference commit h_r (default HEAD)")
    p.add_argument("--since", default=None, help="Inclusive start (unix or ISO-8601)")
    p.add_argument("--until", default=None, help="Exclusive end (unix or ISO-8601)")
    p.add_argument("--commits", default=None, help="Comma-separated commit shas")
    p.set_defaults(func=_cmd_metrics)

    p = sub.add_parser("repos", help="List ingested repositories")
    p.set_defaults(func=_cmd_repos)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
