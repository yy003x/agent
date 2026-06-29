# 05 接入、迁移与路线图

> 项目: `shared-runtime`。入口形态、各项目迁移、现有资产映射、分阶段 P0-Pn。

## 入口与 SDK 边界

| 姿势 | 调用 | 适用项目 |
| --- | --- | --- |
| 进程内库 | `rt = AgentRuntime(config); rt.run(...)` | workbench / agent（Python，需复用常驻模型） |
| CLI | `agent-runtime <verb>` 子进程 + JSON | stock（当前 Go，subprocess 调）、脚本/调度器一次性任务 |
| 上层 SDK client | `LLMApiClient` / `AgentRuntimeClient` 调远端 HTTP/SSE，写回 runtime 文件 | `llm_api` provider、前端或业务服务需要流式模型响应 |

库和 CLI 走同一 `RuntimeService`，**core 永远是文件状态同步**（request/status/events/output/result）。HTTP/SSE 只用于 `llm_api` provider 调远端模型 API，或由上层应用自行封装 SDK；runtime 不启动 `serve`，不暴露本地 HTTP API，不维护第二套状态源。

```text
lib:  AgentRuntime(config).run_task(...) / start_session(...) / loop_step(...)
CLI:  agent-runtime doctor|profiles|task|session|loop ...
SDK:  LLMApiClient.stream(...) -> events.jsonl + result.json
```

SDK 返回只含脱敏状态，不返回 secret/完整 env/完整高敏 prompt；日志和事件以文件为唯一事实。上层若要 HTTP/SSE 给前端，必须在业务应用层自行转发 `events.jsonl`，不能把 runtime core 改成 server。

## 各项目接入模型

每个接入项目维护 `projects/<project-id>.runtime.yaml`（机器读取）和 `projects/<project-id>.md`（人读说明，详见 [../projects/](../projects/)）。项目说明可收紧共享规则、不能放宽共享安全红线。

| 项目 | 栈 | 接入方式 |
| --- | --- | --- |
| workbench | Python | 第一接入方；import lib/CLI；`workbench-runtime` 迁完直接退役；`workbench-loop` 接 LoopControl；与 `lark-runtime` 保持分离 |
| agent | Python | 第二接入方；import lib；`model_backends` → LLMGateway；`runtime/{process_cli,llm_api,tmux}_provider.py` → 三 transport；`brain/orchestrator` 上移为 P6 上层 |
| stock | Python 重构目标 | 第三接入方；`agent-runtime` 可重构为 Python 后坐到 runtime+loop；领域 guardrail / Evidence / Report / SQLite 事实写入留 stock adapter |

## 现有资产迁移映射

| 现有资产 | 去向（runtime 内核） | 处理 |
| --- | --- | --- |
| `agent/apps/workbench/runtime/process_cli_provider.py` | `code_cli` transport | 蒸馏，保留权限拦截/终止 |
| `agent/apps/workbench/runtime/llm_api_provider.py` | `llm_api` transport | 蒸馏 |
| `agent/apps/workbench/runtime/tmux_provider.py` | `tmux` transport | 蒸馏 |
| `agent/apps/workbench/model_backends.py` | `LLMGateway` | 迁入 + provider 抽象 + 改注入 |
| `agent/apps/agent/{brain,orchestrator}.py` | `Planner`/`Executor`/classifier | 概念升级，P6 才进核心 |
| `workbench/scripts/agent_runtime/process_runner.py` | `Executor` 进程/tmux backend | 迁入，保留权限拦截/终止 |
| `workbench/scripts/agent_runtime/json_state.py` | `Persistence` | 迁入（原子写） |
| `workbench/scripts/agent_runtime/workspace_paths.py` | `WorkspaceManager` | 迁入 + 去 `agent_root` 硬编码，改注入 |
| `workbench/scripts/agent_runtime/env.py` | `ConfigManager` | 拆解：路径定位→注入；profile 解析保留 |
| `workbench/scripts/agent_runtime/logs.py` | `Logger/Tracer` | 迁入 + 扩 trace |
| `workbench/apps/workbench-runtime`（tmux owner） | `tmux` transport（蒸馏进内核） | tmux 逻辑蒸馏为 core transport；迁移期暂留、workbench 迁完后该 app **退役**（决策 #5 吸收） |
| `workbench/apps/workbench-loop` | LoopControl | 蒸馏 interactive-serial、dry-run、done/block_when、review 契约 |
| `stock/agent-runtime`（Go：provider/codex/task/store） | stock adapter + shared task/loop | 可按 Python 重构坐到内核上；业务 schema/guardrail 不进 shared core |
| `lark_config` / `lark_runner` / `kb_config` / 各业务 guardrail | **不迁入** | 业务专属，留各业务仓，经 Skill/Tool/policy 注入 |

## 分阶段路线

- **P0 内核骨架**：`ConfigManager`（注入式）+ `EventBus` + `Logger/Tracer` + `Persistence` + 对外动词空壳；定义全部组件接口与事件 schema；`fake` provider；task/result schema；`projects/<id>.runtime.yaml` + `projects/<id>.md` 首批。可空跑最简 LoopEngine（fake LLM）。
- **P1 执行闭环**：三 transport provider（蒸馏现成）+ `Executor`；`LLMGateway` 先 mock/OpenAI-compatible；`Planner`/`Observer`/`StateManager`/`LoopEngine` 单步→多步；`Guardrail` 基础权限拦截；result_file 校验 + runs/audit。
- **P2 能力体系**：`SkillManager` + `ToolManager` + `WorkspaceManager` + `ContextManager`；`Memory` 接 backend 注入（常驻）。
- **P3 接入**：CLI 补齐 + 上层 SDK client；workbench / agent 切到内核（**先吃自己狗粮**），不启动 runtime HTTP server。
- **P4 loop 控制收口**：合并 `workbench-loop` + `stock task` 为统一 LoopControl，补 loop→runtime connector、review 和 capability 门禁。
- **P5 stock 收敛**：`agent-runtime` 可按 Python 重构坐到 runtime+loop 上，领域逻辑留 stock。
- **P6 上层 agent-loop**：蒸馏 agent classifier/planner/executor 为最上层。
- **P7 迁移兼容工具**：提供 `migration report` / project probe / 兼容 smoke，逐项检查原三个项目根目录、规则入口、profile、runs 文件契约和禁用能力。
- **P8 整体验收与治理**：提供 `review` gate，聚合 doctor、profile、overlay、result schema、loop/audit 检查；产出可重复运行的最终验收命令。

每阶段单独验证 + 确认；跨项目改动逐项目灰度。

## 阶段验收口径

每个阶段按固定闭环推进：

1. 实现阶段内最小功能，保持 project 业务逻辑在各自 adapter。
2. 跑 runtime 单测、编译、`git diff --check`。
3. 对 workbench → agent → stock 依次做兼容 smoke；stock 的禁用能力必须验证拒绝。
4. 自我审查：检查是否违反文件契约、HTTP/SSE 边界、stock 业务边界、secret 边界、状态名兼容。
5. 失败修复后重试；单阶段同类失败三次才停止。

## 已确认决策（2026-06-23）

| # | 议题 | 决策 | 来源 |
| --- | --- | --- | --- |
| 1 | HTTP/SSE 边界 | **不封装 runtime server**；HTTP/SSE 只作为 `llm_api` provider/上层 SDK 调远端模型 API，core 永远文件同步 | 用户确认 |
| 2 | 包名与分发 | 包 `runtime`（避撞 workbench `scripts/agent_runtime`）；`pip install -e` 编辑安装，不上 registry | 命名用户授权自定 |
| 3 | Memory/KB 边界 | KB 作为 **Memory backend 注入**，内核只定义 Memory 接口 | 按推荐 |
| 4 | 接入顺序 | **workbench → agent → stock**；scheduler 暂不作为首批（生产 cron 风险高） | 用户确认 |
| 5 | 与 `workbench-runtime` 关系 | **吸收**：tmux 作为 core 的一个 transport（统一文件同步），`workbench-runtime` app 迁完后退役 | 用户确认（三 transport 含 tmux） |
| 6 | .venv | runtime 自带 `.venv` 做开发/CLI；消费方各自 venv 里 `pip install -e` | 按推荐 |
| 7 | overlay/config 格式 | **YAML**（机读：profiles / project overlay / runtime config）；`projects/<id>.md` 仅人读说明；状态产物仍 JSON | 用户确认 |
| 8 | llm_api 启用 | P1 进**结构 + mock**，真实 API 默认关；HTTP/SSE client 可在 provider/上层 SDK 封装 | 用户确认 |
| 9 | stock 边界 | stock 业务不进 runtime；`agent-runtime` 可 Python 重构，但 Evidence/Report/投顾 guardrail/SQLite 事实写入留 stock adapter | 用户确认 |
| 10 | loop 控制 | 加入 LoopControl 设计，统一 workbench-loop 与 stock task 的准入、派发、评审和门禁 | 用户确认 |

> 仍待定（进 P0 前再确认，不阻塞设计）：#3/#6 为「按推荐」默认值，若有异议随时改。
