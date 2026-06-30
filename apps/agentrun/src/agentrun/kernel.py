"""AgentRuntime 门面:按 config 装配组件(见 design/01 库优先 / design/08)。"""
from __future__ import annotations

from pathlib import Path

from agentrun.core.config import ConfigManager
from agentrun.eventbus import EventBus
from agentrun.service import RuntimeService


class AgentRuntime:
    """进程内库入口。

    AgentRuntime() 默认读取项目级 config/agentrun;
    AgentRuntime(conf_dir=...) 用调用方配置覆盖。
    """

    def __init__(self, conf_dir: str | Path | None = None, runs_dir: str | Path | None = None) -> None:
        self.config = ConfigManager(conf_dir=conf_dir, runs_dir=runs_dir)
        self.events = EventBus()
        self.service = RuntimeService(self.config)

    # 直通常用动词,保持窄接口
    def profiles(self):
        return self.service.profiles()

    def doctor(self):
        return self.service.doctor()

    def config_choices(self, **kwargs):
        return self.service.config_choices(**kwargs)

    def validate_config(self, **kwargs):
        return self.service.validate_config(**kwargs)

    def run_task(self, **kwargs):
        return self.service.run_task(**kwargs)

    def start_session(self, **kwargs):
        return self.service.start_session(**kwargs)

    def task_status(self, run_id: str, **kwargs):
        return self.service.task_status(run_id, **kwargs)

    def status(self, run_id: str, **kwargs):
        return self.service.status(run_id, **kwargs)

    def logs(self, run_id: str, **kwargs):
        return self.service.logs(run_id, **kwargs)

    def send(self, run_id: str, text: str, **kwargs):
        return self.service.send(run_id, text, **kwargs)

    def interrupt(self, run_id: str, **kwargs):
        return self.service.interrupt(run_id, **kwargs)

    def stop(self, run_id: str, **kwargs):
        return self.service.stop(run_id, **kwargs)

    def cancel(self, run_id: str, **kwargs):
        return self.service.cancel(run_id, **kwargs)

    def prune(self, dry_run: bool = True):
        return self.service.prune(dry_run=dry_run)
