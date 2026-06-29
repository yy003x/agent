# Agent Runtime 设计

`~/agents/runtime/` 是与业务解耦的**公共 Agent 运行时**，供 `workbench` / `agent` / `stock` 及未来项目复用，统一维护。本目录是全部设计的唯一事实源。

## 这份设计要解决什么

三个项目各自实现了一份 runtime 调用层，且互相覆盖三种执行方式（`llm_api` / `code_cli` / `tmux`）的不同子集：

- `workbench/apps/workbench-runtime`（tmux）、`workbench/apps/workbench-loop`（编排状态机）
- `agent/apps/workbench`（llm_api 多 provider + `runtime/{process_cli,llm_api,tmux}_provider.py`）、`agent/apps/agent`（classifier/planner/executor 已成形）
- `stock/agent-runtime`（Go：Provider 接口 + codex exec + task/store/guardrail）

目标：**抽一份公共 runtime，三项目迁移过来调用、统一维护**；runtime 之上叠 loop/编排，最后补 classifier/planner/executor，做成一个**丰富但边界清晰的 agent runtime**。

## 已定的关键决策（消解三份草案的冲突）

| 维度 | 决策 | 说明 |
| --- | --- | --- |
| 语言 | **Python** | 复用 agent 的三 provider + workbench tmux；贴 LLM 生态；stock market-worker 本就是 Python |
| 形态 | **库优先，CLI 可选，SDK 在上层**（同一核心契约） | 内核进程内库；CLI 是壳；不内置 `serve` / HTTP server |
| 边界 | **lib + CLI + 上层 http/sse SDK client** | Python 项目 import；跨语言/脚本走 CLI+JSON；`llm_api` 的 HTTP/SSE client 由 provider/上层 SDK 封装，不成为 runtime server |
| 形态归属 | 顶层 `runtime/` 当 **live 共享依赖**（非 vendoring） | 配合 CLI 边界，单一事实源 |
| transport | **三种一起进 P1**（tmux / code_cli / llm_api） | 三种**统一用文件状态同步**（request/status/events/output/result）；`llm_api` 的 stream 也汇聚成 result.json，不特殊化；真实 API 默认关、先 mock |
| 状态同步 | **文件契约是唯一事实**；http/sse 只作为 LLM/provider 调用方式 | core 永远写文件；上层 SDK 只负责调用远端 HTTP/SSE 并把事件/结果写回文件契约 |
| 机读配置 | **YAML**（profiles / project overlay / runtime config） | 人写配置用 YAML；runtime 写的状态产物（request/status/result）仍 JSON 原子写，events 用 JSONL 追加 |
| 命名 | 包 `runtime`、门面类 `AgentRuntime`、核心 `RuntimeService`、CLI `agent-runtime`、客户端 `AgentRuntimeClient` | 包名避开 workbench 现有 `scripts/agent_runtime` 撞名；provider 层 `runtime.providers.{tmux,code_cli,llm_api}`，`LLMGateway` 仅是 llm_api 的网络网关 |
| 富 vs 薄 | **富 kernel + 注入边界** | 16 组件作为**机制**进内核；业务 skill/memory/工具内容由项目**注入**，不进内核 |
| 红线 | 领域逻辑（stock 风控、各项目业务）**绝不进内核** | 只进项目 overlay / policy 注入 |

> 「富 vs 薄」是三份草案最大的分歧点：`README` 主张薄、`DESIGN` 主张富 16 组件。结论取**富 kernel + 注入**——内核提供 Memory/Skill/Tool 的**接口与机制**，项目注入**内容**。这样既丰富又不把项目耦合死。

## 文档结构（按面向拆分，分阶段落地）

| 文档 | 内容 |
| --- | --- |
| [01-architecture.md](01-architecture.md) | 定位、五条原则、库优先三姿势、16 组件四层架构、数据流、组件归属（内核机制 vs 项目注入） |
| [02-runtime-core.md](02-runtime-core.md) | 三 transport/provider、profile、task 模型与状态机、result_file 四件套、registry、存储边界、安全门禁 |
| [03-orchestration-loop.md](03-orchestration-loop.md) | LoopEngine / State / Planner / Executor / Observer / Context / Memory / EventBus / Guardrail；loop 状态机与 connector |
| [04-contracts-and-security.md](04-contracts-and-security.md) | 三层配置、schema 版本化、shared-core 注意项、secret/日志边界、验证矩阵 |
| [05-integration-and-migration.md](05-integration-and-migration.md) | 入口与 SDK 边界、各项目迁移、现有资产映射、分阶段 P0-Pn 路线、已确认决策 |
| [06-directory-layout.md](06-directory-layout.md) | 仓库与包目录树、逐项说明（组件/层/阶段/用途）、两入口 + 上层 SDK |
| [../projects/](../projects/) | 各项目 overlay：`*.runtime.yaml` 供机器读取，`*.md` 只写人读差异 |

## 分阶段路线（详见 05）

- **P0 内核骨架**：ConfigManager（注入式）+ EventBus + Logger/Tracer + Persistence + 对外动词空壳 + 全组件接口 + fake provider + schema。
- **P1 执行闭环**：三 transport provider + Executor + 单步→多步 LoopEngine + Planner/Observer/StateManager + Guardrail 基础拦截。
- **P2 能力体系**：SkillManager + ToolManager + WorkspaceManager + ContextManager + Memory（接 backend 注入）。
- **P3 接入**：CLI 补齐 + 上层 SDK client；workbench / agent 切到内核（先吃自己狗粮），不启动 runtime HTTP server。
- **P4 loop 控制收口**：合并 workbench-loop + stock task 为统一 loop control，补 loop→runtime connector、确认门禁和评审态。
- **P5 stock 收敛**：`agent-runtime` 可按 Python 重构坐到 runtime+loop 上，领域 guardrail 留 stock。
- **P6 上层 agent-loop**：蒸馏 agent classifier/planner/executor 为最上层。

每阶段单独验证 + 确认；跨项目改动逐项目灰度；workbench 第一个吃狗粮证明价值再推 agent/stock。

## 项目标注规则

本仓被多项目共同补充。每条新增设计必须带项目归属，避免把单项目需求误升为共享约束：

```yaml
项目: shared-runtime | workbench | agent | stock | future:<project>
类型: shared-core | project-adapter | project-policy | migration-note | open-question
```

- `shared-runtime`：只写所有项目都遵守的机制、契约、安全边界、验证。
- 项目私有 prompt / 业务 schema / 领域 guardrail / 目录晋升规则：留 `projects/<id>.md`、`projects/<id>.runtime.yaml` 或项目仓库。
- 项目需求想升为共享规则：先在项目小节写「候选共享规则」，≥2 项目复用后再提升。
- 每次 request/result/run 必须显式带 `project_id`（runtime 的一级隔离维度）；runtime 实际加载以 `projects/<id>.runtime.yaml` 为准。
