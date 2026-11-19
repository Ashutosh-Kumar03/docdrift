"""Find doc lines that might describe a changed thing WITHOUT naming it (cheap, no model).

Idea: build a small query from the change (words of the option name, words of its --help text,
the old default value as digits and as a word) and rank doc lines with an IDF-weighted overlap.
Only the top few lines go to the LLM, so a huge docs folder never reaches the model.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Set, Tuple

from .models import Change

WORD = re.compile(r"[a-z][a-z0-9]{2,}")
NUMBER = re.compile(r"\b\d+\b")
STOPW = {"the", "and", "for", "with", "this", "that", "from", "are", "you", "use", "can", "will",
         "not", "set", "option", "flag", "default", "value", "when", "into", "your", "has", "was"}
ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen"]
TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
        80: "eighty", 90: "ninety", 100: "hundred"}


def number_word(n: int) -> str | None:
    if 0 <= n < 20:
        return ONES[n]
    if n in TENS:
        return TENS[n]
    if 20 < n < 100:
        return TENS[n // 10 * 10] + " " + ONES[n % 10]
    return None


def words(s: str) -> List[str]:
    return [w for w in WORD.findall(s.lower()) if w not in STOPW]


def query_for(ch: Change) -> Tuple[Set[str], Set[str]]:
    """(name/help words, value tokens)"""
    name_words = set(words(re.sub(r"[-_]", " ", ch.name)))
    for h in re.findall(r"""help\s*=\s*['"]([^'"]+)""", ch.evidence or ""):
        name_words |= set(words(h))
    value_tokens: Set[str] = set()
    if ch.old_value and ch.old_value.isdigit():
        value_tokens.add(ch.old_value)
        w = number_word(int(ch.old_value))
        if w:
            value_tokens |= set(w.split())
    return name_words, value_tokens


def candidates(docs: Dict[str, str], ch: Change, exclude: Set[Tuple[str, int]], k: int = 3
               ) -> List[Tuple[str, int, str]]:
    name_words, value_tokens = query_for(ch)
    if not name_words and not value_tokens:
        return []
    rows = []
    df: Counter = Counter()
    for path, text in docs.items():
        for i, line in enumerate(text.splitlines()):
            toks = set(words(line)) | set(NUMBER.findall(line))
            df.update(toks)
            rows.append((path, i, line, toks))
    n = max(len(rows), 1)
    scored = []
    for path, i, line, toks in rows:
        if (path, i + 1) in exclude or not line.strip():
            continue
        score = sum(math.log(1 + n / df[w]) for w in name_words & toks)
        score += 1.0 * len(value_tokens & toks)
        if score > 0:
            scored.append((score, path, i + 1, line.strip()))
    scored.sort(key=lambda x: -x[0])
    return [(p, ln, text) for _, p, ln, text in scored[:k]]
