# Workbench API

FastAPI 服务入口。真实代码放在 `src/agent_workbench_api/`，由 `scripts/workbench_service.py` 以 `agent_workbench_api.main:app` 启动。

- `src/agent_workbench_api/`：API 路由、schema、服务层。
- `conf/`：API 专属非敏感配置说明；当前运行偏好写入 `runs/workbench/config.json`。
- `tests/`：API 层测试。
