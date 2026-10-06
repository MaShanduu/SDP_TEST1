"""Programmatic git repository builder for tests.

Commits get deterministic committer dates (base + n*1000) so time-filter tests
can assert exact H_t / H_{i,j} boundaries.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

DEFAULT_AUTHOR = ("Builder", "builder@example.com")


class RepoBuilder:
    def __init__(self, path: Path, base_ct: int = 1_700_000_000):
        self.path = Path(path)
        self.base_ct = base_ct
        self.n = 0
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    # -- plumbing ---------------------------------------------------------

    def git(self, *args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
        full_env = dict(os.environ)
        full_env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": DEFAULT_AUTHOR[0],
                "GIT_AUTHOR_EMAIL": DEFAULT_AUTHOR[1],
                "GIT_COMMITTER_NAME": DEFAULT_AUTHOR[0],
                "GIT_COMMITTER_EMAIL": DEFAULT_AUTHOR[1],
            }
        )
        if env:
            full_env.update(env)
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, env=full_env
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace')}"
            )
        return proc

    # -- authoring --------------------------------------------------------

    def write(self, rel: str, content: str | bytes) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    def commit(
        self,
        message: str,
        author: tuple[str, str] = DEFAULT_AUTHOR,
        ct: int | None = None,
        add_all: bool = True,
    ) -> str:
        self.n += 1
        ct = self.base_ct + self.n * 1000 if ct is None else ct
        env = {
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": author[0],
            "GIT_COMMITTER_EMAIL": author[1],
            "GIT_AUTHOR_DATE": f"{ct} +0000",
            "GIT_COMMITTER_DATE": f"{ct} +0000",
        }
        if add_all:
            self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, env=env)
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def merge(self, branch: str, message: str = "merge", author: tuple[str, str] = DEFAULT_AUTHOR) -> str:
        self.n += 1
        ct = self.base_ct + self.n * 1000
        env = {
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": author[0],
            "GIT_COMMITTER_EMAIL": author[1],
            "GIT_COMMITTER_DATE": f"{ct} +0000",
            "GIT_AUTHOR_DATE": f"{ct} +0000",
        }
        self.git("merge", "--no-ff", "-m", message, branch, env=env)
        return self.git("rev-parse", "HEAD").stdout.decode().strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.decode().strip()


def make_zip(source_dir: Path, zip_path: Path) -> Path:
    """Zip a directory tree (including dotfiles like .git)."""
    import zipfile

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source_dir):
            for f in files:
                full = Path(root) / f
                zf.write(full, full.relative_to(source_dir).as_posix())
    return zip_path
