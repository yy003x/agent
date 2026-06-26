# 个人 Agent 工作台兼容入口

`apps/workbench/` 已不再承载主实现，只保留旧启动命令的兼容壳。

## 新边界

- 后端 API：`apps/api/`
- 前端 Web：`apps/web/`
- Runtime 包：`runtime/`

## 启动

推荐使用项目根目录的 Makefile 统一管理前后端：

```bash
make start
make status
make logs
make stop
```

默认端口：

```text
API: http://127.0.0.1:8765
Web: http://127.0.0.1:5173
```

旧命令仍可启动新的 FastAPI 服务：

```bash
python apps/workbench/server.py 8765
```

旧命令是前台兼容入口；常驻运行时使用 `make` 或 `scripts/workbench_service.py`，支持 `list` / `status` / `logs` / `stop` / `restart`。

## 兼容说明

- `/api/*` 路径保持兼容。
- 运行目录仍使用 `runs/workbench/`、`runs/shared-runtime/` 和 shared runtime 自身目录。
- 旧静态页面和旧本地 HTTP server 已移除；后续只维护 `apps/api` 与 `apps/web`。
