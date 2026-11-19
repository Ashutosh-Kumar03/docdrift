import subprocess

from docdrift.check import run_check


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True)


def test_docs_in_a_separate_folder(tmp_path):
    code, docs = tmp_path / "code", tmp_path / "docs-site"
    code.mkdir(); (docs / "guide").mkdir(parents=True)
    git(code, "init", "-q")
    (code / "cli.py").write_text('p.add_argument("--verbose", action="store_true")\n')
    git(code, "add", "-A"); git(code, "commit", "-qm", "a")
    (code / "cli.py").write_text('p.add_argument("--debug", action="store_true")\n')
    git(code, "add", "-A"); git(code, "commit", "-qm", "b")
    (docs / "guide" / "usage.md").write_text("Pass --verbose for logs.\n")

    _, inside = run_check(str(code), "HEAD~1", "HEAD")
    assert inside == []                                   # the code repo itself has no docs
    _, found = run_check(str(code), "HEAD~1", "HEAD", docs_dir=str(docs))
    assert [(f.doc_file, f.line_no) for f in found] == [("guide/usage.md", 1)]
