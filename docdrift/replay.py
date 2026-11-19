"""Historical replay: did we flag a doc line BEFORE a human fixed it by hand?

Answer key (independent of our detector's rules):
  A docs-only commit D is a "drift fix" if a code commit C shortly before it made a specific
  name VANISH from the code (a flag, env var or identifier that was in removed lines and in
  no added line), and D removed doc lines that mention that same name.
We then run docdrift on C (docs as they were at C) and count it as CAUGHT if one of our
findings points at a doc line that D removed/changed.

"flagged without a human fix" is an upper bound on false alarms: it also contains real
drift nobody ever fixed.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Set

from . import gitutil as git
from .changes import FLAG_RE, _skip_path, parse_diff
from .check import HISTORY_FILE, run_check

IDENT = re.compile(r"[A-Za-z_]\w{4,}")
STOP = {"self", "None", "True", "False", "class", "return", "import", "from", "print",
        "str", "string", "value", "values", "name", "names", "file", "files", "path",
        "args", "kwargs", "type", "types", "data", "default", "option", "options", "param"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _commits(repo: str, limit: int):
    raw = git.run(repo, "log", "--no-merges", f"-n{limit}", "--format=%H%x09%P%x09%s")
    out = []
    for l in raw.splitlines():
        h, parents, subj = (l.split("\t", 2) + ["", ""])[:3]
        out.append((h, parents.split()[0] if parents else None, subj))
    return out


def _files(repo: str, c: str):
    return [l for l in git.run(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", c).splitlines() if l]


def specific(t: str) -> bool:
    """Looks like a real code name, not an ordinary English word."""
    if t in STOP:
        return False
    if t.startswith("--"):
        return len(t) > 3
    core = t.strip("_")
    if "_" in core and len(core) >= 5:                       # snake_case / UPPER_SNAKE
        return True
    if re.fullmatch(r"[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+", t):  # CamelCase
        return True
    if re.fullmatch(r"[a-z]+(?:[A-Z][a-z0-9]*)+", t):          # camelCase
        return True
    return False


def doc_tokens(line: str) -> Set[str]:
    """Specific names mentioned in a doc line (flags, ENV_VARS, `backticked`, snake_case, CamelCase)."""
    t = set(FLAG_RE.findall(line))
    t |= set(re.findall(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b", line))
    for span in re.findall(r"`([^`]+)`", line):
        t |= set(IDENT.findall(span))
    t |= set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", line))
    t |= set(re.findall(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b", line))
    return {x for x in t if specific(x)}


def vanished(diff_text: str) -> Set[str]:
    rem: Set[str] = set()
    add: Set[str] = set()
    for p, v in parse_diff(diff_text).items():
        if _skip_path(p):
            continue
        for l in v["removed"]:
            rem |= set(IDENT.findall(l)) | set(FLAG_RE.findall(l))
        for l in v["added"]:
            add |= set(IDENT.findall(l)) | set(FLAG_RE.findall(l))
    return {t for t in rem - add if specific(t)}


def _work(repo: str, c: str, parent: str):
    try:
        _, findings = run_check(repo, parent, c)
        return findings, vanished(git.diff(repo, parent, c))
    except Exception:
        return None


def replay(repo: str, limit: int = 500, lookback: int = 8, workers: int = 8, log=print) -> dict:
    commits = _commits(repo, limit)
    info = {}
    for h, parent, subj in commits:
        fs = _files(repo, h)
        info[h] = {"parent": parent, "subj": subj,
                   "docs_only": bool(fs) and all(git.is_doc(f) for f in fs),
                   "has_code": any(not git.is_doc(f) for f in fs)}
    code = [(h, info[h]["parent"]) for h, _, _ in commits if info[h]["has_code"] and info[h]["parent"]]
    with ThreadPoolExecutor(workers) as ex:
        outs = list(ex.map(lambda hp: _work(repo, hp[0], hp[1]), code))
    scanned = {h: o for (h, _), o in zip(code, outs) if o}

    results, gt_code = [], set()
    for idx, (h, parent, subj) in enumerate(commits):
        if not (info[h]["docs_only"] and parent):
            continue
        removed: Dict[str, Set[str]] = {
            p: {_norm(x) for x in v["removed"] if x.strip()}
            for p, v in parse_diff(git.diff(repo, parent, h)).items()
        }
        removed = {p: s for p, s in removed.items()
                   if s and not HISTORY_FILE.search(p.rsplit("/", 1)[-1])}
        toks: Set[str] = set()
        for s in removed.values():
            for l in s:
                toks |= doc_tokens(l)
        # the doc fix must really DROP the name from the docs (not just reword around it)
        still_there: Set[str] = set()
        for p, v in parse_diff(git.diff(repo, parent, h)).items():
            if p in removed:
                for l in v["added"]:
                    still_there |= doc_tokens(l)
        toks -= still_there
        if not toks:
            continue
        pairs = []
        for ch, _, _ in commits[idx + 1: idx + 1 + lookback]:
            if ch in scanned and (toks & scanned[ch][1]):
                pairs.append(ch)
        if not pairs:
            continue
        best = None
        for ch in pairs:
            gt_code.add(ch)
            for f in scanned[ch][0]:
                if _norm(f.sentence) in removed.get(f.doc_file, set()):
                    best = (ch, f)
                    break
            if best:
                break
        ch0 = best[0] if best else pairs[0]
        res = {"doc_fix": h[:8], "subject": subj, "code_commit": ch0[:8],
               "names": sorted(toks & scanned[ch0][1])[:4], "caught": bool(best)}
        if best:
            res["doc"] = f"{best[1].doc_file}:{best[1].line_no}"
            res["why"] = best[1].message
        results.append(res)
        log(f"{'CAUGHT' if best else 'missed'}  fix {h[:8]} <- code {ch0[:8]}  {subj[:60]}  {res['names']}")

    caught = sum(r["caught"] for r in results)
    other = [h for h in scanned if h not in gt_code]
    flagged = [h for h in other if scanned[h][0]]
    return {
        "doc_fix_commits": len(results), "caught": caught,
        "code_commits_scanned": len(scanned),
        "code_commits_without_doc_fix": len(other),
        "flagged_without_human_fix": len(flagged),
        "results": results,
    }
