# 图书运营 Agent 工作台 0-1 重设计

本文是本项目升级后的主设计入口。现有 `01-06` 设计都作为参考材料保留；后续从 0 到 1 重构和补齐时，以本文的模块边界、阶段路线和验收标准为准。

---

## 1. 背景与目标

当前 Agent 的真实目标不是单一“内容生成脚本”，而是一个面向图书运营日常工作的本地工作台：

- 整理大量产品资料、图书文档、图片、视频和运营素材。
- 把素材纳入本地知识库，支持检索、回读、复用和清理。
- 通过聊天式交互完成选题、检索、草稿、润色、组装、预览和复盘。
- 通过 GUI 管理长任务和终端 Agent 会话，也保留 CLI 入口给熟练用户。
- 所有对外发布都由人工完成，Agent 只生成草稿、成品包和发布前 checklist。

目标态一句话：

```text
本地 Web 工作台 + Python 编排服务 + 本地知识库 + tmux 托管的 CLI 智能 runtime + 可审计运行记录。
```

---

## 2. 核心原则

1. 本地优先：所有业务资料、索引、草稿、运行记录默认保存在本机项目目录。
2. 工作台优先：GUI 是日常入口，CLI 是高级入口，两者复用同一套 workflow controller。
3. Runtime 收敛：工作台托管的智能步骤统一走 tmux 真会话，默认 `codex_cli`，`claude_cli` 通过配置启用。
4. 长任务可审计：耗 token、耗时长、一次性内容生成或素材整理任务都走 tmux CLI runtime，并用 result file 判定完成。
5. 确定性步骤直连 Python：KB、文件、媒体组装、打包、finalize 等不交给 LLM 自由发挥。
6. 人工确认门：素材选择、写文件、组装、发布前预览、高影响操作都必须可见、可停、可追溯。
7. 不自动发布：不调用小红书、微信、群发等外部发布 API。
8. 不泄露敏感值：UI、日志、成品包、session 记录都不得展示 token、API key、cookie、private key 或完整 JWT。

---

## 3. 用户与主要场景

### 3.1 用户画像

学而思图书运营人员，日常围绕 K12 学生家长做内容运营：

- 小红书图文。
- 朋友圈文案。
- 家长群话术。
- 图书推荐、书单、读书笔记、知识卡片、短视频脚本。

默认口吻是真诚、专业、软引导，不硬广、不制造焦虑。

### 3.2 高频场景

| 场景 | 用户动作 | 系统动作 |
|---|---|---|
| 素材入库 | 选择产品资料、文档、图片、视频目录 | 解析、caption、向量化、写 KB、记录 ingest |
| 素材检索 | 输入主题或图书名 | hybrid search、展示候选、允许回读事实源 |
| 内容生成 | 输入平台、主题和风格 | 检索素材、生成草稿、润色、组装成品包 |
| 长任务生成 | 发起复杂选题、批量整理、深度内容生成 | tmux 启动真实 `codex` / `claude` 交互会话，按 result file 回收 |
| 成品检查 | 查看 outputs | 预览标题、正文、标签、图片、视频 clip 和 checklist |
| 运行排障 | 打开系统面板 | 看依赖、CLI、tmux、KB、scheduler、最近 session |
| 复盘学习 | 查看 session 和候选 | 人工确认后晋升 memory、rules 或 skill |

---

## 4. 总体架构

```text
┌──────────────────────────── 浏览器工作台 ────────────────────────────┐
│ Chat  内容流水线  素材库  产出中心  Runtime  系统面板                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP + SSE/WebSocket
┌───────────────────────────────▼─────────────────────────────────────┐
│ Python Workbench Service                                             │
│                                                                      │
│ API Layer                                                            │
│   chat / workflow / kb / outputs / files / runtime / health          │
│                                                                      │
│ Application Layer                                                    │
│   ChatSessionStore                                                   │
│   ContentWorkflowController                                          │
│   AssetIngestController                                              │
│   OutputBrowser                                                      │
│   HealthDoctor                                                       │
│                                                                      │
│ Runtime Layer                                                        │
│   MainRuntime                                                        │
│   DirectPythonAdapter: content_runtime / finalize / scheduler         │
│   ExternalCliWorkerRuntime: codex_cli 默认 / claude_cli 配置 / result file │
│   OfflineTemplateRuntime                                             │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ Local State                                                          │
│ rules/ skills/ memory/ design/                                       │
│ workspace/kb/ workspace/media-store/ workspace/daily/                │
│ outputs/ runs/workbench/ runs/tmux/ runs/scheduler/               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. 模块边界

### 5.1 Workbench UI

职责：

- 展示聊天、事件流、确认按钮和任务状态。
- 展示素材库搜索结果、文件预览和 ingest 状态。
- 展示草稿、plan、成品包和发布 checklist。
- 管理 runtime runs：start、status、logs、send、stop、attach。
- 展示系统健康状态。

不负责：

- 不直接读写敏感文件。
- 不直接调用外部发布平台。
- 不在前端实现业务判断，业务判断在 Python service。

### 5.2 Python Workbench Service

职责：

- 提供本地 HTTP API。
- 管理 chat session、workflow state、runtime state。
- 把用户操作转换为 workflow controller 的输入。
- 调用 `content_runtime.py`、`finalize.py`、scheduler 和 runtime manager。
- 写入 `runs/workbench/` 运行状态。

### 5.3 ContentWorkflowController

职责：

- 复用 `content-generate` 10 步流程。
- 把终端交互原语抽象为 `WorkflowIO`。
- GUI 和 CLI 共享同一个 controller：
  - `TerminalWorkflowIO` 对应 `print/input`。
  - `WebWorkflowIO` 对应事件流和确认按钮。

### 5.4 KnowledgeBase

职责：

- 素材 ingest。
- 文档分块、图片 caption、视频抽帧 caption。
- LanceDB 向量、标量、FTS、图召回。
- 搜索日志、命中时间、归档和清理。

对外只暴露 `content_runtime.py kb ...` 及 service API，不让 UI 关心 LanceDB 内部表结构。

### 5.5 Main Runtime 与 External CLI Worker Runtime

统一智能步骤入口：

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

ExternalCliWorkerRuntime
├── codex_cli          # 默认 runtime，命令形态为交互式 codex，不使用 codex exec
├── claude_cli         # 可配置 runtime，命令形态为交互式 claude，不使用 claude -p
├── tmux_provider      # runtime 内部 provider，不暴露给 UI 层
└── file_result        # result file 回收
```

覆盖任务：

- 输入分类。
- 需求抽取。
- 文案润色和改写。
- 问答和选题讨论。
- 运营合规自查。
- 图片 / 视频 caption。

External CLI Worker Runtime 底层必须通过 provider 边界实现：

- `TmuxRunSpec`：描述 command、cwd、tmux session、prompt、result file、timeout 和发送方式。
- `TmuxProvider`：位于 runtime 包内部，只暴露 `start/status/logs/send/stop/close` 等操作。
- `external_cli.py`：只做工作台配置到 provider spec 的翻译，不直接承载 UI 业务逻辑。
- `main.py`：作为 UI / health 的唯一 runtime 入口，组合 Planner、Executor、Observer、State、Skill Registry 和 Task Store。
- `Skill Registry`：扫描项目根 `skills/*/SKILL.md`，作为工作台可展示、可调度的 skill 能力事实源；第一版只读展示，执行仍由 workflow/runtime 明确触发。

目录命名说明：当前实现已拆为 `apps/api/`、`apps/web/` 和项目级 `runtime/`。`apps/workbench/` 只保留旧启动命令的兼容壳；`apps/api/` 承载 FastAPI 服务，`apps/web/` 承载 React + TypeScript 前端，`runtime/` 承载 External CLI Worker Runtime 和 provider。

---

## 6. 核心业务流程

### 6.1 素材入库

```text
用户选择目录
  -> 选择 modality / limit / resume
  -> dry-run 预览
  -> 用户确认
  -> content_runtime.py kb ingest --allow-write
  -> doc: 文本解析、分块、写 KB
  -> image: runtime caption 或人工 caption、复制 media-store、写 KB
  -> video: ffmpeg 抽帧、runtime caption、复制 media-store、写 KB
  -> 写 ingest log 和 finalize activity
```

失败策略：

- KB 依赖缺失：UI 显示安装建议，不阻塞工作台启动。
- runtime 不可用：允许手工 caption 或用文件名/目录标签降级。
- 单个素材失败：记录失败，批处理继续，最后展示失败清单。

### 6.2 内容生成

```text
用户输入需求
  -> route.classified
  -> extract requirements
  -> KB search
  -> 展示候选素材
  -> 用户选择素材
  -> 回读 source_path 事实源
  -> text draft
  -> runtime polish
  -> 用户修改或确认
  -> plan build
  -> 用户确认组装
  -> media assemble
  -> publish package
  -> 预览 outputs
  -> finalize record
```

关键约束：

- 所有内容事实必须来自用户输入或选中素材。
- 没有素材时可以生成泛化草稿，但必须标注“未引用具体素材”。
- 成品包只落 `outputs/`，不自动发布。

### 6.3 长任务 runtime

适用：

- 深度选题设计。
- 批量素材整理。
- 高 token 文案生成。
- 多轮复杂改写。
- 需要保留终端上下文的 Agent 任务。

执行：

```text
UI 创建 run
  -> runs/tmux/<run_id>/prompt.md
  -> runs/tmux/<run_id>/meta.json
  -> TmuxProvider 创建 tmux session/window/pane
  -> provider 写 command.sh/status.json/output.log
  -> command 启动真实 codex / claude 交互会话
  -> detector 采样 output.log，等待 TUI idle
  -> tmux buffer 原样粘贴任务文本，并用 C-m 自动提交
  -> tmux capture-pane 作为日志快照
  -> result.json 稳定写入
  -> UI 标记 done
```

只信 `result.json`，不靠屏幕内容判断完成。

---

## 7. 数据与目录

```text
workspace/
  kb/
    lance/                 # LanceDB
    search-log.jsonl
    caption-cache.json
    graph.jsonl
  media-store/             # ingest 后的媒体副本
  daily/                   # session 摘要
  agent-learning/          # 自学习候选
  resume/                  # 未完成恢复点

outputs/
  YYYY-MM-DD/
    content/<slug>/
      sources.json
      draft.json
      plan.json
      xiaohongshu/
        publish-checklist.md
        *.jpg
      moments/
      wechat_group/

runs/
  workbench/
    sessions/chat-*/
      messages.jsonl
      events.jsonl
      state.json
      linked_outputs.json
  tmux/<run_id>/
    prompt.md
    result.json
    output.log
    status.json
    meta.json
    command.sh
    events.jsonl
```

目录规则：

- `design/` 是设计第一事实源。
- `workspace/`、`outputs/`、`runs/` 是运行态，默认不进 Git。
- session 记录不存用户原始长对话，只存摘要、路径和结构化事件。

---

## 8. API 设计

第一版 API：

```text
GET  /                         -> Web UI
GET  /api/health               -> 依赖和 runtime 状态
GET  /api/state                -> 工作台概览

POST /api/chat/sessions
GET  /api/chat/sessions
GET  /api/chat/sessions/{id}
POST /api/chat/sessions/{id}/messages
GET  /api/chat/sessions/{id}/events

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
```

第二版补齐：

```text
POST /api/workflows/content
GET  /api/workflows/{workflow_id}
POST /api/workflows/{workflow_id}/confirm
POST /api/workflows/{workflow_id}/cancel

GET  /api/scheduler/jobs
POST /api/scheduler/jobs/{job_id}/run

GET  /api/learning/candidates
POST /api/learning/candidates/{id}/decision
```

---

## 9. UI 信息架构

### 9.1 第一版布局

```text
左侧：会话列表、健康摘要
中间：聊天、消息、输入框
右侧：事件、素材库、产出、Runtime、系统
```

### 9.2 页面职责

| 页面 | 第一版 | 第二版 |
|---|---|---|
| 聊天 | 会话、消息、草稿 dry-run | 完整内容 workflow |
| 事件 | 显示结构化事件 | 支持确认、恢复、重试 |
| 素材库 | 搜索 KB | ingest、批处理状态、相关素材 |
| 产出 | 文件列表和预览 | 成品包专用预览、复制按钮、版本对比 |
| Runtime | tmux run 管理 | runtime preset、canary、attach |
| 系统 | 健康检查 | scheduler、self-learning、配置编辑预览 |

---

## 10. 配置设计

目标配置：

```toml
[server]
host = "127.0.0.1"
port = 8765
open_browser = true

[paths]
runtime_dir = "runs/workbench"
tmux_runtime_dir = "runs/tmux"

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
default_style = "知识科普"
```

敏感配置：

- API key 只放本地 `.env` 或系统环境变量。
- UI 只能展示“已配置/未配置”，不能展示值。
- CLI 登录态由 `codex` / `claude` 自行管理，UI 不读取 credential 文件。

---

## 11. 安全设计

### 11.1 文件访问

允许预览：

- `design/`
- `rules/`
- `skills/`
- `memory/`
- `workspace/`
- `outputs/`
- `runs/`
- `apps/`
- `scripts/`

拒绝：

- `.env`
- `*.pem`
- `*.key`
- `*.p12`
- cookie/token/secret/private key 路径
- project root 之外路径

### 11.2 写操作

必须二次确认：

- ingest。
- media assemble。
- publish package。
- KB gc。
- runtime stop。
- self-learning accept。
- 删除或覆盖已有输出。

### 11.3 运营合规

所有外部文案都必须通过安全规则：

- 不承诺提分、升学、保过、效果。
- 不用极限词。
- 不制造教育焦虑。
- 不伪造家长评价。
- 不暴露内部价格策略和个人信息。
- 不贬低其他机构。

---

## 12. 实施路线

### W0 设计收敛

目标：明确新主设计，旧设计作为参考。

交付：

- `design/00-workbench-0-1-redesign.md`
- `design/README.md` 指向新主设计

验收：

- 所有后续实现任务能从本文定位模块和阶段。

### W1 工作台基础壳

目标：Web UI 可打开，看到会话、健康状态、文件和 runtime。

交付：

- `apps/api/main.py`
- `apps/api/health.py`
- `apps/api/file_browser.py`
- `apps/api/services/workbench.py`
- `apps/web/`
- `runtime/main.py`
- `runtime/external_cli.py`
- shared runtime provider 适配

验收：

- `python apps/workbench/server.py 8765` 可启动。
- `python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8765` 可启动。
- `http://127.0.0.1:8765` 可打开。
- fake tmux run 能写 result file。

### W2 Workflow Controller 抽象

目标：拆出可复用内容状态机。

交付：

- `apps/agent/workflow_controller.py`
- `TerminalWorkflowIO`
- `WebWorkflowIO`

验收：

- 原 CLI REPL 行为不退化。
- Web 能走到素材选择确认节点。

### W3 MainRuntime

目标：统一智能 runtime。

交付：

- `runtime/main.py`
- `runtime/external_cli.py`
- shared runtime provider 适配
- `ExternalCliWorkerRuntime`：默认 `codex_cli`，可配置 `claude_cli`
- `OfflineTemplateRuntime`
- API backend 接口占位，但不是第一版默认路径

验收：

- 分类、需求抽取、润色、caption 都从 MainRuntime 调用，工作台路径走 tmux 真会话。
- `codex` / `claude` 至少一种 CLI canary 成功。

### W4 内容 workflow 全链路

目标：GUI 从聊天跑到成品包。

交付：

- KB 搜索和候选选择 UI。
- 草稿预览和修改 UI。
- plan 预览和组装确认。
- outputs 成品包预览。
- finalize 状态展示。

验收：

- 输入“出一篇数学思维书单小红书文案”能生成 `outputs/YYYY-MM-DD/content/<slug>/publish-checklist.md`。

### W5 素材库与 ingest

目标：GUI 能整理资料入库。

交付：

- 目录选择或路径输入。
- ingest dry-run。
- 批处理状态。
- caption fallback。
- ingest 结果报告。

验收：

- 文档、图片、视频各一个样例能完成入库或明确失败原因。

### W6 系统面板与自学习

目标：工作台能显示运行状态和学习候选。

交付：

- scheduler jobs 展示。
- 最近 session 展示。
- learning candidates 展示。
- accept/reject/modify 的 preview-first 流程。

验收：

- 不展示敏感值。
- accept 前展示 patch，确认后执行。

### W7 打磨与验收

目标：第一版可日常使用。

交付：

- README 启动说明。
- quick/e2e 验证。
- 真实素材回归集。
- UI smoke checklist。

验收：

- `bash scripts/validate.sh --quick` 通过。
- 工作台首页、健康、聊天、KB、outputs、runtime smoke 通过。

---

## 13. 当前实现差距

截至本文创建时：

- 已完成 FastAPI + TypeScript 拆分：`apps/api/` 为后端入口，`apps/web/` 为前端入口，`runtime/` 为智能运行时入口，`apps/workbench/` 为兼容壳。
- `orchestrator.py` 仍是终端状态机，尚未拆成 controller。
- `brain.py` 和 `content_runtime.py` 仍有 legacy `codex exec` 调用，尚未迁移到工作台 tmux 真会话 runtime。
- `claude_cli` 通过配置启用，LLM API backend 是未来扩展，尚未完成业务编排接入。
- e2e 依赖环境仍缺部分包和 ffmpeg。

---

## 14. 验证清单

每个阶段至少跑：

```bash
git diff --check
bash scripts/validate.sh --quick
```

GUI smoke：

```bash
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8765
# 兼容旧命令：
# python apps/workbench/server.py 8765
curl -I http://127.0.0.1:8765/
curl http://127.0.0.1:8765/api/health
```

tmux smoke：

```bash
curl -X POST http://127.0.0.1:8765/api/runtime/tmux/runs \
  -H 'Content-Type: application/json' \
  -d '{"runtime":"fake","prompt":"fake smoke"}'
```

内容链路 smoke：

```bash
python skills/content-generate/scripts/content_runtime.py text draft \
  --brief "数学思维书单" \
  --platform xiaohongshu \
  --style "知识科普"
```

---

## 15. 旧设计如何使用

| 文件 | 新定位 |
|---|---|
| `01-framework.md` | rules / skills / finalize 机制参考 |
| `02-self-evolution.md` | 自学习候选与晋升流程参考 |
| `03-content-agent.md` | 内容 workflow 和平台成品包参考 |
| `04-knowledge-base.md` | KB 表结构、检索和清理参考 |
| `05-implementation-steps.md` | P0-P5 已有基线实现历史路线 |
| `06-graphical-agent-workbench.md` | GUI 工作台细节参考 |
| `content-agent-architecture.md` | 旧架构总览，后续逐步并入本文 |

原则：新实现决策先写入本文；如果细节稳定，再同步拆到对应专题设计。
