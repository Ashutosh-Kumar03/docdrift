"""LLM-checked prose drift: doc sentences that describe a change without naming it.

Precision guards (the model is never trusted on its own):
  1. Only options / env vars. Function names are ordinary English words ("process", "load"), so
     prose matching for them would be mostly noise; they are handled by the rules (backticks / `name(`).
  2. A line that already names the changed thing is the rules' job, not the model's.
  3. A line that explicitly names a DIFFERENT option (--verbose-mode) is about that option.
  4. The model must quote the exact words that are now wrong. We check the quote really is in the
     line and really mentions the old value / old name. A made-up quote is rejected.
"""
from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from . import llm
from .changes import FLAG_RE
from .models import Change, Finding
from .retrieve import candidates, number_word, words
from .scanner import word_re

MAX_CALLS = 25   # hard cap per run, keeps free API tiers happy

PROMPT = """A code change was made: {desc}.
Code evidence: {evidence}

Here is a line from the documentation (line {line} of {doc}), with its neighbours:
{context}

Look ONLY at the line marked with >>>. Decide:
1. about_change: does that line describe the thing that changed (same option / setting)?
2. stale: if so, is it now wrong because of the change?
3. evidence: copy, word for word, the exact words from the >>> line that are now wrong.
Answer ONLY with JSON: {{"about_change": true|false, "stale": true|false, "evidence": "<exact words from the line>", "reason": "<one short sentence>", "fix": "<corrected line, or empty>"}}"""


def describe(ch: Change) -> str:
    what = {"flag": "option", "env": "environment variable", "symbol": "function/class"}[ch.what]
    if ch.kind == "renamed":
        return f"{what} `{ch.name}` was renamed to `{ch.new_name}`"
    if ch.kind == "removed":
        return f"{what} `{ch.name}` was removed"
    return f"the default of `{ch.name}` changed from {ch.old_value} to {ch.new_value}"


def eligible(ch: Change, line: str) -> bool:
    """Deterministic pre-filter: should this line even be shown to the model?"""
    if ch.what == "symbol":
        return False
    ic = not (ch.name.startswith("--") or ch.name.isupper())
    if word_re(ch.name, ignore_case=ic).search(line):
        return False
    if FLAG_RE.search(line):
        return False
    return True


def evidence_ok(ch: Change, line: str, quote: str) -> bool:
    """The model's quote must be real text from the line and must mention the OLD thing."""
    q = (quote or "").strip().lower()
    if not q or q not in line.lower():
        return False
    if ch.kind == "default_changed":
        old = (ch.old_value or "").lower()
        tokens = {old} if old else set()
        if old.isdigit() and number_word(int(old)):
            tokens |= set(number_word(int(old)).split())
        return any(re.search(r"(?<![\w.])" + re.escape(t) + r"(?!\w)", q) for t in tokens)
    name_words = set(words(re.sub(r"[-_]", " ", ch.name)))
    return any(w in q for w in name_words)


def prose_findings(docs: Dict[str, str], changes: List[Change], existing: List[Finding],
                   cfg: dict, k: int = 3) -> List[Finding]:
    taken: Set[Tuple[str, int]] = {(f.doc_file, f.line_no) for f in existing}
    out: List[Finding] = []
    calls = 0
    for ch in changes:
        if ch.kind not in ("renamed", "removed", "default_changed") or ch.what == "symbol":
            continue
        cands = [c for c in candidates(docs, ch, exclude=taken, k=k * 6) if eligible(ch, c[2])][:k]
        for path, line_no, text in cands:
            if calls >= MAX_CALLS:
                return out
            calls += 1
            lines = docs[path].splitlines()
            a, b = max(0, line_no - 3), min(len(lines), line_no + 2)
            ctx = "\n".join((">>> " if i + 1 == line_no else "    ") + lines[i] for i in range(a, b))
            try:
                ans = llm._ask(cfg, PROMPT.format(desc=describe(ch), evidence=ch.evidence,
                                                  line=line_no, doc=path, context=ctx))
            except Exception:
                continue          # the error is recorded in llm.ERRORS and shown by the CLI
            if (ans.get("about_change") is True and ans.get("stale") is True
                    and evidence_ok(ch, text, ans.get("evidence", ""))):
                taken.add((path, line_no))
                out.append(Finding(path, line_no, text, "medium",
                                   describe(ch) + " (prose, checked by LLM)", ch,
                                   suggestion=ans.get("fix") or None,
                                   llm_note=f'{ans.get("reason", "")} [quote: "{ans["evidence"].strip()}"]'))
    return out
