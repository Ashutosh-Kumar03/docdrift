"""Turn a unified git diff into a list of meaningful Change objects."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Tuple

from .gitutil import is_doc
from .models import Change

FLAG_RE = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*)")
ENV_RE = re.compile(
    r"""(?:environ(?:\.get)?\s*[\[\(]|getenv\s*\()\s*['"]([A-Z][A-Z0-9_]{2,})['"]"""
)
SHORT_RE = re.compile(r"""['"](-[A-Za-z])['"]""")
OPT_CTX = re.compile(r"add_argument\(|\boption\(|add_option\(")
DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")
ARG_DEFAULT_RE = re.compile(
    r"""\(\s*(?:['"]-\w['"]\s*,\s*)?['"](--[\w-]+)['"].*?\bdefault\s*=\s*([^,)]+)"""
)
ASSIGN_RE = re.compile(r"""^\s*([A-Za-z_]\w*)\s*(?::\s*[\w\[\], .|]+)?\s*=\s*([^=].*?)\s*$""")
KV_RE = re.compile(r"""^\s*["']?([A-Za-z_][\w.-]*)["']?\s*:\s*([^:].*?),?\s*$""")
STOP_KEYS = {"else", "try", "except", "finally", "default", "case", "lambda", "return", "self"}


def _skip_path(path: str) -> bool:
    p = path.lower()
    return (
        is_doc(p)
        or p.startswith("tests/") or "/tests/" in p or "/test/" in p
        or p.endswith((".lock", "package-lock.json"))
    )


def parse_diff(text: str) -> Dict[str, Dict[str, List[str]]]:
    """path -> {"removed": [...], "added": [...]} (content lines only)."""
    files: Dict[str, Dict[str, List[str]]] = {}
    cur = None
    in_header = False
    for line in text.splitlines():
        if line.startswith("diff --git"):
            in_header = True
            cur = line.split(" b/", 1)[-1]
            files.setdefault(cur, {"removed": [], "added": []})
        elif in_header:
            if line.startswith("@@"):
                in_header = False
        elif cur is not None:
            if line.startswith("-"):
                files[cur]["removed"].append(line[1:])
            elif line.startswith("+"):
                files[cur]["added"].append(line[1:])
    return files


def _norm(v: str) -> str:
    v = re.sub(r"\s+#.*$", "", v).strip().rstrip(",;").strip()
    return v.strip("'\"")


def extract_default(line: str) -> Tuple[str, str] | None:
    m = ARG_DEFAULT_RE.search(line)
    if m:
        return m.group(1), _norm(m.group(2))
    m = ASSIGN_RE.match(line) or KV_RE.match(line)
    if m and m.group(1) not in STOP_KEYS:
        val = _norm(m.group(2))
        if val and len(val) <= 40 and len(m.group(1)) >= 3:
            return m.group(1), val
    return None


def _names(lines: List[str]) -> Dict[str, Tuple[str, str]]:
    """name -> (what, evidence line). what is flag | env | symbol."""
    out: Dict[str, Tuple[str, str]] = {}
    for l in lines:
        for f in FLAG_RE.findall(l):
            out.setdefault(f, ("flag", l.strip()))
        if OPT_CTX.search(l):
            for sf in SHORT_RE.findall(l):
                out.setdefault(sf, ("flag", l.strip()))
        for e in ENV_RE.findall(l):
            out.setdefault(e, ("env", l.strip()))
        m = DEF_RE.match(l)
        if m and not m.group(1).startswith("_") and len(m.group(1)) >= 4:
            out.setdefault(m.group(1), ("symbol", l.strip()))
    return out


def _pair(gone: List[str], new: List[str], what: str):
    """Match disappeared names to new names. Returns (pairs, still_gone)."""
    if len(gone) == 1 and len(new) == 1:
        r = SequenceMatcher(None, gone[0], new[0]).ratio()
        if what != "symbol" or r >= 0.4:
            return [(gone[0], new[0])], []
    scored = sorted(
        ((SequenceMatcher(None, g, n).ratio(), g, n) for g in gone for n in new), reverse=True
    )
    pairs, used_g, used_n = [], set(), set()
    for r, g, n in scored:
        if r >= 0.6 and g not in used_g and n not in used_n:
            pairs.append((g, n))
            used_g.add(g)
            used_n.add(n)
    return pairs, [g for g in gone if g not in used_g]


def extract_changes(diff_text: str, read_head=None) -> List[Change]:
    """read_head(path) -> file text at the head revision (used to find sibling flags)."""
    files = {p: v for p, v in parse_diff(diff_text).items() if not _skip_path(p)}
    changes: List[Change] = []

    removed_all: Dict[str, Tuple[str, str, str]] = {}   # name -> (path, what, evidence)
    added_all: Dict[str, Tuple[str, str, str]] = {}
    for path, v in files.items():
        for n, (what, ev) in _names(v["removed"]).items():
            removed_all.setdefault(n, (path, what, ev))
        for n, (what, ev) in _names(v["added"]).items():
            added_all.setdefault(n, (path, what, ev))

    for what in ("flag", "env", "symbol"):
        gone = [n for n, t in removed_all.items() if t[1] == what and n not in added_all]
        new = [n for n, t in added_all.items() if t[1] == what and n not in removed_all]
        pairs, still_gone = _pair(gone, new, what)
        for g, n in pairs:
            changes.append(Change("renamed", g, removed_all[g][0], removed_all[g][2],
                                  new_name=n, what=what))
        for g in still_gone:
            changes.append(Change("removed", g, removed_all[g][0], removed_all[g][2], what=what))
        if what == "flag":
            renamed_to = {n for _, n in pairs}
            for n in new:
                if n in renamed_to or not n.startswith("--"):
                    continue
                ch = Change("added", n, added_all[n][0], added_all[n][2], what="flag")
                if read_head:
                    txt = read_head(ch.file) or ""
                    ch.siblings = sorted({f for f in FLAG_RE.findall(txt) if f != n})
                changes.append(ch)

    # changed defaults: same key, different value, within one file
    for path, v in files.items():
        old = {k: val for k, val in filter(None, (extract_default(l) for l in v["removed"]))}
        newd = {k: val for k, val in filter(None, (extract_default(l) for l in v["added"]))}
        for k, ov in old.items():
            nv = newd.get(k)
            if nv is not None and nv != ov:
                ev = next((l.strip() for l in v["added"] if k in l), "")
                changes.append(Change("default_changed", k, path, ev,
                                      old_value=ov, new_value=nv, what="flag"))
    return changes
