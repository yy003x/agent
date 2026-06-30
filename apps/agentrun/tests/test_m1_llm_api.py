"""M1 api:protocol 构造、本地 HTTP stub 全链路、secret 不泄漏。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agentrun.core.config import Profile
from agentrun.core.jsonio import read_jsonl
from agentrun.core.rundir import run_paths
from agentrun.core.run import TURN, RunRequest
from agentrun.providers.llm_api import LlmApiProvider
from agentrun.providers.llm_api.protocols import anthropic, get_protocol, openai


class ProtocolTest(unittest.TestCase):
    def test_openai_payload_headers(self) -> None:
        self.assertEqual(openai.build_payload("m", "hi")["messages"][0]["content"], "hi")
        self.assertEqual(openai.build_headers("KEY")["Authorization"], "Bearer KEY")
        self.assertEqual(openai.extract_text({"choices": [{"message": {"content": "ok"}}]}), "ok")

    def test_anthropic_payload_headers(self) -> None:
        self.assertEqual(anthropic.build_headers("KEY")["x-api-key"], "KEY")
        self.assertIn("anthropic-version", anthropic.build_headers("KEY"))
        self.assertEqual(anthropic.extract_text({"content": [{"text": "ok"}]}), "ok")

    def test_unknown_protocol_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_protocol("bogus")


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8")
        self.seen.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
        if self.path == "/messages":
            payload = {"content": [{"text": "ok from stub"}]}
        else:
            payload = {"choices": [{"message": {"content": "ok from stub"}}]}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


@contextmanager
def _server():
    _Handler.seen = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", _Handler.seen
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()


def _profile(base_url: str, **extra) -> Profile:
    raw = {
        "protocol": "openai",
        "base_url": base_url,
        "model": "m",
        "api_key_env": "AGENTRUN_TEST_KEY",
        **extra,
    }
    return Profile("api-test", "api", "x", "", [], 120, "required", raw=raw)


class LlmApiProviderTest(unittest.TestCase):
    def _run(self, profile, t):
        d = Path(t)
        prompt = d / "p.md"
        prompt.write_text("hello", encoding="utf-8")
        paths = run_paths(d / "runs", "_default", TURN, "turn-1").ensure()
        req = RunRequest(
            run_type=TURN,
            run_id="turn-1",
            provider_profile="api-test",
            provider="api",
            prompt_file=prompt,
            result_file=paths.result_file,
        )
        with patch.dict(os.environ, {"AGENTRUN_TEST_KEY": "super-secret-value"}, clear=False):
            return LlmApiProvider(profile).run(req, paths), paths

    def test_http_stub_reaches_done(self) -> None:
        with _server() as (base_url, seen), tempfile.TemporaryDirectory() as t:
            out, _ = self._run(_profile(base_url), t)
            self.assertEqual(out["status"]["state"], "done")
            self.assertEqual(seen[0]["path"], "/chat/completions")
            self.assertEqual(seen[0]["body"]["model"], "m")
            self.assertEqual(seen[0]["headers"]["Authorization"], "Bearer super-secret-value")

    def test_secret_not_leaked_in_events(self) -> None:
        with _server() as (base_url, _), tempfile.TemporaryDirectory() as t:
            _, paths = self._run(_profile(base_url), t)
            blob = "\n".join(str(r) for r in read_jsonl(paths.events_file))
            self.assertIn("'api_key_env': 'set'", blob)
            self.assertNotIn("super-secret-value", blob)

    def test_event_records_prompt_digest_not_text(self) -> None:
        with _server() as (base_url, _), tempfile.TemporaryDirectory() as t:
            _, paths = self._run(_profile(base_url), t)
            blob = "\n".join(str(r) for r in read_jsonl(paths.events_file))
            self.assertIn("sha256", blob)
            self.assertNotIn("hello", blob)

    def test_header_env_expansion_skips_missing_optional_header(self) -> None:
        with _server() as (base_url, seen), tempfile.TemporaryDirectory() as t:
            profile = _profile(base_url, headers={"X-Optional": "${AGENTRUN_TEST_MISSING}", "X-Set": "${AGENTRUN_TEST_KEY}"})
            out, _ = self._run(profile, t)
            self.assertEqual(out["status"]["state"], "done")
            self.assertNotIn("X-Optional", seen[0]["headers"])
            self.assertEqual(seen[0]["headers"]["X-Set"], "super-secret-value")

    def test_anthropic_auth_header_can_use_bearer(self) -> None:
        with _server() as (base_url, seen), tempfile.TemporaryDirectory() as t:
            profile = _profile(
                base_url,
                protocol="anthropic",
                api_key_header="Authorization",
                api_key_prefix="Bearer ",
            )
            out, _ = self._run(profile, t)
            self.assertEqual(out["status"]["state"], "done")
            self.assertEqual(seen[0]["path"], "/messages")
            self.assertEqual(seen[0]["headers"]["Authorization"], "Bearer super-secret-value")
            self.assertNotIn("x-api-key", seen[0]["headers"])


if __name__ == "__main__":
    unittest.main()
