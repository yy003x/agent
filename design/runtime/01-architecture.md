# 01 架构：定位、原则、分层

> 项目: `shared-runtime`；类型: `shared-core`。

## 定位

**是什么**：一个 Agent「跑起来」所需的通用机制集合——配置、LLM 调用、上下文、状态、技能/工具、工作区、循环编排、规划/执行/观察、记忆、事件、护栏、持久化、可观测。

**不是什么**：
- 不含任何具体业务（不含 lark / kb / 选题 / 选股 / 出版等领域逻辑）。
- 不绑定调用方目录布局（不假设存在某个固定 `agent_root` / `workspace_root`）。
- **吸收 `workbench-runtime`**（决策 #5）：其 tmux 会话管理逻辑蒸馏为本 runtime 的 `tmux` transport（与 code_cli/llm_api 统一文件状态同步）；workbench 迁移完成后 `workbench-runtime` app 退役，不长期保留两份 tmux 实现。

## 五条原则（违反即设计回退）

1. **库优先，服务可选**——内核是进程内库；服务/CLI 是内核外的壳。见「库优先三姿势」。
2. **配置注入，零硬编码路径**——所有外部位置/凭证/后端经 `ConfigManager` 注入。反例：把 `agent_root` 绑死某项目目录。
3. **业务无关**——内核只有通用机制，业务能力经 `SkillManager`/`ToolManager`/`Memory backend` 注册进来。反例：内核里混进 `lark_config`。
4. **窄而稳的契约**——对外只暴露一组稳定动词，内部组件自由演进。
5. **单向依赖 + 事件解耦**——上层依赖下层，跨层/反向通信只走 `EventBus`，杜绝循环依赖。

**项目维度**：runtime 被多项目复用，所有 request/result/run 记录必须显式带 `project_id`；内核只放跨项目机制，机器可读 overlay 放 `projects/<id>.runtime.yaml`，人读差异放 `projects/<id>.md`。

## 库优先两入口 + 上层 SDK

「统一 runtime」≠「runtime 自己启动 HTTP server」。`LLMGateway` 的模型客户端、`Memory` 的 embedding/reranker/向量库连接是**重型、需常驻复用**的资源；若内核碎成 RPC，每次调用冷启加载模型（数秒 + 数百 MB），本地场景不可用。

因此分两层：内核（进程内库，重型资源常驻） + 薄入口/SDK。runtime 自身只提供库和 CLI；`llm_api` 的 HTTP/SSE client 由 provider 或上层 SDK 封装，所有状态仍写入文件契约。

| 姿势 | 形态 | 适用 | 代价 |
| --- | --- | --- | --- |
| 进程内库 | `rt = AgentRuntime(config); rt.run(...)` | 同语言 Python、复用常驻模型、低延迟 | 与调用方同进程 |
| CLI | `agent-runtime <verb>` 子进程 + JSON | 脚本/调度器、跨语言、跨仓库松耦合 | 每次冷启，仅适合非高频 |
| 上层 SDK client | `AgentRuntimeClient` / `LLMApiClient` 调远端 HTTP/SSE | `llm_api` provider、需要流式模型响应的上层应用 | 不拥有 runtime 状态，只负责把事件/结果写回文件 |

约束：内核**无 I/O 服务器假设**（不 import web 框架、不提供 `serve`）；库和 CLI 走同一 `RuntimeService`；SDK client 是调用远端 LLM API 的适配层，不是本地 runtime server（详见 [05](05-integration-and-migration.md)）。

## 分层架构（16 组件，四层 + 两横切）

```text
                ┌──────────── 横切：EventBus（异步解耦总线） ────────────┐
  L3 治理       │                  Guardrail（护栏/边界）                 │ ← 拦截所有副作用动作
  L2 编排    ContextManager → LoopEngine → Planner → Executor → Observer  │
                │            ↘ StateManager ↙        ↘ Memory ↙           │
  L1 能力/网关  LLMGateway  SkillManager  ToolManager  Memory  WorkspaceMgr│
  L0 基础设施   ConfigManager   Persistence   Logger/Tracer                │
                └────────── 横切：Logger/Tracer（贯穿每层埋点） ──────────┘
```

依赖规则：**L_n 只依赖 L_{<n}**；反向/同层通信走 `EventBus`；`ConfigManager`/`Logger`/`EventBus` 由 DI 注入而非各组件自取。

## 一次 agent turn 数据流

1. 输入 → `ContextManager` 组装上下文（历史 + `Memory` 召回 + `Skill`/`ToolManager` 能力清单）。
2. `LoopEngine` 驱动循环：
   1. `Planner` 经 `LLMGateway` 产出下一步（计划/动作/终止）。
   2. `Guardrail` 校验动作（权限/边界/预算）→ 拒绝则回退或终止。
   3. `Executor` 执行（调 `Tool`/`SkillManager`，外部 CLI/tmux 走 transport provider）。
   4. `Observer` 解析结果与状态（result_file / 输出模式 / LLM 评判）。
   5. `StateManager` 更新状态、`Memory` 写入、`Persistence` 落盘。
   6. `EventBus` 广播事件（UI/日志/审批订阅）。
   7. 判终止条件，否则回 1。
3. 输出 + trace。

## 组件归属：内核机制 vs 项目注入

**这是「富 kernel」不踩红线的关键**——16 组件都在内核，但分两类：

| 类别 | 组件 | 内核提供 | 项目注入 |
| --- | --- | --- | --- |
| 纯机制（P1 核心） | ConfigManager / LLMGateway / StateManager / Persistence / Logger·Tracer / Guardrail(机制) | 全部 | policy 值、provider 配置 |
| 机制 + 注入内容 | SkillManager / ToolManager / Memory / WorkspaceManager / ContextManager | 接口、注册、路由、隔离、调度 | **技能/工具实现、记忆 backend、工作区根、上下文策略** |
| 编排（P1/P4） | LoopEngine / Planner / Executor / Observer / StateManager | 全部机制 | 规划/观察策略可插拔 |
| 横切 | EventBus / Logger·Tracer | 全部 | 订阅者 |

红线：**领域 guardrail（stock 风控）、业务 prompt、业务 schema、知识库内容**绝不进内核——只经注入（Skill/Tool/Memory backend/policy）接入。内核 `grep` 不应出现任何业务名或写死的仓库路径（CI 加静态检查）。

组件逐项职责与注意项见 [03-orchestration-loop.md](03-orchestration-loop.md)（L2/L3 编排治理）与 [02-runtime-core.md](02-runtime-core.md)（L0/L1 基础与网关）。
