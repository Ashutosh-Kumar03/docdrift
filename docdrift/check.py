from __future__ import annotations

import os
import re
from typing import List, Tuple

from . import gitutil as git
from .changes import extract_changes
from .llm import judge
from .models import Change, Finding
from .prose import prose_findings
from .scanner import scan_docs

HISTORY_FILE = re.compile(r"(changelog|changes|history|news|release|license)", re.I)
MAX_DOC_BYTES = 500_000


def load_docs(repo: str, rev: str) -> dict:
    docs = {}
    for p in git.list_files(repo, rev):
        if git.is_doc(p) and not HISTORY_FILE.search(p.rsplit("/", 1)[-1]):
            txt = git.show(repo, rev, p)
            if txt and len(txt) <= MAX_DOC_BYTES:
                docs[p] = txt
    return docs


def load_docs_dir(path: str) -> dict:
    """Docs that live OUTSIDE the code repo (a separate docs repo, a checked-out website folder)."""
    docs = {}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__")]
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, path)
            if git.is_doc(name) and not HISTORY_FILE.search(name) and os.path.getsize(full) <= MAX_DOC_BYTES:
                with open(full, encoding="utf-8", errors="replace") as f:
                    docs[rel] = f.read()
    return docs


def run_check(repo: str, base: str, head: str, llm_cfg=None, docs_dir=None
              ) -> Tuple[List[Change], List[Finding]]:
    d = git.diff(repo, base, head)
    changes = extract_changes(d, read_head=lambda p: git.show(repo, head, p))
    docs = load_docs_dir(docs_dir) if docs_dir else load_docs(repo, head)
    findings = scan_docs(docs, changes)
    if llm_cfg:
        findings = judge(findings, docs, llm_cfg)
        findings = findings + prose_findings(docs, changes, findings, llm_cfg)
    return changes, findings
