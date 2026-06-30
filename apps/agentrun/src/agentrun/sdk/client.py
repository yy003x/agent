"""AgentRunClient:驱动远端 AgentRun 实例的 client(类比 docker client,见 design/08 §3)。

内核不提供 server;把 RuntimeService 暴露成远端服务是上层应用的职责。本 client 只把动词
调用转成 transport 请求,按文件契约 / 返回读回状态。transport 由调用方注入(HTTP/进程/本机回环)。
"""
from __future__ import annotations

from typing import Any, Callable

# transport: (verb, params) -> response dict
Transport = Callable[[str, dict[str, Any]], Any]


class AgentRunClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def profiles(self) -> Any:
        return self._transport("profiles", {})

    def doctor(self) -> Any:
        return self._transport("doctor", {})

    def run_task(self, **kwargs: Any) -> Any:
        return self._transport("run_task", kwargs)

    def start_session(self, **kwargs: Any) -> Any:
        return self._transport("start_session", kwargs)

    def task_status(self, run_id: str, **kwargs: Any) -> Any:
        return self._transport("task_status", {"run_id": run_id, **kwargs})

    def status(self, run_id: str, **kwargs: Any) -> Any:
        return self._transport("status", {"run_id": run_id, **kwargs})

    def logs(self, run_id: str, **kwargs: Any) -> Any:
        return self._transport("logs", {"run_id": run_id, **kwargs})

    def send(self, run_id: str, text: str, **kwargs: Any) -> Any:
        return self._transport("send", {"run_id": run_id, "text": text, **kwargs})

    def interrupt(self, run_id: str, **kwargs: Any) -> Any:
        return self._transport("interrupt", {"run_id": run_id, **kwargs})

    def stop(self, run_id: str, **kwargs: Any) -> Any:
        return self._transport("stop", {"run_id": run_id, **kwargs})

    def cancel(self, run_id: str, **kwargs: Any) -> Any:
        return self._transport("cancel", {"run_id": run_id, **kwargs})

    def prune(self, dry_run: bool = True) -> Any:
        return self._transport("prune", {"dry_run": dry_run})


def local_transport(service: Any) -> Transport:
    """本机回环 transport:把 client 动词直接打到一个 RuntimeService(测试/同机用)。

    真实远端由上层应用实现 HTTP/进程 transport,把请求送达其暴露的服务。
    """

    def call(verb: str, params: dict[str, Any]) -> Any:
        return getattr(service, verb)(**params)

    return call
