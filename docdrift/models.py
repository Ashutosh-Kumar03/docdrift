from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Change:
    """One meaningful thing a code diff changed (flag, env var, symbol, default value)."""
    kind: str                      # removed | renamed | added | default_changed
    name: str                      # flag / env var / symbol / config key
    file: str = ""                 # code file where the change happened
    evidence: str = ""             # the code line that shows the change
    new_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    what: str = "flag"             # flag | env | symbol
    siblings: List[str] = field(default_factory=list)  # other flags next to an added one


@dataclass
class Finding:
    doc_file: str
    line_no: int
    sentence: str
    confidence: str                # high | medium | low
    message: str
    change: Change
    suggestion: Optional[str] = None
    llm_note: Optional[str] = None
    # text substitutions behind the suggestion: (regex, regex_flags, replacement)
    subs: List[tuple] = field(default_factory=list)
