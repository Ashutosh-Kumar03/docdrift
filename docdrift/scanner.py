"""Find documentation sentences that a set of code changes probably made wrong."""
from __future__ import annotations

import re
from typing import Dict, List

from .models import Change, Finding

RANK = {"high": 0, "medium": 1, "low": 2}


def word_re(name: str, ignore_case: bool = False) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.I if ignore_case else 0)


def value_re(val: str) -> re.Pattern:
    # a value at the end of a sentence ("... is 30.") must still match, but "30.5" or "30x" must not
    return re.compile(r"(?<![\w.])" + re.escape(val) + r"(?!\w|\.\w)")


def symbol_hit(name: str, line: str) -> bool:
    """A function/class name counts as 'mentioned' only in `backticks` or as name(...)."""
    if not word_re(name).search(line):
        return False
    if re.search(r"`[^`]*" + re.escape(name) + r"[^`]*`", line):
        return True
    return bool(re.search(r"(?<![\w.])" + re.escape(name) + r"\s*\(", line))


def _sub(pat: re.Pattern, repl: str) -> tuple:
    return (pat.pattern, pat.flags, repl)


def _apply(subs: List[tuple], text: str) -> str:
    for pattern, flags, repl in subs:
        text = re.compile(pattern, flags).sub(lambda m, r=repl: r, text)
    return text


def scan_docs(docs: Dict[str, str], changes: List[Change]) -> List[Finding]:
    raw: List[Finding] = []
    for path, text in docs.items():
        lines = text.splitlines()
        for ch in changes:
            ic = ch.kind == "default_changed" and not ch.name.startswith("--") and not ch.name.isupper()
            pat = word_re(ch.name, ignore_case=ic)
            if ch.what == "symbol":
                hits = [i for i, l in enumerate(lines) if symbol_hit(ch.name, l)]
            else:
                hits = [i for i, l in enumerate(lines) if pat.search(l)]

            if ch.kind in ("removed", "renamed"):
                kind_word = {"flag": "flag", "env": "env var", "symbol": "function/class"}[ch.what]
                for i in hits:
                    if ch.kind == "renamed":
                        msg = f"{kind_word} `{ch.name}` was renamed to `{ch.new_name}` in {ch.file}"
                        subs = [_sub(pat, ch.new_name)]
                    else:
                        msg = f"{kind_word} `{ch.name}` was removed from {ch.file}"
                        subs = []
                    raw.append(Finding(path, i + 1, lines[i].strip(), "high", msg, ch, subs=subs))

            elif ch.kind == "default_changed" and ch.old_value is not None:
                vpat = value_re(ch.old_value)
                for i in hits:
                    window = range(i, min(i + 3, len(lines)))
                    j = next((k for k in window if vpat.search(lines[k])), None)
                    if j is None:
                        continue
                    conf = "high" if j == i else "medium"
                    msg = (f"default of `{ch.name}` changed from {ch.old_value} to "
                           f"{ch.new_value} in {ch.file}")
                    raw.append(Finding(path, j + 1, lines[j].strip(), conf, msg, ch,
                                       subs=[_sub(vpat, ch.new_value)]))

            elif ch.kind == "added" and len(ch.siblings) >= 2 and not hits:
                sib_lines = [(i, l) for i, l in enumerate(lines)
                             if any(word_re(s).search(l) for s in ch.siblings)]
                mentioned = {s for s in ch.siblings if any(word_re(s).search(l) for _, l in sib_lines)}
                if len(mentioned) >= 2:
                    i = sib_lines[-1][0]
                    msg = (f"new option `{ch.name}` added in {ch.file}, but this doc lists "
                           f"{len(mentioned)} sibling options and not this one")
                    raw.append(Finding(path, i + 1, lines[i].strip(), "low", msg, ch))

    # one finding per doc line: merge reasons and build ONE combined fix
    groups: Dict[tuple, List[Finding]] = {}
    seen = set()
    for f in raw:
        key = (f.doc_file, f.line_no, f.change.name, f.change.kind)
        if key in seen:
            continue
        seen.add(key)
        groups.setdefault((f.doc_file, f.line_no), []).append(f)

    out: List[Finding] = []
    for (_, _), fs in groups.items():
        first = fs[0]
        subs = [s for f in fs for s in f.subs]
        first.message = "; ".join(f.message for f in fs)
        first.confidence = min((f.confidence for f in fs), key=lambda c: RANK[c])
        first.subs = subs
        first.suggestion = _apply(subs, first.sentence) if subs else None
        out.append(first)
    out.sort(key=lambda f: (RANK[f.confidence], f.doc_file, f.line_no))
    return out
