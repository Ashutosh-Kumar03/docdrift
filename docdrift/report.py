from __future__ import annotations

import json
from dataclasses import asdict
from typing import List

from .models import Finding

ICON = {"high": "HIGH  ", "medium": "MEDIUM", "low": "LOW   "}


def as_text(findings: List[Finding]) -> str:
    if not findings:
        return "docdrift: no documentation drift found."
    out = []
    for f in findings:
        out.append(f"[{ICON[f.confidence]}] {f.doc_file}:{f.line_no}")
        out.append(f"   doc : {f.sentence}")
        out.append(f"   why : {f.message}")
        if f.llm_note:
            out.append(f"   llm : {f.llm_note}")
        if f.suggestion:
            out.append(f"   fix : {f.suggestion}")
    return "\n".join(out)


def as_markdown(findings: List[Finding]) -> str:
    if not findings:
        return "### docdrift\nNo documentation drift found."
    out = ["### docdrift: documentation may be out of date", ""]
    for f in findings:
        out.append(f"- **{f.confidence.upper()}** `{f.doc_file}:{f.line_no}`: {f.message}")
        out.append(f"  - doc says: `{f.sentence}`")
        if f.suggestion:
            out.append(f"  - suggested: `{f.suggestion}`")
        if f.llm_note:
            out.append(f"  - checked by LLM: {f.llm_note}")
    return "\n".join(out)


def as_json(findings: List[Finding]) -> str:
    return json.dumps([asdict(f) for f in findings], indent=2)
