"""api provider:按 protocol 调远端 LLM,汇聚成 result.json(见 design/03 §2)。

secret 只通过 api_key_env 引用,日志只记 set|missing。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from agentrun.core.config import Profile
from agentrun.core.contract import event, mark_done_if_valid, write_result, write_status
from agentrun.core.rundir import RunPaths
from agentrun.core.run import RUNNING, RunRequest
from agentrun.logging import prompt_digest
from agentrun.providers.llm_api.protocols import get_protocol


class LlmApiProvider:
    transport = "api"

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        raw = profile.raw
        self.protocol_name = str(raw.get("protocol", "openai"))
        self.base_url = str(raw.get("base_url") or raw.get("host") or "").rstrip("/")
        self.model = str(raw.get("model", ""))
        self.api_key_env = str(raw.get("api_key_env", ""))
        self.headers = dict(raw.get("headers") or {})
        self.mock = bool(raw.get("mock", False))

    def run(self, request: RunRequest, paths: RunPaths) -> dict[str, Any]:
        protocol = get_protocol(self.protocol_name)
        prompt = ""
        if request.prompt_file and request.prompt_file.exists():
            prompt = request.prompt_file.read_text(encoding="utf-8")

        api_key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        write_status(paths, request, RUNNING, message="api running")
        event(
            paths,
            request,
            "status.changed",
            {
                "state": RUNNING,
                "transport": self.transport,
                "protocol": self.protocol_name,
                "model": self.model,
                "api_key_env": "set" if api_key else "missing",
                "prompt": prompt_digest(prompt),
            },
        )

        if self.mock:
            text = f"[mock {self.protocol_name}:{self.model}] {len(prompt)} chars"
            return self._finish(paths, request, text)

        if not api_key:
            status = write_status(
                paths, request, "failed", failure_reason="provider_error", message="api_key 缺失(检查 api_key_env)"
            )
            return {"status": status}

        try:
            payload = protocol.build_payload(self.model, prompt)
            headers = {**protocol.build_headers(api_key), **_expanded_headers(self.headers)}
            response = self._post(self.base_url + protocol.ENDPOINT, payload, headers, request.deadline_seconds or 120)
            text = protocol.extract_text(response)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
            status = write_status(
                paths, request, "failed", failure_reason="provider_error", message=f"调用失败: {type(exc).__name__}"
            )
            return {"status": status}
        return self._finish(paths, request, text)

    def _finish(self, paths: RunPaths, request: RunRequest, text: str) -> dict[str, Any]:
        paths.output_log.write_text(text + "\n", encoding="utf-8")
        write_result(paths, request, "succeeded", summary=text)
        status = mark_done_if_valid(paths, request)
        return {"status": status}

    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 url 来自配置
            return json.loads(resp.read().decode("utf-8"))


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expanded_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        text = str(value)
        missing = False
        for name in _ENV_PATTERN.findall(text):
            if name not in os.environ:
                missing = True
            text = text.replace("${" + name + "}", os.environ.get(name, ""))
        if missing:
            continue
        if text:
            out[str(key)] = text
    return out
