import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from docdrift import llm


def serve(handler):
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, {"key": "k", "base": f"http://127.0.0.1:{srv.server_port}", "model": "m"}


def test_sends_a_real_user_agent_and_parses_json():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen["ua"] = self.headers.get("User-Agent")
            out = json.dumps({"choices": [{"message": {"content": 'sure: {"ok": true}'}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv, cfg = serve(H)
    assert llm._ask(cfg, "hi") == {"ok": True}
    srv.shutdown()
    assert seen["ua"].startswith("docdrift")


def test_http_errors_are_never_silent():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"invalid key"}')

        def log_message(self, *a):
            pass

    llm.ERRORS.clear()
    srv, cfg = serve(H)
    with pytest.raises(llm.LLMError, match="HTTP 401"):
        llm._ask(cfg, "hi")
    srv.shutdown()
    assert llm.ERRORS and "invalid key" in llm.ERRORS[0]


def test_config_strips_quotes_and_spaces(monkeypatch):
    monkeypatch.setenv("DOCDRIFT_API_KEY", ' "abc" ')
    monkeypatch.setenv("DOCDRIFT_BASE_URL", "https://x.test/v1/")
    monkeypatch.setenv("DOCDRIFT_MODEL", "m")
    assert llm.config() == {"key": "abc", "base": "https://x.test/v1", "model": "m"}
