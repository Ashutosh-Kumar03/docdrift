# docdrift: architecture and trade-offs

docdrift is a CI check that runs on a pull request. It finds documentation lines the change has probably
made wrong, and points at the exact line and the exact code change.

## Pipeline

    git diff (base..head)
          │
          ▼
    changes.py   what did the code change? (renamed/removed option, env var, function/class;
          │       changed default value; new option)  ← only the diff is read, never the whole repo
          ▼
    scanner.py   rules: doc lines that name the old thing, or sit next to its old default value
          │       → HIGH / MEDIUM / LOW findings, each with a ready-made fix
          ▼
    retrieve.py  prose candidates: doc lines that describe the thing without naming it
          │       (IDF-weighted overlap with the option's name words, --help words, old value)
          ▼
    prose.py     optional open-weight LLM, behind deterministic guards → MEDIUM findings
          ▼
    report.py    text / markdown (PR comment) / json        action.yml → sticky PR comment

## The four design questions

**1. How do you link a doc sentence to code when nobody wrote the link?**
Three tiers, from cheap and certain to expensive and uncertain.
(a) Exact names: a flag, env var or `backticked` function name that the diff removed or renamed, found
in the docs with word-boundary matching (`--verbose` does not match `--verbose-mode`).
(b) Values: an option's old default next to the option's name (same line, or within two lines).
(c) Prose: "waits thirty seconds" never names `--timeout`. We rank doc lines by overlap with the option's
name words, its `help=` text and the old value (as digits and as a word), then ask the model about the top 3
lines only. The model must quote the exact wrong words, and the quote is checked against the line.

**2. How do you keep it current on each PR without rescanning everything?**
We never scan the code base: the unit of work is the diff. Docs are read from git at the head commit, so
docs fixed in the same PR never match. The expensive part (the LLM) sees at most 3 lines per change and at
most 25 calls per run. Honest limit: the doc index is rebuilt on each run (a few string passes, fast for
the doc sizes we tried). A cache keyed by git blob SHA is the obvious next step and is not built.

**3. How do you handle docs in a separate repository or website?**
`docdrift check --docs-dir PATH` reads docs from a folder instead of from the code repo. In CI, check the
docs repo out next to the code and pass its path (`docs-path` input of the action). Docs that only exist as
a rendered website are not handled.

**4. How do you keep false alarms low enough that maintainers keep the check on?**
- Confidence levels, and `--fail-on` so the check can stay advisory.
- Changelog / history files are skipped (they describe the past, so old names there are correct).
- Test files and docs files in the diff are not treated as code changes.
- The model is never trusted alone (see below). Everything it adds is MEDIUM and carries its quote.

## Why the model is behind guards
Our first real run with gpt-oss-20b (via Groq) found the prose case but also raised 3 false alarms on the
benchmark: a line that already named the option, the English word "process" (a renamed function), and a line
about a different flag (`--verbose-mode`). The fixes are deterministic, not prompt tweaks:
options and env vars only; lines that name the change or another option are never sent; the model's quoted
evidence must be real text from the line and must mention the old value or name.

## Results (what is measured, and what is not)

Synthetic benchmark (`docdrift bench`, 23 cases written by us, so treat it as a regression suite, not a
proof): 13/13 drift cases caught, 10/10 look-alike cases quiet, 0 false alarms. With gpt-oss-20b through
Groq the prose case ("waits thirty seconds") is also caught (1 of 2 known limits; one run, model output can
vary). Still missed: behaviour changes with no name change ("now returns None instead of raising").

Real history (`docdrift replay`, rules-only version, public repos: click, black, flake8, pre-commit, tqdm,
yt-dlp, pip-tools, httpie): auto-mining "doc fix after code change" pairs gave almost no genuine pairs.
Only pip-tools produced candidates: 3 candidates, 2 flagged by us; one is genuine (code removed `--auto` and
`--interactive`; the README section naming them was removed later, commit 8e4abf3e), one is a coincidence
(`--upgrade` in a `pip install --upgrade pip` line, commit 01adaefd), one is a changelog entry. So the real
evidence is one confirmed catch, and we do not claim a recall number from it. "Flagged without a human fix"
was 0-4% of code commits on most repos and about 10% on yt-dlp; that is an upper bound on false alarms (it
also contains real drift nobody fixed). The mkdocs run did not finish.

## Known limits
Python-style option definitions only (argparse, click, os.environ, simple assignments). Behaviour changes
with no rename are not seen. The "medium" rule (old default within two lines of the option name) can
misfire on an unrelated number nearby. Short and long flags declared together
(`"-v", "--verbose"` → `"-d", "--debug"`) may be reported as removed instead of renamed.

## Open-weight models
Any OpenAI-compatible endpoint works through three environment variables. Tested with `openai/gpt-oss-20b`
on Groq's free tier. No API key is needed for the rules-only mode.
