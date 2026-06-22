# 个人 Agent 工作台

本目录是本地工作台服务，包含 Python service、External CLI Worker Runtime、Skill Registry 和静态 Web UI。当前实现不引入 FastAPI/uvicorn，直接使用 Python 标准库 HTTP server。

## 启动

```bash
python apps/workbench/server.py 8765
```

打开：

```text
http://127.0.0.1:8765
```

## 当前已实现

- 本地 Web 页面：聊天、进度、素材、产出、设置、诊断面板。默认界面面向运营任务；raw event、runtime log、pane、bytes、result file、skill/health 明细放到诊断高级模式。
- 健康检查：Python、tmux、`codex`、`claude`、ffmpeg、KB 依赖和关键目录。
- 会话：创建聊天 session、发送消息、记录 `runs/workbench/sessions/*`；会话列表读取每个 `chat-*` 目录下的 `state.json`，不是读取 Codex / Claude 原生聊天历史；每个聊天 session 绑定一个 tmux 中的真实 `codex` / `claude` 交互会话。
- 会话删除：UI 的删除按钮调用 `DELETE /api/chat/sessions/<session_id>`，批量删除调用 `POST /api/chat/sessions/delete`；删除前会先尝试停止该 session 绑定的 tmux/provider pane，然后物理删除 `runs/workbench/sessions/<session_id>` 整个目录。
- 聊天输入：`Enter` 发送，`Shift+Enter` 换行；发送后先写入 UI 会话，tmux 投递在后台执行，避免等待 Codex/Claude TUI ready 时阻塞聊天区显示。
- tmux 聊天协议：启动 Codex/Claude 会话时只发送一次 runtime contract；后续每轮只把用户原文粘贴到 tmux。完整控制信息写入 `current_turn.json` 和 `turns/<turn_id>/prompt.md`，不再每轮把长 `WORKBENCH CONTROL` 文本粘贴进 CLI。
- 长任务可见性：如果执行 10 分钟仍未写入 `result.json`，聊天消息会保持 pending；进度页会展示运营可读状态、当前步骤和最近活动，诊断高级模式保留当前会话 runtime 状态和 `output.log` 日志尾部。
- 助手设置：设置面板默认展示聊天助手、长任务助手、执行模式、项目目录和 Codex/Claude/tmux 可用状态；Codex/Claude 命令和常用参数折叠在高级启动参数中。配置写入 `runs/workbench/config.json`，新会话和 Runtime 面板启动会使用该配置。
- 聊天 runtime：默认 `codex_cli`，可通过 UI 配置切到 `claude_cli`；发送消息时会写入 `turns/<turn_id>/raw_user_message.txt`、`prompt.md`、`sent_to_tmux.txt`。External CLI Worker Runtime 会先通过 `output.log` 的 idle/output-rate detector 等 TUI 就绪，再用 tmux buffer 原样粘贴并自动发送回车。
- KB 搜索包装：调用 `content_runtime.py kb search --json --no-log --no-touch`。
- 文件预览：按 allowlist 读取 `outputs/`、`workspace/`、`design/` 等目录，拒绝敏感文件名和 key/cookie/token 类路径。
- tmux runtime：支持 `codex_cli` / `claude_cli` run；`codex_cli` / `claude_cli` 都启动交互式 CLI 真会话，不走 `codex exec` 或 `claude -p`，完成信号为 `result.json`。`fake` 仅保留为后端自动化测试 provider，不在 UI 下拉框展示。
- External CLI Worker Runtime：实现位于 `runtime/` 包。`MainRuntime` 是 UI 唯一入口，内部包含 `Planner`、`Executor/TmuxCodexWorker`、`Observer/FileResultObserver`、`State`、`SkillRegistry`、`TaskStore`；`tmux_provider.py` 属于 runtime 内部 provider，不再是 UI 层依赖。
- Skill Registry：`MainRuntime` 会扫描项目根 `skills/*/SKILL.md`，通过 `/api/skills` 和 `/api/state` 暴露给诊断高级模式；当前只读展示，不在 UI 直接执行 skill。当前可见能力包括 workbench 主链路、图书运营业务闭环和内容生产能力：`workbench-chat`、`knowledge-search`、`workbench-research`、`workbench-design`、`workbench-execute`、`workbench-finalizer`、`agent-learn`、`agent-skill-create`、`book-asset`、`knowledge-sync`、`book-profile`、`book-campaign`、`content-package`、`content-compliance-review`、`book-media`、`workbench-session-ops` 和 `content-generate`。
- Operator view：`GET /api/chat/sessions/<session_id>/operator` 会把 session state、pending turns、runtime status、events 和 outputs 聚合成运营视角的进度卡；普通 UI 不直接消费 raw runtime 字段。

## 命名边界

`apps/workbench/` 是工作台应用边界。其中 `static/` 是前端界面，`runtime/` 是 External CLI Worker Runtime 和 provider 边界，`server.py` 是本地 Python service 入口。

## Runtime 架构

```text
MainRuntime
├── Planner
├── Executor
│   └── TmuxCodexWorker
├── Observer
│   └── FileResultObserver
├── State
├── SkillRegistry
└── TaskStore

External CLI Worker Runtime
└── runtime/
    ├── main.py
    ├── planner.py
    ├── executor.py
    ├── observer.py
    ├── state.py
    ├── skill_registry.py
    ├── task_store.py
    ├── external_cli.py
    └── tmux_provider.py
```

边界约定：

- `server.py` / `health.py` 只调用 `MainRuntime`。
- `external_cli.py` 负责把工作台 runtime 配置翻译成 CLI worker spec。
- `tmux_provider.py` 只处理 tmux session/window/pane、buffer paste、日志、状态和 result-file contract。
- `FileResultObserver` 只信 `result.json`，不把屏幕输出当完成信号。

## Runtime 配置

| 变量 | 默认 | 用途 |
|---|---|---|
| `AGENT_WORKBENCH_CHAT_RUNTIME` | `codex_cli` | GUI 聊天默认 runtime 初始值，可在 UI 保存到 `runs/workbench/config.json` |
| `AGENT_WORKBENCH_DEFAULT_RUNTIME` | `codex_cli` | Runtime 面板默认 CLI runtime 初始值，可在 UI 覆盖 |
| `AGENT_WORKBENCH_CODEX_SANDBOX` | `workspace-write` | 传给交互式 `codex --sandbox` |
| `AGENT_WORKBENCH_CODEX_APPROVAL` | `never` | 传给交互式 `codex --ask-for-approval`，默认不审批 |
| `AGENT_WORKBENCH_CODEX_BYPASS` | 空 | 设为 `1` 时使用 `--dangerously-bypass-approvals-and-sandbox`，会关闭 sandbox 和审批 |
| `AGENT_WORKBENCH_CODEX_NO_ALT_SCREEN` | `1` | 默认追加 `--no-alt-screen`，便于 tmux capture-pane 获取日志 |
| `AGENT_WORKBENCH_CODEX_ARGS` | 空 | 追加给交互式 `codex` 的参数 |
| `AGENT_WORKBENCH_CLAUDE_PERMISSION_MODE` | `dontAsk` | 传给交互式 `claude --permission-mode` |
| `AGENT_WORKBENCH_CLAUDE_SKIP_PERMISSIONS` | 空 | 设为 `1` 时追加 `--dangerously-skip-permissions` |
| `AGENT_WORKBENCH_CLAUDE_ARGS` | 空 | 追加给交互式 `claude` 的参数 |
| `AGENT_WORKBENCH_CHAT_WAIT_SECONDS` | `120` | HTTP 请求等待当前 turn 写回 result 的秒数 |
| `AGENT_WORKBENCH_TMUX_STARTUP_DELAY_S` | `1.5` | 兼容保留；主路径由 idle/output-rate detector 判断 ready |
| `AGENT_WORKBENCH_TMUX_SUBMIT_DELAY_S` | `0.15` | 粘贴文本后发送回车前的等待秒数 |
| `AGENT_WORKBENCH_TMUX_SUBMIT_KEY` | `C-m` | 自动提交使用的 tmux key，默认比 `Enter` 更接近真实回车 |
| `AGENT_WORKBENCH_TMUX_POLL_INTERVAL_S` | `1` | detector 采样间隔 |
| `AGENT_WORKBENCH_TMUX_SILENCE_THRESHOLD_S` | `0.6` | output.log 静默多久视为一个 idle tick |
| `AGENT_WORKBENCH_TMUX_PROMPT_IDLE_TIMEOUT_S` | `300` | 等待 TUI idle 的最长时间 |
| `AGENT_WORKBENCH_TMUX_PROMPT_READY_SETTLE_S` | `2` | 检测到 TUI 后的稳定等待 |
| `AGENT_WORKBENCH_TMUX_PROMPT_READY_SETTLE_FAST_S` | `0.5` | 非 TUI 简单命令的快速稳定等待 |
| `AGENT_WORKBENCH_TMUX_PROMPT_STABLE_TIMEOUT_S` | `10` | paste 后等待稳定的最长时间 |

当前默认 Codex 启动参数等价于：

```bash
codex --no-alt-screen -C /Users/yang/agents/agent \
  --sandbox workspace-write \
  --ask-for-approval never
```

## External CLI Worker 运行目录与 ready detector

worker/provider 不用固定 sleep 判断 Codex/Claude 是否 ready。它会通过 `pipe-pane` 把 pane 输出写入 `output.log`，并在 `status.json` 中持续记录：

- `phase`
- `bytes_total`
- `bytes_per_sec`
- `idle_seconds`
- `silent_ticks`
- `required_idle_ticks`
- `tui_detected`

只有状态进入 idle 后才 paste prompt 并发送 `C-m`。`startup_delay_seconds` 仅作为兼容配置保留，不是主路径。

Runtime 面板 run：

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

聊天 session 的持久 CLI 会话：

```text
runs/workbench/sessions/<session_id>/
  state.json
  current_turn.json
  runtime_contract.md
  messages.jsonl
  events.jsonl
  linked_outputs.json
  pending_turns.json
  turns/<turn_id>/
    raw_user_message.txt
    prompt.md
    sent_to_tmux.txt
    result.json
  runtime/provider/<provider_run_id>/
    prompt.md
    output.log
    status.json
    meta.json
    command.sh
```

## 当前限制

- 内容生成完整 10 步 workflow 尚未从 `orchestrator.py` 抽成 Web controller。
- `claude_cli` 默认只追加 `--add-dir <项目目录>`，权限模式等需要通过 `AGENT_WORKBENCH_CLAUDE_ARGS` 固化。
- LLM API backend 只是未来扩展，尚未实现。
- e2e 依赖缺失时，KB 搜索会在 UI 中显示错误，不阻塞工作台启动。
