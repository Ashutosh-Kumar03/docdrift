"""Optional judge using any OpenAI-compatible endpoint (Groq, OpenRouter, Ollama...).

Set:  DOCDRIFT_API_KEY, DOCDRIFT_BASE_URL (e.g. https://api.groq.com/openai/v1),
      DOCDRIFT_MODEL (e.g. openai/gpt-oss-20b or llama-3.1-8b-instant)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import List, Optional

from .models import Finding

PROMPT = """You check whether documentation became wrong after a code change.
Code change: {message}
Code evidence: {evidence}
Doc file: {doc}
Doc text (line {line} is the one to judge):
{context}

Answer ONLY with JSON: {{"stale": true|false, "reason": "<one short sentence>", "fix": "<corrected line or empty>"}}"""


class LLMError(Exception):
    pass


ERRORS: List[str] = []   # every failed call is recorded here so it is never silent


def _clean(v: Optional[str]) -> str:
    return (v or "").strip().strip("\"'").strip()


def config() -> Optional[dict]:
    key, base, model = (_clean(os.environ.get(k)) for k in
                        ("DOCDRIFT_API_KEY", "DOCDRIFT_BASE_URL", "DOCDRIFT_MODEL"))
    if key and base and model:
        return {"key": key, "base": base.rstrip("/"), "model": model}
    return None


def _ssl_context():
    try:
        import ssl

        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _ask(cfg: dict, prompt: str) -> dict:
    body = json.dumps({"model": cfg["model"], "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions", data=body,
        headers={"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json",
                 "Accept": "application/json",
                 # some providers (behind Cloudflare) reject Python's default "Python-urllib" agent
                 "User-Agent": "docdrift/0.1 (open-source docs drift checker)"})
    try:
        try:
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code} from {cfg['base']}: {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            hint = " (on macOS run: pip install certifi)" if "CERTIFICATE_VERIFY_FAILED" in str(e.reason) else ""
            raise LLMError(f"could not reach {cfg['base']}: {e.reason}{hint}")
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise LLMError("model did not return JSON: " + content[:200])
        try:
            return json.loads(m.group(0))
        except ValueError:
            raise LLMError("model returned broken JSON: " + m.group(0)[:200])
    except LLMError as e:
        ERRORS.append(str(e))
        raise


def judge(findings: List[Finding], docs: dict, cfg: dict) -> List[Finding]:
    """Verify medium/low findings. High findings are exact string matches, kept as is."""
    out = []
    for f in findings:
        if f.confidence == "high":
            out.append(f)
            continue
        lines = docs[f.doc_file].splitlines()
        a, b = max(0, f.line_no - 4), min(len(lines), f.line_no + 3)
        ctx = "\n".join(f"{i+1}: {lines[i]}" for i in range(a, b))
        try:
            ans = _ask(cfg, PROMPT.format(message=f.message, evidence=f.change.evidence,
                                          doc=f.doc_file, line=f.line_no, context=ctx))
        except Exception as e:  # network/parse problems: keep the finding, say so
            f.llm_note = f"LLM check failed: {e}"
            out.append(f)
            continue
        if ans.get("stale") is False:
            continue
        if ans.get("stale") is True:
            f.confidence = "high"
            f.llm_note = ans.get("reason")
            if ans.get("fix"):
                f.suggestion = ans["fix"]
        out.append(f)
    return out
