# 个人 Agent 工作台兼容入口

`apps/workbench/` 已不再承载主实现，只保留旧启动命令的兼容壳。

## 新边界

- 后端 API：`apps/api/`
- 前端 Web：`apps/web/`
- Runtime 包：`runtime/`

## 启动

后端：

```bash
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8765
```

前端开发服务：

```bash
cd apps/web
npm run dev
```

旧命令仍可启动新的 FastAPI 服务：

```bash
python apps/workbench/server.py 8765
```

## 兼容说明

- `/api/*` 路径保持兼容。
- 运行目录仍使用 `runs/workbench/`、`runs/shared-runtime/` 和 shared runtime 自身目录。
- 旧静态页面和旧本地 HTTP server 已移除；后续只维护 `apps/api` 与 `apps/web`。
