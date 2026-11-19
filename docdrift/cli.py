from __future__ import annotations

import argparse
import json
import sys

from . import llm, report
from .check import run_check
from .replay import replay


def _warn_llm() -> None:
    if llm.ERRORS:
        print(f"\nWARNING: {len(llm.ERRORS)} LLM call(s) failed, so LLM results are incomplete.", file=sys.stderr)
        print(f"First error: {llm.ERRORS[0]}", file=sys.stderr)
        print("Run `docdrift llm-test` to check your settings.", file=sys.stderr)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="docdrift", description="Flag docs made wrong by a code change.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check one change (e.g. a PR) against the docs")
    c.add_argument("--repo", default=".")
    c.add_argument("--base", required=True, help="base commit/branch (e.g. origin/main)")
    c.add_argument("--head", default="HEAD")
    c.add_argument("--docs-dir", help="read docs from this folder (e.g. a separate docs repo checkout) "
                                      "instead of from the code repo")
    c.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    c.add_argument("--llm", action="store_true", help="verify medium/low findings with an LLM (env vars)")
    c.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="high")

    r = sub.add_parser("replay", help="replay real history: do we catch drift before the human fix?")
    r.add_argument("--repo", default=".")
    r.add_argument("--limit", type=int, default=500, help="how many recent commits to look at")
    r.add_argument("--lookback", type=int, default=8)
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--json", action="store_true")

    sub.add_parser("llm-test", help="check that your DOCDRIFT_* LLM settings work")

    b = sub.add_parser("bench", help="run the built-in benchmark (synthetic cases with known answers)")
    b.add_argument("--llm", action="store_true", help="also use the LLM (needs DOCDRIFT_* env vars)")
    b.add_argument("--markdown", action="store_true", help="print a markdown table for your README/submission")

    a = p.parse_args(argv)

    if a.cmd == "llm-test":
        cfg = llm.config()
        if not cfg:
            print("Set DOCDRIFT_API_KEY, DOCDRIFT_BASE_URL and DOCDRIFT_MODEL first.")
            return 2
        print(f"Testing {cfg['model']} at {cfg['base']} ...")
        try:
            ans = llm._ask(cfg, 'Reply with ONLY this JSON and nothing else: {"ok": true}')
        except Exception as e:
            print("FAILED:", e)
            return 1
        print("OK, model answered:", ans)
        return 0

    if a.cmd == "bench":
        from . import bench
        cfg = llm.config() if a.llm else None
        if a.llm and not cfg:
            print("docdrift: --llm needs DOCDRIFT_API_KEY, DOCDRIFT_BASE_URL, DOCDRIFT_MODEL", file=sys.stderr)
            return 2
        res = bench.run_bench(cfg)
        print(bench.as_markdown(res) if a.markdown else bench.as_text(res))
        _warn_llm()
        return 0 if all(r["ok"] for r in res["rows"] if r["kind"] != "limit") else 1

    if a.cmd == "check":
        cfg = llm.config() if a.llm else None
        if a.llm and not cfg:
            print("docdrift: --llm needs DOCDRIFT_API_KEY, DOCDRIFT_BASE_URL, DOCDRIFT_MODEL", file=sys.stderr)
            return 2
        _, findings = run_check(a.repo, a.base, a.head, cfg, a.docs_dir)
        fmt = {"text": report.as_text, "markdown": report.as_markdown, "json": report.as_json}[a.format]
        print(fmt(findings))
        _warn_llm()
        if a.fail_on == "never":
            return 0
        rank = {"high": 0, "medium": 1, "low": 2}
        return 1 if any(rank[f.confidence] <= rank[a.fail_on] for f in findings) else 0

    res = replay(a.repo, a.limit, a.lookback, a.workers, log=(lambda *_: None) if a.json else print)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        n = res["doc_fix_commits"]
        print(f"\nReal drift fixes found in history: {n}")
        print(f"Caught BEFORE the human fix:       {res['caught']}" + (f" ({100*res['caught']//n}%)" if n else ""))
        c = res["code_commits_without_doc_fix"]
        print(f"Other code commits scanned:        {c}")
        print(f"  flagged with no human doc fix:   {res['flagged_without_human_fix']}"
              + (f" ({100*res['flagged_without_human_fix']//c}%)  <- upper bound on false alarms" if c else ""))
    return 0
