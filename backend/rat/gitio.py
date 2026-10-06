"""Thin wrapper around the git CLI plus the streaming ``git log`` parser.

The ingestion hot path uses a **single** ``git log`` invocation whose output is
parsed incrementally: O(batch) memory and one process spawn per repository,
regardless of history size.

Output format contract (verified empirically against git 2.43)::

    \\x1e<sha>\\0<parents>\\0<aN>\\0<aE>\\0<an>\\0<ae>\\0<ct>\\0<subject>\\0\\0
    <added>\\t<removed>\\t<path>\\0            # normal change
    <added>\\t<removed>\\t\\0<old>\\0<new>\\0  # rename: path field is empty
    -\\t-\\t<path>\\0                          # binary file (not measured)

``-z`` disables path quoting entirely (even tabs/quotes/newlines survive raw),
so NUL-separated tokens are unambiguous.  ``%aN/%aE`` are mailmapped by git
(``--use-mailmap``); ``%an/%ae`` stay raw so the UI can offer manual merging.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

RS = b"\x1e"
LOG_FORMAT = "%x1e%H%x00%P%x00%aN%x00%aE%x00%an%x00%ae%x00%ct%x00%s%x00"

_COUNT_RE = re.compile(rb"^(?:\r?\n)*(-|\d+)\t(-|\d+)\t")
_SHA_RE = re.compile(rb"^[0-9a-f]{40,64}$")
_PROGRESS_RE = re.compile(rb"(Counting|Compressing|Receiving|Resolving)[^:]*:\s+(\d+)%")

# 50% rename similarity threshold is mandated by the spec.
RENAME_THRESHOLD = "50%"


class GitError(RuntimeError):
    """Raised when a git subprocess fails."""


class GitLogFormatError(GitError):
    """Raised when the ``git log`` stream cannot be parsed (desync guard)."""


@dataclass(slots=True)
class Change:
    """One (commit, file) change record: the unit of file metrics."""

    path: str  # post-rename path (changes are attributed to the new path)
    old_path: str | None  # set when git detected a rename
    added: int
    removed: int
    is_binary: bool


@dataclass(slots=True)
class Commit:
    sha: str
    parents: list[str]
    author_name: str  # mailmapped
    author_email: str  # mailmapped
    raw_name: str
    raw_email: str
    ct: int  # committer date, unix seconds
    subject: str
    changes: list[Change]


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["GIT_PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for credentials
    if Path("/bin/true").exists():
        env["GIT_ASKPASS"] = "/bin/true"
    return env


def _decode_stderr(err: bytes, limit: int = 2000) -> str:
    return _decode(err[-limit:]).strip()


# ---------------------------------------------------------------------------
# Streaming log parser
# ---------------------------------------------------------------------------


def parse_log_record(rec: bytes) -> Commit:
    """Parse one ``\\x1e``-delimited record into a :class:`Commit`."""
    toks = rec.lstrip(b"\r\n").split(b"\x00")
    if len(toks) < 8:
        raise GitLogFormatError(f"short record ({len(toks)} fields): {rec[:80]!r}")
    if not _SHA_RE.match(toks[0]):
        raise GitLogFormatError(f"record does not start with a sha: {rec[:80]!r}")

    ct_raw = toks[6].strip()
    try:
        ct = int(ct_raw)
    except ValueError as exc:
        raise GitLogFormatError(f"bad committer date {ct_raw!r} for {_decode(toks[0])}") from exc

    changes: list[Change] = []
    i = 8  # fields 0..7 are the fixed header (H,P,aN,aE,an,ae,ct,s)
    while i < len(toks):
        m = _COUNT_RE.match(toks[i])
        if not m:
            i += 1
            continue
        a_raw, r_raw = m.group(1), m.group(2)
        is_binary = a_raw == b"-" or r_raw == b"-"
        added = 0 if is_binary else int(a_raw)
        removed = 0 if is_binary else int(r_raw)
        rest = toks[i][m.end() :]
        if rest == b"":  # rename: old and new paths follow as separate fields
            if i + 2 >= len(toks):
                raise GitLogFormatError(f"rename entry missing paths for {_decode(toks[0])}")
            old_path, new_path = _decode(toks[i + 1]), _decode(toks[i + 2])
            changes.append(Change(new_path, old_path, added, removed, is_binary))
            i += 3
        else:
            changes.append(Change(_decode(rest), None, added, removed, is_binary))
            i += 1

    return Commit(
        sha=_decode(toks[0]),
        parents=_decode(toks[1]).split(),
        author_name=_decode(toks[2]),
        author_email=_decode(toks[3]),
        raw_name=_decode(toks[4]),
        raw_email=_decode(toks[5]),
        ct=ct,
        subject=_decode(toks[7]),
        changes=changes,
    )


def _log_cmd(repo_path: str | Path) -> list[str]:
    return [
        "git",
        "-C",
        str(repo_path),
        "-c",
        "core.quotePath=false",
        "-c",
        "diff.renameLimit=65535",
        "-c",
        "diff.algorithm=myers",  # deterministic line counts
        "-c",
        "log.showSignature=false",
        "log",
        "--no-merges",  # H-bar: non-merge commits only (spec §2)
        "--root",  # initial commit diffs against the empty tree
        "--use-mailmap",
        f"--find-renames={RENAME_THRESHOLD}",
        "--numstat",
        "-z",
        f"--format={LOG_FORMAT}",
    ]


def stream_log(repo_path: str | Path) -> Iterator[Commit]:
    """Yield commits from ``git log`` in a memory-bounded streaming fashion."""
    proc = subprocess.Popen(
        _log_cmd(repo_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=git_env(),
    )
    assert proc.stdout is not None and proc.stderr is not None

    tail: deque[bytes] = deque(maxlen=200)
    drain = threading.Thread(target=lambda: [tail.append(line) for line in proc.stderr], daemon=True)
    drain.start()

    killed = False
    buf = b""
    try:
        while True:
            chunk = proc.stdout.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            records = buf.split(RS)
            buf = records.pop()
            for rec in records:
                if not rec.strip(b"\0\r\n \t"):
                    continue
                yield parse_log_record(rec)
        if buf.strip(b"\0\r\n \t"):
            yield parse_log_record(buf)
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        if proc.poll() is None:
            killed = True
            proc.kill()
        rc = proc.wait()
        drain.join(timeout=2)
        if rc != 0 and not killed:
            raise GitError(f"git log failed (exit {rc}):\n{_decode_stderr(b''.join(tail))}")


# ---------------------------------------------------------------------------
# One-shot git helpers
# ---------------------------------------------------------------------------


def run_git(
    repo_path: str | Path,
    *args: str,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            env=git_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out") from exc
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}):\n{_decode_stderr(proc.stderr)}"
        )
    return proc


def is_valid_repo(repo_path: str | Path) -> bool:
    """True when ``git -C path`` can open a repository rooted there."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--git-dir"],
            capture_output=True,
            env=git_env(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def head_info(repo_path: str | Path) -> tuple[str | None, str | None]:
    """Return ``(head_sha, branch)``; ``head_sha`` is None for an empty repo."""
    sha_proc = run_git(repo_path, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    sha = sha_proc.stdout.decode("ascii", "replace").strip() or None
    branch_proc = run_git(repo_path, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    branch = branch_proc.stdout.decode("utf-8", "replace").strip() or None
    return sha, branch


def resolve_commit(repo_path: str | Path, ref: str) -> str | None:
    """Resolve a user-supplied ref (sha/branch/tag) to a commit sha."""
    if not ref or ref.startswith("-"):
        return None
    proc = run_git(repo_path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if proc.returncode != 0:
        return None
    sha = proc.stdout.decode("ascii", "replace").strip()
    return sha or None


def count_commits(repo_path: str | Path) -> int:
    """Number of non-merge commits reachable from HEAD."""
    proc = run_git(repo_path, "rev-list", "--count", "--no-merges", "HEAD")
    return int(proc.stdout.decode("ascii", "replace").strip() or "0")


def rev_list_no_merges(repo_path: str | Path, ref: str) -> list[str]:
    """All non-merge commits reachable from ``ref`` (for analysis at h_r)."""
    proc = run_git(repo_path, "rev-list", "--no-merges", ref)
    return proc.stdout.decode("ascii", "replace").split()


def list_head_files(repo_path: str | Path) -> list[str]:
    """All blob paths in the HEAD tree (raw, NUL-separated)."""
    proc = run_git(repo_path, "ls-tree", "-r", "-z", "--name-only", "HEAD")
    return [_decode(p) for p in proc.stdout.split(b"\x00") if p]


# ---------------------------------------------------------------------------
# Zip / clone preparation helpers
# ---------------------------------------------------------------------------

_SCP_LIKE_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:.+$")
_ALLOWED_SCHEMES = ("http://", "https://", "git://", "ssh://")


def validate_clone_url(url: str) -> str:
    """Validate a remote clone URL; raises ValueError with a helpful message."""
    url = (url or "").strip()
    if not url:
        raise ValueError("Repository URL is required.")
    if url.startswith("-"):
        raise ValueError("Repository URL must not start with '-'.")
    if _SCP_LIKE_RE.match(url) or url.startswith(_ALLOWED_SCHEMES):
        return url
    raise ValueError(
        "Only remote URLs are supported (http://, https://, git://, ssh://, git@host:path). "
        "To analyse a local repository, upload a zip that includes its .git directory."
    )


def clone_bare(url: str, dest: Path, progress_cb: Callable[[float, str], None] | None = None) -> None:
    """Full ('deep') clone as a bare repository.

    Bare clones keep every branch and tag but skip the working tree, which is
    all the metric engine needs -- and it roughly halves clone size.
    """
    url = validate_clone_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)

    proc = subprocess.Popen(
        ["git", "clone", "--bare", "--progress", "--", url, str(dest)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=git_env(),
    )
    assert proc.stderr is not None
    tail: deque[bytes] = deque(maxlen=100)
    best = 0.0
    pending = b""
    try:
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            pending += chunk
            for line in re.split(rb"[\r\n]+", pending):
                if not line:
                    continue
                tail.append(line)
                if progress_cb:
                    m = _PROGRESS_RE.search(line)
                    if m:
                        pct = int(m.group(2)) / 100.0
                        if pct > best:
                            best = pct
                        progress_cb(min(best, 1.0), _decode(m.group(1)))
            pending = pending[-256:]
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if rc != 0:
        raise GitError(f"git clone failed (exit {rc}):\n{_decode_stderr(b' \n'.join(tail))}")


def find_git_root(root: Path, max_depth: int = 4) -> Path | None:
    """Locate a usable repository inside an extracted zip.

    Handles: repo at zip root, single wrapper folder, bare layout, and a
    ``.git`` file (worktree pointer) that resolves inside the extraction.
    """
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_depth = len(Path(dirpath).relative_to(root).parts)
        if rel_depth > max_depth:
            dirnames[:] = []
            continue
        # No repository lives inside a `.git` directory; prune for speed.
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if ".git" in dirnames or ".git" in filenames:
            candidates.append(Path(dirpath))
    candidates.append(root)  # bare-repository zip layout

    def depth(p: Path) -> int:
        return len(p.relative_to(root).parts)

    for cand in sorted(set(candidates), key=depth):
        if is_valid_repo(cand):
            return cand
    return None
