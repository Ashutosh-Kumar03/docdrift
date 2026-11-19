import subprocess

from docdrift.check import run_check
from docdrift.replay import replay


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def commit(repo, msg):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


def make_repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "cli.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\n'
        'p.add_argument("--verbose", action="store_true")\n'
        'p.add_argument("--timeout", default=30)\n'
        'p.add_argument("--output")\n'
    )
    (tmp_path / "README.md").write_text(
        "# Tool\n\nRun with --verbose to see more logs.\n\n"
        "| option | meaning |\n|---|---|\n| --timeout | seconds, default 30 |\n| --output | file |\n"
    )
    commit(tmp_path, "initial")


def test_rename_and_default(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "cli.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\n'
        'p.add_argument("--debug", action="store_true")\n'
        'p.add_argument("--timeout", default=60)\n'
        'p.add_argument("--output")\n'
    )
    commit(tmp_path, "rename flag, change default")
    _, findings = run_check(str(tmp_path), "HEAD~1", "HEAD")
    msgs = [(f.doc_file, f.line_no, f.confidence) for f in findings]
    assert ("README.md", 3, "high") in msgs          # --verbose renamed
    assert ("README.md", 7, "high") in msgs          # default 30 -> 60
    sug = {f.line_no: f.suggestion for f in findings}
    assert "--debug" in sug[3]
    assert "60" in sug[7]


def test_no_drift_when_docs_also_updated(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "cli.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\n'
        'p.add_argument("--debug", action="store_true")\n'
        'p.add_argument("--timeout", default=30)\n'
        'p.add_argument("--output")\n'
    )
    (tmp_path / "README.md").write_text(
        (tmp_path / "README.md").read_text().replace("--verbose", "--debug"))
    commit(tmp_path, "rename flag and update docs")
    _, findings = run_check(str(tmp_path), "HEAD~1", "HEAD")
    assert findings == []


def test_replay_catches_late_doc_fix(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "cli.py").write_text(
        (tmp_path / "cli.py").read_text().replace("--verbose", "--debug"))
    commit(tmp_path, "rename --verbose to --debug")
    (tmp_path / "README.md").write_text(
        (tmp_path / "README.md").read_text().replace("--verbose", "--debug"))
    commit(tmp_path, "docs: update flag name")
    res = replay(str(tmp_path), log=lambda *_: None, workers=2)
    assert res["doc_fix_commits"] == 1 and res["caught"] == 1


def test_default_at_end_of_sentence(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "cli.py").write_text('p.add_argument("--timeout", default=30)\n')
    (tmp_path / "README.md").write_text("The default --timeout is 30.\n")
    commit(tmp_path, "initial")
    (tmp_path / "cli.py").write_text('p.add_argument("--timeout", default=60)\n')
    commit(tmp_path, "change default")
    _, findings = run_check(str(tmp_path), "HEAD~1", "HEAD")
    assert len(findings) == 1
    assert findings[0].suggestion == "The default --timeout is 60."


def test_two_problems_one_line_gives_one_combined_fix(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "cli.py").write_text(
        'p.add_argument("--verbose", action="store_true")\np.add_argument("--timeout", default=30)\n')
    (tmp_path / "README.md").write_text(
        "Run with --verbose to see more logs. The default --timeout is 30.\n")
    commit(tmp_path, "initial")
    (tmp_path / "cli.py").write_text(
        'p.add_argument("--debug", action="store_true")\np.add_argument("--timeout", default=60)\n')
    commit(tmp_path, "rename and change default")
    _, findings = run_check(str(tmp_path), "HEAD~1", "HEAD")
    assert len(findings) == 1
    assert findings[0].suggestion == "Run with --debug to see more logs. The default --timeout is 60."


def test_renamed_function_mentioned_in_backticks(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "lib.py").write_text("def load_config(path):\n    return path\n")
    (tmp_path / "README.md").write_text(
        "Call `load_config()` first.\nWe load config lazily.\n")
    commit(tmp_path, "initial")
    (tmp_path / "lib.py").write_text("def read_config(path):\n    return path\n")
    commit(tmp_path, "rename function")
    _, findings = run_check(str(tmp_path), "HEAD~1", "HEAD")
    assert [(f.line_no, f.confidence) for f in findings] == [(1, "high")]
    assert "read_config" in findings[0].suggestion
