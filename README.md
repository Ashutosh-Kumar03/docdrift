# docdrift

A CI check that flags documentation made wrong by a code change in a pull request.
Standard library only. Works offline; an optional LLM (any OpenAI-compatible, open-weight
model such as gpt-oss-20b or Llama 3.1 8B on Groq/OpenRouter) verifies uncertain findings.

## Use it on pull requests (GitHub Action)
Put this in `.github/workflows/docdrift.yml` of any repo (see `examples/demo-repo/`):

    - uses: actions/checkout@v4
      with: {fetch-depth: 0}
    - uses: YOUR_GITHUB_USERNAME/docdrift@main
      with:
        api-key: ${{ secrets.DOCDRIFT_API_KEY }}   # optional, rules-only without it

Architecture, design answers, results and limits: `docs/ARCHITECTURE.md`.

## Quick start
    pip install -e .
    docdrift check --base origin/main --head HEAD            # one PR
    docdrift check --base origin/main --format markdown      # PR comment text
    docdrift replay --repo /path/to/cloned/repo --limit 1500 # replay real history

Optional LLM verification:
    export DOCDRIFT_API_KEY=...  DOCDRIFT_BASE_URL=https://api.groq.com/openai/v1  DOCDRIFT_MODEL=openai/gpt-oss-20b
    docdrift check --base origin/main --llm

## How it works
1. `changes.py`: parse the git diff into changes: renamed/removed flags and env vars, changed defaults, added flags.
2. `scanner.py`: find the exact doc lines that mention the old name (or old default value) and propose a fix.
   Docs already updated in the same PR never match, because docs are read at the head commit.
3. `llm.py` (optional): medium/low confidence findings are verified by a model; exact matches skip the LLM.
4. `replay.py`: replay git history, check whether we flag a doc line before a human fixed it.

## Prose drift (LLM, optional)
Rules only see drift where the doc names the thing (`--timeout`, `load_config()`).
For sentences like "waits thirty seconds before giving up", `retrieve.py` ranks doc lines by an
IDF-weighted overlap with the changed option (name words, its --help words, old value as digits and
as a word). Only the top 3 lines per change (max 25 model calls per run) go to the LLM, which must say
the line is about the change AND now wrong. Huge docs never reach the model.

    docdrift bench            # synthetic benchmark, rules only
    docdrift bench --llm      # same, plus LLM for the prose cases

### Why the model is not trusted on its own
In our first real run with gpt-oss-20b the prose stage found the "thirty seconds" case but also raised
3 false alarms (a line that named the option itself, the English word "process", a different flag
`--verbose-mode`). So the stage now has deterministic guards: options and env vars only; lines that name the
change, or another option, are never sent; and the model must quote the exact words that are now wrong. The
quote is checked against the line and must mention the old value or name.

macOS note: python.org builds have no CA bundle. `pip install certifi` and docdrift uses it automatically.

## Confidence levels
- high: doc line contains the removed/renamed name, or the old default next to its option name
- medium: old default within 2 lines after the option name
- low: new option missing from a doc that lists its sibling options

## Known limits (honest)
- Python-style flags/env vars/defaults only (argparse, click, os.environ, simple assignments).
- Prose that describes behavior without naming the option is not caught yet.
- Changelogs are skipped on purpose (they describe history).
