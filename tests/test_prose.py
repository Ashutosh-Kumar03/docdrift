import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from docdrift.check import run_check

PROMPTS = []
QUOTE = {"value": "waits thirty seconds"}


class Fake(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][0]["content"]
        PROMPTS.append(prompt)
        yes = ">>> The tool waits thirty seconds" in prompt
        content = json.dumps({"about_change": yes, "stale": yes, "evidence": QUOTE["value"],
                              "reason": "says thirty, now sixty",
                              "fix": "The tool waits sixty seconds before giving up." if yes else ""})
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True)


def make(tmp_path, readme):
    git(tmp_path, "init", "-q")
    (tmp_path / "cli.py").write_text('p.add_argument("--timeout", default=30)\n')
    (tmp_path / "README.md").write_text(readme)
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "a")
    (tmp_path / "cli.py").write_text('p.add_argument("--timeout", default=60)\n')
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "b")


def with_server():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, {"key": "x", "base": f"http://127.0.0.1:{srv.server_port}", "model": "fake"}


def test_prose_drift_needs_llm_and_is_found_with_it(tmp_path):
    make(tmp_path, "The --timeout option controls the wait.\n"
                   "The tool waits thirty seconds before giving up.\n\n\n\nRetries: 30 attempts.\n")
    _, without = run_check(str(tmp_path), "HEAD~1", "HEAD")
    assert without == []                      # rules alone cannot see it

    PROMPTS.clear(); QUOTE["value"] = "waits thirty seconds"
    srv, cfg = with_server()
    _, with_llm = run_check(str(tmp_path), "HEAD~1", "HEAD", cfg)
    srv.shutdown()
    assert [(f.line_no, f.confidence) for f in with_llm] == [(2, "medium")]
    assert with_llm[0].suggestion == "The tool waits sixty seconds before giving up."
    # a line that already names --timeout is never sent to the model
    assert not any(">>> The --timeout option" in p for p in PROMPTS)


def test_made_up_quote_is_rejected(tmp_path):
    make(tmp_path, "The tool waits thirty seconds before giving up.\n")
    PROMPTS.clear(); QUOTE["value"] = "waits ninety seconds"      # not in the line
    srv, cfg = with_server()
    _, found = run_check(str(tmp_path), "HEAD~1", "HEAD", cfg)
    srv.shutdown()
    assert found == []


def test_quote_must_mention_the_old_value(tmp_path):
    make(tmp_path, "The tool waits thirty seconds before giving up.\n")
    PROMPTS.clear(); QUOTE["value"] = "before giving up"           # real text, but not about the value
    srv, cfg = with_server()
    _, found = run_check(str(tmp_path), "HEAD~1", "HEAD", cfg)
    srv.shutdown()
    assert found == []


def test_functions_and_other_flags_are_never_sent_to_the_model(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "lib.py").write_text('def process(x):\n    return x\np.add_argument("--verbose", action="store_true")\n')
    (tmp_path / "README.md").write_text("We process the data lazily.\nSee the --verbose-mode section.\n")
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "a")
    (tmp_path / "lib.py").write_text('def handle(x):\n    return x\np.add_argument("--debug", action="store_true")\n')
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "b")
    PROMPTS.clear()
    srv, cfg = with_server()
    _, found = run_check(str(tmp_path), "HEAD~1", "HEAD", cfg)
    srv.shutdown()
    assert found == [] and PROMPTS == []
