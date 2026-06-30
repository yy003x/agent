# Scheduler

本地定时任务应用。真实代码放在 `src/agent_scheduler/`，任务配置放在 `conf/jobs.json`。

- `src/agent_scheduler/`：APScheduler 启动与任务执行。
- `conf/jobs.json`：可提交的本地任务声明。
- `bin/scheduler.py`：薄启动入口。
- `tests/`：scheduler 专属测试。
