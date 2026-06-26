# FastAPI + TypeScript 工作台重构设计

## 1. 背景

个人 Agent 工作台已经从早期 `apps/workbench/` 单体壳升级为前后端分离结构：

- 后端由 Python `FastAPI` 提供 API、运行态服务和静态产物托管。
- 前端由 `React + TypeScript + Vite` 实现运营工作台界面。
- 智能 runtime 独立为项目级 `runtime/` 包，统一承载 `MainRuntime`、Planner、Executor、Observer、State、Skill Registry、Task Store 和 External CLI Worker provider。
- 旧 `apps/workbench/server.py` 只保留兼容启动入口，不再承载主业务实现。

本重构的目标不是改变图书运营业务流程，而是把 UI、API、runtime 三个边界拆清楚，方便后续扩展素材库、任务进度、产出预览和 provider 配置。

## 2. 目标

- `apps/api/` 成为工作台 HTTP API 的唯一主入口。
- `apps/web/` 成为工作台前端的唯一主实现。
- `runtime/` 成为工作台智能运行时和 provider 的唯一主实现。
- `apps/workbench/` 降级为兼容层，避免旧启动命令直接失效。
- 保持现有 API 路径兼容，尤其是 `/api/chat/*`、`/api/runtime/tmux/*`、`/api/skills`、`/api/health`。
- 工作台 UI 面向运营视角：会话、当前进度、素材、产出、设置和诊断分层展示。

## 3. 非目标

- 不把 GUI runtime 改成 `codex exec` 或 `claude -p`。
- 不要求 LLM API key；API backend 仍是未来可选能力。
- 不改内容生成、知识库入库、发布包生成的业务规则。
- 不自动发布到小红书、朋友圈或社群。
- 不在本轮清理 `runs/`、`workspace/`、`outputs/` 运行数据。

## 4. 目录边界

```text
apps/
  api/
    main.py                 # FastAPI app 和 API route
    schemas.py              # Pydantic request model
    health.py               # 环境、依赖、runtime 健康检查
    file_browser.py         # outputs / workspace / design / rules 文件读取与打开
    services/
      workbench.py          # session、chat、operator、runtime run、draft preview 服务
  web/
    index.html
    package.json
    src/
      App.tsx               # 工作台主界面
      api/client.ts         # fetch client
      types.ts              # 前端 DTO 类型
      styles.css
  workbench/
    server.py               # 兼容启动壳，转发到 apps.api.main
    README.md

runtime/
  main.py                   # MainRuntime
  planner.py
  executor.py
  observer.py
  state.py
  skill_registry.py
  task_store.py
  external_cli.py           # shared runtime provider spec 翻译
  shared_runtime.py         # /Users/yang/agents/runtime 适配
  model_backends.py         # 本地可选 LLM API 配置只读发现
```

`apps/api` 可以调用 `runtime.MainRuntime`；`apps/web` 只能通过 HTTP API 访问后端；`runtime` 不依赖 `apps/web`。

## 5. API 兼容层

后端继续保留已有 URL，降低前端和脚本迁移成本：

```text
GET    /api/health
GET    /api/state
GET    /api/config/runtime
POST   /api/config/runtime
GET    /api/model-backends
GET    /api/skills
GET    /api/outputs
GET    /api/files
POST   /api/files/open
GET    /api/kb/search

POST   /api/chat/sessions
GET    /api/chat/sessions/{session_id}
DELETE /api/chat/sessions/{session_id}
POST   /api/chat/sessions/delete
POST   /api/chat/sessions/{session_id}/messages
GET    /api/chat/sessions/{session_id}/operator
GET    /api/chat/sessions/{session_id}/runtime/status
GET    /api/chat/sessions/{session_id}/runtime/logs
POST   /api/chat/sessions/{session_id}/runtime/stop

GET    /api/runtime/tmux/runs
POST   /api/runtime/tmux/runs
GET    /api/runtime/tmux/runs/{run_id}
GET    /api/runtime/tmux/runs/{run_id}/logs
POST   /api/runtime/tmux/runs/{run_id}/send
POST   /api/runtime/tmux/runs/{run_id}/stop
```

`/api/runtime/tmux/*` 这个路径名保留兼容，但内部实现不应直接散写 tmux 命令，必须经 `runtime.MainRuntime`。

## 6. 前端设计

前端是运营工作台，不是 runtime debug console。默认信息架构：

- 左侧：会话列表、批量选择、创建/删除、当前 provider。
- 中间：聊天输入与任务状态。用户消息发送后不重复插入本地假消息，等待服务状态刷新。
- 右侧：任务进度、素材库、产出、Provider 设置、系统诊断。

输入约定：

- `Enter` 发送。
- `Shift+Enter` 换行。
- provider 默认 `codex_cli`，`claude_cli` 通过配置启用。
- 隐藏 fake/canary provider，不作为运营用户选项展示。

## 7. Runtime 边界

`runtime/` 是智能执行层：

```text
MainRuntime
├── Planner
├── Executor
│   └── External CLI Worker
├── Observer
│   └── FileResultObserver
├── State
├── Skill Registry
└── Task Store
```

External CLI Worker provider 由 shared runtime 承接，GUI 主路径仍通过 tmux 托管真实 `codex` / `claude` 交互会话。服务层只提交 task、读取状态和日志，不直接拼 tmux 命令。

## 8. 启动方式

生产式本地启动：

```bash
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8765
```

旧命令兼容：

```bash
python apps/workbench/server.py 8765
```

前端开发：

```bash
cd apps/web
npm install
npm run dev
```

Vite dev server 将 `/api` 代理到 `http://127.0.0.1:8765`。

## 9. 验证

基础验证：

```bash
python3 -m py_compile runtime/*.py
python3 -m py_compile apps/api/*.py apps/api/services/*.py apps/workbench/server.py
python3 -c 'from apps.api.main import app; assert app.title'
python3 apps/workbench/server.py --help
cd apps/web && npm run typecheck && npm run build
git diff --check
bash scripts/validate.sh --quick
```

API 冒烟：

```bash
curl -sS http://127.0.0.1:8765/api/health
curl -sS http://127.0.0.1:8765/api/state
curl -sS http://127.0.0.1:8765/api/config/runtime
```

## 10. 迁移策略

- 第一阶段：保留旧 API 路径，拆出 FastAPI 和 React 主实现。
- 第二阶段：把 `apps/api/services/workbench.py` 中兼容旧标准库 handler 的代码收敛为纯 service。
- 第三阶段：为 API 生成 OpenAPI types，前端从生成类型而不是手写 DTO 读取契约。
- 第四阶段：按运营视角继续削弱诊断信息默认可见性，把高级诊断放入二级入口。
