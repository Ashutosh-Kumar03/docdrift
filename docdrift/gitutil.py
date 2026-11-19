from __future__ import annotations

import subprocess
from typing import List

DOC_EXT = (".md", ".rst", ".txt", ".mdx")


def run(repo: str, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def diff(repo: str, base: str, head: str) -> str:
    return run(repo, "diff", "--no-color", "--no-ext-diff", "-U0", base, head)


def list_files(repo: str, rev: str) -> List[str]:
    return [l for l in run(repo, "ls-tree", "-r", "--name-only", rev).splitlines() if l]


def show(repo: str, rev: str, path: str) -> str:
    try:
        return run(repo, "show", f"{rev}:{path}")
    except RuntimeError:
        return ""


def is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_EXT)
