"""Built-in benchmark: small git repos with a known answer.

Every case builds a tiny repo (before / after a code change), runs docdrift on that change
and compares the flagged doc lines with the expected ones. These are SYNTHETIC cases, written
to cover the kinds of drift the tool claims to handle, plus look-alike cases where it must
stay quiet (false-alarm checks). Real-history results come from `docdrift replay`.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Set, Tuple

from .check import run_check

CLI = 'import argparse\np = argparse.ArgumentParser()\n{lines}'


def argp(*args: str) -> str:
    return CLI.format(lines="".join(f"p.add_argument({a})\n" for a in args))


V, VD = '"--verbose", action="store_true"', '"--debug", action="store_true"'
T30, T60 = '"--timeout", default=30', '"--timeout", default=60'

# name, kind (drift|clean), before files, after files (None = deleted), expected {(doc, line)}
CASES: List[dict] = [
    # ---------------- drift: the tool SHOULD flag these ----------------
    dict(name="flag renamed, README still old", kind="drift",
         before={"cli.py": argp(V), "README.md": "# Tool\n\nRun with --verbose to see logs.\n"},
         after={"cli.py": argp(VD)}, expected={("README.md", 3)}),
    dict(name="flag removed, still in code block", kind="drift",
         before={"cli.py": argp(V, T30), "README.md": "Usage:\n\n    tool --verbose --timeout 5\n"},
         after={"cli.py": argp(T30)}, expected={("README.md", 3)}),
    dict(name="default changed, config table row", kind="drift",
         before={"cli.py": argp(T30), "README.md": "| option | default |\n|---|---|\n| --timeout | 30 |\n"},
         after={"cli.py": argp(T60)}, expected={("README.md", 3)}),
    dict(name="default changed, value on next line", kind="drift",
         before={"cli.py": argp(T30), "README.md": "The --timeout option controls the wait.\nDefault: 30 seconds.\n"},
         after={"cli.py": argp(T60)}, expected={("README.md", 2)}),
    dict(name="env var renamed", kind="drift",
         before={"app.py": 'import os\ntok = os.environ.get("APP_TOKEN")\n',
                 "README.md": "Set APP_TOKEN before starting.\n"},
         after={"app.py": 'import os\ntok = os.environ.get("APP_API_TOKEN")\n'},
         expected={("README.md", 1)}),
    dict(name="function renamed, mentioned in backticks", kind="drift",
         before={"lib.py": "def load_config(path):\n    return path\n",
                 "README.md": "Call `load_config()` first.\n"},
         after={"lib.py": "def read_config(path):\n    return path\n"}, expected={("README.md", 1)}),
    dict(name="class removed, still documented", kind="drift",
         before={"lib.py": "class OldClient:\n    pass\n", "README.md": "Create an `OldClient` to connect.\n"},
         after={"lib.py": "x = 1\n"}, expected={("README.md", 1)}),
    dict(name="drift in docs/ .rst file", kind="drift",
         before={"cli.py": argp(V), "docs/usage.rst": "Options\n=======\n\nUse ``--verbose`` for logs.\n"},
         after={"cli.py": argp(VD)}, expected={("docs/usage.rst", 4)}),
    dict(name="rename + default change on one line", kind="drift",
         before={"cli.py": argp(V, T30), "README.md": "Use --verbose for logs. The --timeout default is 30.\n"},
         after={"cli.py": argp(VD, T60)}, expected={("README.md", 1)}),
    dict(name="click option renamed", kind="drift",
         before={"cli.py": 'import click\n@click.option("--verbose", is_flag=True)\ndef main(verbose): pass\n',
                 "README.md": "Pass --verbose to see more output.\n"},
         after={"cli.py": 'import click\n@click.option("--debug", is_flag=True)\ndef main(debug): pass\n'},
         expected={("README.md", 1)}),
    dict(name="two doc lines mention removed flag", kind="drift",
         before={"cli.py": argp(V, T30), "README.md": "Intro.\nUse --verbose here.\n\nMore text.\nAlso --verbose there.\n"},
         after={"cli.py": argp(T30)}, expected={("README.md", 2), ("README.md", 5)}),
    dict(name="new flag missing from options list", kind="drift",
         before={"cli.py": argp('"--alpha"', '"--beta"'),
                 "README.md": "Options:\n\n- --alpha: first\n- --beta: second\n"},
         after={"cli.py": argp('"--alpha"', '"--beta"', '"--gamma"')}, expected={("README.md", 4)}),
    # ---------------- clean: the tool must stay QUIET ----------------
    dict(name="rename, docs updated in same change", kind="clean",
         before={"cli.py": argp(V), "README.md": "Run with --verbose.\n"},
         after={"cli.py": argp(VD), "README.md": "Run with --debug.\n"}, expected=set()),
    dict(name="internal refactor, no flags or names", kind="clean",
         before={"cli.py": argp(V), "util.py": "def _helper(x):\n    return x + 1\n", "README.md": "Run with --verbose.\n"},
         after={"util.py": "def _helper(x):\n    return 1 + x\n"}, expected=set()),
    dict(name="old flag only in CHANGELOG (history)", kind="clean",
         before={"cli.py": argp(V), "CHANGELOG.md": "- added --verbose\n"},
         after={"cli.py": argp(VD)}, expected=set()),
    dict(name="default changed, doc has unrelated 30", kind="clean",
         before={"cli.py": argp(T30), "README.md": "The --timeout flag sets the wait.\n\n\n\nRetries: 30 attempts.\n"},
         after={"cli.py": argp(T60)}, expected=set()),
    dict(name="function renamed, plain word in prose", kind="clean",
         before={"lib.py": "def process(x):\n    return x\n", "README.md": "We process the data lazily.\n"},
         after={"lib.py": "def handle(x):\n    return x\n"}, expected=set()),
    dict(name="flag moved to another file", kind="clean",
         before={"a.py": argp(V), "b.py": "x = 1\n", "README.md": "Run with --verbose.\n"},
         after={"a.py": "x = 2\n", "b.py": argp(V)}, expected=set()),
    dict(name="new flag, doc lists only one sibling", kind="clean",
         before={"cli.py": argp('"--alpha"', '"--beta"'), "README.md": "Only --alpha is described here.\n"},
         after={"cli.py": argp('"--alpha"', '"--beta"', '"--gamma"')}, expected=set()),
    dict(name="whitespace-only reformat", kind="clean",
         before={"cli.py": argp(V, T30), "README.md": "Use --verbose. Default --timeout is 30.\n"},
         after={"cli.py": argp('"--verbose",  action="store_true"', '"--timeout",  default=30')}, expected=set()),
    dict(name="only a test file changes", kind="clean",
         before={"cli.py": argp(V), "tests/test_cli.py": 'ARGS = ["--verbose"]\n', "README.md": "Run with --verbose.\n"},
         after={"tests/test_cli.py": 'ARGS = ["--quiet"]\n'}, expected=set()),
    dict(name="similar flag name (--verbose-mode)", kind="clean",
         before={"cli.py": argp(V), "README.md": "See the --verbose-mode section.\n"},
         after={"cli.py": argp(VD)}, expected=set()),
    # ---------------- known limits: drift the rules-only tool does NOT see yet ----------------
    dict(name="prose says 'thirty seconds', never names the flag", kind="limit",
         before={"cli.py": argp(T30), "README.md": "The tool waits thirty seconds before giving up.\n"},
         after={"cli.py": argp(T60)}, expected={("README.md", 1)}),
    dict(name="short flag -v renamed to -d", kind="drift",
         before={"cli.py": argp('"-v", action="store_true"'), "README.md": "Use -v for more logs.\n"},
         after={"cli.py": argp('"-d", action="store_true"')}, expected={("README.md", 1)}),
    dict(name="behaviour changed, no name changed", kind="limit",
         before={"lib.py": "def parse(x):\n    raise ValueError(x)\n", "README.md": "`parse` raises ValueError on bad input.\n"},
         after={"lib.py": "def parse(x):\n    return None\n"}, expected={("README.md", 1)}),
]


def _git(repo: str, *a: str) -> None:
    subprocess.run(["git", "-C", repo, "-c", "user.name=bench", "-c", "user.email=b@b", *a],
                   check=True, capture_output=True)


def _write(repo: str, files: Dict[str, Optional[str]]) -> None:
    for path, text in files.items():
        full = os.path.join(repo, path)
        if text is None:
            if os.path.exists(full):
                os.remove(full)
            continue
        os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)


def run_case(case: dict, llm_cfg=None) -> Tuple[Set[Tuple[str, int]], list]:
    with tempfile.TemporaryDirectory() as repo:
        _git(repo, "init", "-q")
        _write(repo, case["before"])
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "before")
        _write(repo, case["after"])
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "after")
        _, findings = run_check(repo, "HEAD~1", "HEAD", llm_cfg)
    return {(f.doc_file, f.line_no) for f in findings}, findings


def run_bench(llm_cfg=None) -> dict:
    rows, found, expected_total, false_alarms = [], 0, 0, 0
    for c in CASES:
        flagged, _ = run_case(c, llm_cfg)
        exp = c["expected"]
        hit = flagged & exp
        extra = flagged - exp
        if c["kind"] != "limit":
            found += len(hit)
            expected_total += len(exp)
        if c["kind"] != "limit":
            false_alarms += len(extra)
        rows.append({"name": c["name"], "kind": c["kind"], "ok": flagged == exp,
                     "caught": len(hit), "expected": len(exp), "false_alarms": len(extra)})
    drift = [r for r in rows if r["kind"] == "drift"]
    clean = [r for r in rows if r["kind"] == "clean"]
    limits = [r for r in rows if r["kind"] == "limit"]
    return {
        "rows": rows,
        "limit_cases": len(limits), "limit_caught": sum(r["ok"] for r in limits),
        "drift_cases": len(drift), "drift_caught": sum(r["caught"] == r["expected"] and r["ok"] for r in drift),
        "doc_lines_caught": found, "doc_lines_expected": expected_total,
        "clean_cases": len(clean), "clean_quiet": sum(r["ok"] for r in clean),
        "false_alarms": false_alarms,
    }


def as_text(res: dict) -> str:
    out = []
    for r in res["rows"]:
        mark = ("PASS" if r["ok"] else "FAIL") if r["kind"] != "limit" else ("caught" if r["ok"] else "MISSED")
        extra = f"  (+{r['false_alarms']} false alarm)" if r["false_alarms"] else ""
        out.append(f"{mark}  [{r['kind']:5}] {r['name']}{extra}")
    out.append("")
    out.append(f"Drift cases caught:   {res['drift_caught']}/{res['drift_cases']}"
               f"   (doc lines {res['doc_lines_caught']}/{res['doc_lines_expected']})")
    out.append(f"Clean cases quiet:    {res['clean_quiet']}/{res['clean_cases']}")
    out.append(f"False alarms total:   {res['false_alarms']}")
    out.append(f"Known limits caught:  {res['limit_caught']}/{res['limit_cases']}   (not counted above; honest gaps)")
    out.append("(synthetic cases, see docdrift/bench.py)")
    return "\n".join(out)


def as_markdown(res: dict) -> str:
    out = ["| Case | Type | Result |", "|---|---|---|"]
    for r in res["rows"]:
        typ = {"drift": "drift", "clean": "no drift", "limit": "known limit"}[r["kind"]]
        res_txt = ("pass" if r["ok"] else "FAIL") if r["kind"] != "limit" else ("caught" if r["ok"] else "missed")
        out.append(f"| {r['name']} | {typ} | {res_txt} |")
    out.append("")
    out.append(f"**Drift caught:** {res['drift_caught']}/{res['drift_cases']} · "
               f"**Clean cases quiet:** {res['clean_quiet']}/{res['clean_cases']} · "
               f"**False alarms:** {res['false_alarms']} · "
               f"**Known limits caught:** {res['limit_caught']}/{res['limit_cases']}  _(synthetic benchmark)_")
    return "\n".join(out)
