# 图形化 Agent 工作台设计

本文定义「学而思图书运营 Agent」的工作台目标态：围绕图书运营日常处理，用本地 Web 界面承载聊天、内容生成、素材检索、成品预览、知识库整理，以及 `codex` / `claude` CLI 的 tmux 会话控制。

---

## 1. 目标

构建一个本地运行的图书运营工作台，让用户可以通过 GUI 或 CLI 两种方式使用 Agent：

```text
浏览器工作台
  -> Python 服务
    -> 当前 content runtime / finalize / scheduler
    -> tmux CLI runtime（codex_cli 默认 / claude_cli 配置）
    -> 未来可选 LLM API runtime
    -> outputs / workspace / runs 展示
```

用户在工作台里可以完成：

- 聊天式输入图书运营需求。
- 整理大量产品资料、文档、图片和视频，并纳入本地知识库。
- 查看路由分类、素材检索、候选素材、文案草稿、组装计划和成品包。
- 对关键节点做确认、修改、继续、停止。
- 预览 `outputs/` 中的小红书 / 朋友圈 / 家长群成品包。
- 管理 `codex` / `claude` CLI 终端 Agent 会话：启动、查看日志、发送补充指令、attach、停止。
- 查看当前 Agent 的 rules、skills、memory、KB、scheduler、session 记录和健康状态。

---

## 2. 已确认决策

1. UI 形态使用**本地 Web UI**，不使用 Tkinter 作为第一版主路径。
2. 图书运营内容生成主链路由 **Python 后端控制流程**：确定性步骤直连 Python，智能步骤通过 tmux 托管的 CLI runtime 完成。
3. 工作台有两种使用方式：GUI 通过 tmux 管理真实 `codex` / `claude` CLI 会话；CLI 高级入口可直接打开 `codex` / `claude`。工作台托管 runtime 默认 `codex_cli`，`claude_cli` 通过配置启用。
4. 设计耗 token、耗时较长、一次性内容生成或素材整理任务都走 tmux CLI runtime；不使用 `codex exec` 或 `claude -p` 作为 GUI runtime。LLM API backend 仅作为未来可选扩展。
5. 第一版只做可用工作台：聊天、素材检索、文案生成、成品包展示、tmux 会话日志与控制，不先做复杂权限、多用户或远端发布。

---

## 3. 非目标

第一版不做：

- 不自动发小红书、朋友圈或群消息。
- 不把工作台做成公网服务。
- 不引入完整 `mozi-agent-base` runtime / pydantic-ai provider 层。
- 不重写 `content_runtime.py` 的 KB、文案、媒体组装能力。
- 不在 `workspace/` 镜像设计正文；`design/` 是本设计第一事实源。
- 不把 token、API key、cookie、private key 或完整 JWT 展示到 UI、日志或成品包。

---

## 4. 总体架构

```text
┌──────────────────────────────── 浏览器工作台 ────────────────────────────────┐
│                                                                              │
│  Chat 面板       素材 / KB       文案 / Plan       Outputs       Runtime      │
│  - 对话输入      - 搜索          - 草稿预览        - 成品包      - CLI/API     │
│  - 确认按钮      - 候选选择      - 修改意见        - 图片视频    - logs/send   │
│  - 状态流        - 文件预览      - 组装确认        - checklist   - logs/send   │
│                                                                              │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ HTTP + WebSocket / SSE
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                              Python Workbench Service                         │
│                                                                              │
│  API Layer                                                                    │
│  - /api/chat                                                                  │
│  - /api/runs                                                                  │
│  - /api/files                                                                 │
│  - /api/runtime/tmux                                                          │
│  - /api/health                                                                │
│                                                                              │
│  Agent Application Layer                                                      │
│  - ChatSessionStore                                                           │
│  - ContentWorkflowController                                                  │
│  - AssetBrowser                                                               │
│  - OutputBrowser                                                              │
│  - HealthDoctor                                                               │
│                                                                              │
│  Runtime Layer                                                                │
│  - Direct Python adapter: content_runtime / finalize / scheduler              │
│  - TmuxCliRuntime: real codex / claude interactive CLI sessions               │
│  - OfflineTemplateRuntime: deterministic fallback                             │
│  - LlmApiRuntime: future optional provider API tasks                          │
│                                                                              │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                               Project Local State                             │
│                                                                              │
│  rules/           skills/          memory/summary.md                          │
│  workspace/kb/    workspace/daily/ workspace/resume/                          │
│  outputs/         runs/workbench/ runs/tmux/ runs/scheduler/               │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 当前可复用资产

| 资产 | 当前职责 | 工作台使用方式 |
|---|---|---|
| `apps/agent/orchestrator.py` | 纯 Python 主循环，已串起路由、content-generate、finalize | 拆出无终端依赖的 workflow controller |
| `apps/agent/brain.py` | 当前实现为 legacy `codex exec` 直连认知层和离线降级 | 设计目标是替换为 `MainRuntime`：`ExternalCliWorkerRuntime(codex_cli 默认 / claude_cli 配置)` / `OfflineTemplateRuntime`，未来可选 `LlmApiRuntime` |
| `skills/content-generate/scripts/content_runtime.py` | KB、文案草稿、plan、媒体组装、发布包 | 作为 Python service 的核心工具层 |
| `scripts/finalize.py` | session 记录、hook、snapshot | 工作台完成实质任务后调用 |
| `apps/scheduler/scheduler.py` | 定时任务入口 | 工作台显示 jobs、运行日志和启停建议 |
| `rules/` / `skills/` / `memory/` | Agent 行为事实源 | 工作台只读展示和文件预览 |
| `outputs/` | 草稿和成品包 | 工作台产出中心 |
| `runs/` | 运行日志 | 工作台运行面板 |

---

## 6. UI 信息架构

### 6.1 顶部导航

- `聊天`
- `内容流水线`
- `素材库`
- `产出`
- `Runtime`
- `系统`

第一版可以合并为左右两栏：

```text
左侧：会话列表 / 当前任务状态 / Runtime 状态
中间：聊天 + 确认控件
右侧：素材候选 / 文案预览 / 成品包 / 日志
```

### 6.2 聊天面板

必须支持：

- 文本输入。
- 多轮消息历史。
- 任务状态流：`routing`、`searching`、`waiting_confirmation`、`drafting`、`assembling`、`done`、`failed`。
- 人工确认控件：
  - 选择素材。
  - 确认需求解析。
  - 输入文案修改意见。
  - 确认组装。
  - 停止任务。
- 显示哪些步骤调用了 Python 工具，哪些步骤调用了 tmux CLI runtime；LLM API backend 若未来启用也必须单独标识。

### 6.3 内容流水线

围绕 `content-generate` 的 10 步状态机展示：

1. 需求解析。
2. KB 检索。
3. 候选素材选择。
4. 回读事实源。
5. 文案草稿和润色。
6. `plan.json`。
7. 媒体组装。
8. 发布包。
9. 成品预览。
10. finalize session。

每一步都要展示：

- 当前状态。
- 输入。
- 输出路径。
- 失败原因。
- 可执行操作。

### 6.4 素材库

功能：

- 搜索 `workspace/kb/lance`。
- 展示 `source_path`、标题、modality、caption、tags、score。
- 预览文档文本、图片、视频元信息。
- 发起 ingest：
  - 选择源目录。
  - modality：`auto` / `doc` / `image` / `video`。
  - limit。
  - 是否 `--resume`。
- 明确提示图片 / 视频 caption 可走 tmux CLI runtime（`codex_cli` / `claude_cli`）；当前 runtime 不支持媒体理解时，允许手工 caption 或文件名/目录标签降级。

### 6.5 产出中心

按日期展示：

```text
outputs/YYYY-MM-DD/
  content/
  research/
  design/
```

支持：

- 打开 `draft.json`、`plan.json`、`sources.json`。
- 预览 `publish-checklist.md`。
- 预览图片。
- 展示视频 clip 路径。
- 复制标题、正文、标签。
- 显示“仅供手动发布”提示。

### 6.6 Runtime 面板

管理 tmux CLI runtime 会话：

- 会话列表：
  - runtime type：`codex_cli` / `claude_cli`。
  - run_id。
  - tmux session / window / pane。
  - state：`starting` / `idle` / `running` / `waiting_result` / `done` / `failed` / `stopped`。
  - result_file 是否存在。
  - output bytes、last_output_at、idle_seconds。
- 操作：
  - start。
  - send。
  - attach。
  - stop。
  - close。
  - view logs。
  - view prompt/result/meta/status。

---

## 7. Python 服务设计

当前实现已进一步拆为 `apps/api/`、`apps/web/` 和项目级 `runtime/`；以下早期目录草案保留为模块职责参考。

建议新增目录：

```text
apps/workbench/
  server.py              # HTTP / WebSocket 服务入口
  app_state.py           # 会话、任务、runtime 状态
  content_controller.py  # 图书运营 workflow controller
  file_browser.py        # outputs / workspace / design / rules 文件预览
  health.py              # 环境检查
  runtime/
    main.py              # MainRuntime：UI / health 的唯一 runtime 入口
    planner.py           # Planner：生成 runtime task contract
    executor.py          # Executor / TmuxCodexWorker
    observer.py          # FileResultObserver：result file 完成信号
    state.py             # runtime state / error / typed task
    skill_registry.py    # runtime 可见能力注册
    task_store.py        # task/run 目录边界
    external_cli.py      # External CLI Worker Runtime 配置翻译
    tmux_provider.py     # runtime 内部 provider：tmux spec/start/status/logs/send/stop
  schemas.py             # API request / response model
  static/
    index.html
    app.js
    styles.css
```

依赖策略：

- 第一版优先使用 `FastAPI + uvicorn`。
- 如果要保持依赖更少，可以用标准库 HTTP 服务，但 WebSocket / SSE 和静态资源会更繁琐。
- 不使用 Tkinter 作为第一版主路径；`python-tk@3.14` 可作为备用方案，不影响 Web UI。

### 7.1 API 草案

```text
GET  /                         -> 工作台页面
GET  /api/health               -> 环境与依赖状态
GET  /api/state                -> 当前工作台全局状态

POST /api/chat/sessions        -> 创建聊天 session
GET  /api/chat/sessions        -> 列表
GET  /api/chat/sessions/{id}   -> 详情
POST /api/chat/sessions/{id}/messages
WS   /api/chat/sessions/{id}/events

GET  /api/kb/search
POST /api/kb/ingest

GET  /api/outputs
GET  /api/files?path=...

POST /api/runtime/tmux/runs
GET  /api/runtime/tmux/runs
GET  /api/runtime/tmux/runs/{run_id}
GET  /api/runtime/tmux/runs/{run_id}/logs
POST /api/runtime/tmux/runs/{run_id}/send
POST /api/runtime/tmux/runs/{run_id}/stop
POST /api/runtime/tmux/runs/{run_id}/attach
```

### 7.2 事件模型

工作台后端对 UI 推送结构化事件：

```json
{
  "event_id": "evt-...",
  "session_id": "chat-...",
  "run_id": "run-...",
  "type": "workflow.step",
  "status": "running",
  "title": "KB 检索",
  "message": "正在检索数学思维相关素材",
  "data": {
    "step": "kb_search",
    "query": "数学思维",
    "topk": 10
  },
  "ts": "2026-06-22T..."
}
```

关键事件类型：

- `chat.message`
- `route.classified`
- `workflow.step`
- `workflow.waiting_confirmation`
- `workflow.output_ready`
- `runtime.started`
- `runtime.status`
- `runtime.log`
- `runtime.result_ready`
- `error`

---

## 8. 内容 workflow 改造

当前 `orchestrator.py` 的 `say()`、`ask()`、`confirm()` 是终端交互原语。工作台需要把它们抽象为事件式 IO。

智能步骤统一走 `MainRuntime`。`MainRuntime` 不是单一 API SDK 封装，而是智能 runtime 选择层；第一版工作台只托管 tmux 真实 CLI 会话：

```text
MainRuntime
  ├─ ExternalCliWorkerRuntime
  │   ├─ codex_cli     # 默认 runtime，启动交互式 codex，不使用 codex exec
  │   └─ claude_cli    # 可配置 runtime，启动交互式 claude，不使用 claude -p
  ├─ LlmApiRuntime     # 未来可选扩展，不是第一版硬依赖
  └─ OfflineTemplateRuntime
```

`MainRuntime` 至少覆盖这些窄任务：

- 输入分类。
- 内容需求抽取。
- 文案润色 / 改写。
- 问答 / 选题讨论。
- 运营合规自查。
- 图片 / 视频 caption（当前 runtime 不支持媒体理解时，要求手工 caption 或降级为文件名/目录标签）。

### 8.1 抽象目标

把：

```python
say("候选素材...")
sel = ask("选择使用哪些素材")
if confirm("确认组装？"):
    ...
```

改为：

```python
ui.emit("workflow.step", ...)
sel = ui.wait_input("select_assets", options=...)
if ui.wait_confirm("confirm_assemble", plan=plan):
    ...
```

### 8.2 Controller 接口草案

```python
class WorkflowIO:
    def emit(self, event_type: str, **payload): ...
    def request_input(self, request_type: str, **payload) -> str: ...
    def request_confirm(self, request_type: str, **payload) -> bool: ...


class ContentWorkflowController:
    def run(self, session_id: str, user_input: str, io: WorkflowIO) -> WorkflowResult:
        ...
```

CLI 仍可复用同一 controller：

- `TerminalWorkflowIO`：映射到 `print/input`。
- `WebWorkflowIO`：映射到 WebSocket 事件和用户操作。

这样避免维护两套内容生成状态机。

---

## 9. tmux runtime 设计

### 9.0 Runtime 边界

```text
MainRuntime
├── Planner
├── Executor
│   └── TmuxCodexWorker
├── Observer
│   └── FileResultObserver
├── State
├── Skill Registry
└── Task Store

External CLI Worker Runtime
└── runtime/
    ├── external_cli.py
    └── tmux_provider.py
```

`provider` 是 External CLI Worker Runtime 的内部实现，不属于 UI 层。`server.py`、`health.py` 只能通过 `MainRuntime` 调 runtime；不能直接散写 tmux 命令，也不能直接依赖 `TmuxProvider`。

`Skill Registry` 由 `MainRuntime` 托管，扫描项目根 `skills/*/SKILL.md` 并在系统面板展示。第一版只读展示 skill 名称、类别、触发摘要、步骤能力、脚本路径；不在 UI 上直接执行 skill，避免绕过确认和写操作门禁。

命名边界：当前实现中 `apps/api/` 是 Python service 边界，`apps/web/` 是前端边界，项目级 `runtime/` 是 runtime、provider、skill registry 边界。`apps/workbench/` 只保留兼容启动入口。

### 9.1 原则

- tmux 托管 CLI 智能后端：`codex_cli` 是默认 runtime，`claude_cli` 通过配置启用；custom command 只作为受控扩展。
- 启动的是 `codex` / `claude` 交互式 CLI 真会话，不使用 `codex exec`、`claude -p` 或其它 print/one-shot 模式。
- prompt 不通过 shell 参数传入；provider 先通过 `output.log` 的 idle/output-rate detector 等 TUI 就绪，再用 tmux buffer 粘贴文本，并用 `C-m` 自动提交，避免 shell quoting 改写用户输入。
- tmux 操作通过 runtime 内部 provider 边界实现：`TmuxRunSpec` 描述 command/cwd/prompt/result/session，`TmuxProvider` 暴露 `start/status/logs/send/stop/close`；上层只通过 `MainRuntime` 调用，不直接依赖 provider。
- 不用屏幕内容作为完成判断。
- 完成信号必须是 result file 稳定写入。
- 每个 run 都有固定 run directory，所有 prompt、日志、结果和状态可审计。
- UI 可以 send 文本给 pane，但必须记录到 run events。

### 9.2 运行目录

```text
runs/tmux/<run_id>/
  prompt.md
  result.json
  output.log
  status.json
  meta.json
  command.sh
  events.jsonl
```

聊天 session 会额外在 `runs/workbench/sessions/<session_id>/runtime/provider/<provider_run_id>/` 保存 provider run 的 `meta.json/status.json/command.sh/output.log`。

### 9.3 RunSpec

```json
{
  "runtime": "codex_cli",
  "command": "codex",
  "cwd": "/Users/yang/agents/agent",
  "tmux_session_name": "book-agent-workbench",
  "prompt_delivery": "tmux_buffer_paste",
  "submit_key": "C-m",
  "result_file_name": "result.json",
  "timeout_seconds": 1800,
  "env": {
    "AGENT_WORKBENCH_RUN_ID": "{run_id}",
    "AGENT_WORKBENCH_RESULT_FILE": "{result_file}"
  }
}
```

### 9.4 Prompt 契约

所有发给 CLI runtime 的任务都要包含：

```markdown
# 任务

...

## 输出要求

请把最终结果写入：
`{result_file}`

写入格式：

```json
{
  "status": "success|partial|failed",
  "summary": "...",
  "outputs": [],
  "questions": [],
  "errors": []
}
```

只有 result file 稳定写入后，工作台才把 run 标记为 done。
```

### 9.5 Runtime 操作

| 操作 | 行为 |
|---|---|
| start | 创建 tmux session/window/pane，写 prompt/meta/status，启动 command |
| status | 读取 pane 是否存活、output.log 尺寸、idle_seconds、result_file |
| logs | 读取 `output.log` tail |
| send | 向 pane 发送文字并记录事件 |
| attach | 打开本机终端 attach 到 tmux session |
| stop | kill 记录的 pane，不杀整个 tmux session |
| close | 完成后关闭记录的 pane |

---

## 10. 数据与状态

### 10.1 Workbench run store

建议新增：

```text
runs/workbench/
  state.sqlite              # 可选：第二阶段再引入
  sessions/
    chat-<id>/
      messages.jsonl
      events.jsonl
      state.json
      linked_outputs.json
```

第一版可以只用文件：

- `messages.jsonl`：聊天消息。
- `events.jsonl`：步骤、确认、runtime 状态。
- `state.json`：当前 session 状态。
- `linked_outputs.json`：关联 `outputs/` 路径和 tmux run_id。

### 10.2 状态机

```text
created
  -> routing
  -> running
  -> waiting_user
  -> running
  -> done

失败/中止分支：
  running -> failed
  waiting_user -> cancelled
  running -> stopped
```

`done` 条件：

- 内容 workflow：成品包写入完成，或只读任务完成回答。
- tmux runtime：`result_file` 稳定写入，且 JSON 可解析。
- 需要 finalize 的实质任务：`scripts/finalize.py record` 已执行或失败原因已记录。

---

## 11. 配置

建议新增：

```text
config/workbench.toml
```

草案：

```toml
[server]
host = "127.0.0.1"
port = 8765
open_browser = true

[paths]
runtime_dir = "runs/workbench"
tmux_runtime_dir = "runs/tmux"

[tmux]
session_name = "book-agent-workbench"
default_timeout_seconds = 1800

[runtime]
default_backend = "tmux_cli"
default_cli = "codex_cli"
allow_api_backend = false

[runtime.cli.codex]
enabled = true
command = "codex"
no_alt_screen = true
sandbox = "workspace-write"
approval = "never"
bypass_approvals_and_sandbox = false
extra_args = ""

[runtime.cli.claude]
enabled = false
command = "claude"
permission_mode = "dontAsk"
skip_permissions = false
extra_args = ""

[tmux.submit]
startup_delay_seconds = 1.5 # 兼容保留，主路径使用 detector
submit_delay_seconds = 0.15
submit_key = "C-m"
poll_interval_seconds = 1
silence_threshold_seconds = 0.6
prompt_idle_timeout_seconds = 300
prompt_ready_settle_seconds = 2
prompt_ready_settle_fast_seconds = 0.5
prompt_stable_timeout_seconds = 10

[runtime.api]
enabled = false
provider = ""

[content]
default_platform = "xiaohongshu"
```

敏感值不进入该配置：

- 默认不要求 LLM API key；只有启用 `runtime.api` 时才需要对应 provider 的本地环境变量。
- `codex` / `claude` 登录态由各自 CLI 管理。
- UI 只展示 CLI 是否可执行、tmux 是否可用、runtime canary 是否能写 result file，不展示任何登录态、API key 或敏感值。

---

## 12. 安全边界

- 工作台默认只绑定 `127.0.0.1`。
- 不提供公网监听默认配置。
- 文件预览只能访问项目根下允许目录：
  - `design/`
  - `rules/`
  - `skills/`
  - `memory/`
  - `workspace/`
  - `outputs/`
  - `runs/`
  - `apps/`
  - `scripts/`
- 禁止通过 file API 读取 `.env`、private key、pem、cookie、token 文件。
- 发布动作只生成预览，不调用外部平台 API。
- 批量 ingest、删除、清理、停止 runtime 等高影响操作需要 UI 二次确认。
- tmux `send` 操作要写入 `events.jsonl`，便于追溯。

---

## 13. 实施计划

### P6.1 设计落地与接口拆分

目标：不改变现有 CLI 行为，先让内容状态机可被 Web UI 调用。

任务：

1. 新增 `apps/workbench/` 骨架。
2. 从 `apps/agent/orchestrator.py` 抽出 `ContentWorkflowController`。
3. 保留 `TerminalWorkflowIO`，保证原 REPL 仍能跑。
4. 定义 `schemas.py`：session、event、confirmation、runtime status。

验收：

- `python apps/agent/orchestrator.py "..."` 行为不退化。
- controller 可在测试中用 fake IO 跑到等待确认节点。

### P6.2 Web 服务与基础界面

目标：本地浏览器可以聊天并显示 workflow 事件。

任务：

1. 实现 `server.py`。
2. 实现 `static/index.html`、`app.js`、`styles.css`。
3. 支持 chat session、事件流、确认按钮。
4. 支持 `outputs/` 文件列表和 Markdown/JSON 预览。

验收：

- 启动命令：
  ```bash
  python apps/workbench/server.py
  ```
- 浏览器打开 `http://127.0.0.1:8765`。
- 能完成一次无 tmux 的文本内容生成 dry smoke。

### P6.3 tmux runtime manager

目标：工作台可控 `codex` / `claude` CLI tmux 会话。

任务：

1. 实现 `runtime/main.py`、`runtime/external_cli.py`、`runtime/tmux_provider.py`。
2. 支持 start/status/logs/send/attach/stop。
3. 使用 `runs/tmux/<run_id>/result.json` 作为完成信号。
4. UI Runtime 面板展示 run 状态和日志。

验收：

- 用 fake command 写 result.json 完成测试。
- 能启动 `codex` 或 `claude` CLI 命令并显示 output.log。
- stop 只关闭记录 pane。

### P6.4 工作台完整串联

目标：聊天工作台可完成图书运营 Agent 的主要工作。

任务：

1. 素材检索和候选选择 UI。
2. 文案草稿预览和修改意见 UI。
3. plan / assemble / package 确认 UI。
4. 成品包预览和复制。
5. finalize session 状态展示。

验收：

- 从聊天输入到 `outputs/YYYY-MM-DD/content/<slug>/publish-checklist.md` 完整跑通。
- UI 明确提示人工发布，不自动发布。

### P6.5 系统面板与健康检查

目标：用户能看到当前 Agent 所有关键资产和运行状态。

任务：

1. 展示 `rules/`、`skills/`、`memory/summary.md`。
2. 展示 `workspace/kb/` 状态。
3. 展示 scheduler jobs 和 `runs/scheduler/` 日志。
4. 健康检查：
   - Python 版本。
   - `codex` / `claude` CLI 是否可执行。
   - 已启用的 CLI runtime 是否能通过 tmux fake/canary 任务写入 result file。
   - 未来可选 LLM API backend 是否配置完整（只显示状态，不显示 key）。
   - tmux。
   - ffmpeg。
   - LanceDB。
   - sentence-transformers。
   - jieba。
   - pillow。

验收：

- 健康检查不会打印敏感值。
- 缺失依赖给出明确安装建议。

---

## 14. 验证策略

文档与结构：

```bash
bash scripts/validate.sh --quick
git diff --check
```

后端单元测试建议：

- fake `WorkflowIO` 测内容 workflow 状态。
- fake tmux command：
  ```bash
  sh -c 'echo running; sleep 1; echo "{\"status\":\"success\",\"summary\":\"ok\",\"outputs\":[],\"questions\":[],\"errors\":[]}" > "$AGENT_WORKBENCH_RESULT_FILE"'
  ```
- file browser path traversal 测试。
- health doctor 不泄露 secret 测试。

手工 smoke：

1. 启动服务。
2. 浏览器创建聊天。
3. 输入内容生成请求。
4. 选择素材。
5. 生成文案。
6. 组装并查看成品包。
7. 启动一个 fake tmux runtime，确认日志和 result 展示。

---

## 15. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 直接复用 `orchestrator.py` 会阻塞 Web 请求 | UI 卡死 | 抽成 controller + 后台任务 + 事件流 |
| tmux TUI 输出难判断是否完成 | 误判任务结束 | 只信 result file，不信屏幕输出 |
| CLI 命令、登录态或 API 配置异常 | runtime 不稳定 | command/provider 走配置；健康检查提供 canary 任务；失败时降级到 offline_template |
| 长任务日志过大 | UI 卡顿 | logs API 默认 tail，状态只采样指标 |
| 工作台误读敏感文件 | 泄露风险 | allowlist 目录 + denylist 文件名/扩展名 |
| 多会话同时写同一输出目录 | 覆盖产物 | 输出目录带 session/run id 或冲突时加后缀 |
| 依赖过重 | 启动门槛高 | Web UI 依赖单独列出，content runtime 依赖延迟加载 |

---

## 16. 第一版完成定义

满足以下条件视为第一版完成：

- `python apps/workbench/server.py` 可启动本地 Web UI。
- 聊天页可输入需求并看到结构化事件流。
- 内容生成主链路可从 UI 跑到成品包。
- `outputs/` 成品可在 UI 预览。
- `codex` / `claude` CLI runtime 可通过 tmux start/status/logs/send/stop。
- Runtime 完成以 `result.json` 稳定写入为准。
- 健康检查覆盖 CLI runtime、可选 API backend、tmux、ffmpeg、KB 依赖。
- 不自动发布，不泄露敏感值。
- `bash scripts/validate.sh --quick` 通过。
