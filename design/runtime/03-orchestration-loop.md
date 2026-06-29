# 03 编排与 Loop：组件职责与状态机

> 项目: `shared-runtime`；类型: `shared-core`。L1 能力层 + L2 编排层 + L3 治理 + 横切，逐组件给职责/注意项/复用来源。

## L1 能力 / 网关层

### LLMGateway（大模型调用网关）
- **职责**：统一多 provider 的对话/工具调用入口，屏蔽 Anthropic 原生与 OpenAI-compatible 差异。
- **注意**：默认走最新 Claude（`claude-opus-4-8` / `claude-sonnet-4-6`），由 Config 注入可切换；统一支持流式、tool-use、system/multi-turn、超时/重试/退避、并发限流；**token/成本计量**与 **prompt 缓存**作为一等公民入 trace；失败分类（限流/超时/拒答/截断）暴露给上层，不吞异常；网关只「调用」，**不做** prompt 拼装（属 Context）与动作决策（属 Planner）。
- **复用**：`agent/apps/workbench/model_backends.py`（OpenAI-compatible 多后端 + 凭证解析）整体迁入改注入式。

### SkillManager（技能管理）
- **职责**：发现/注册/校验/路由「技能」（prompt + 流程 + 脚本的高层能力单元）。
- **注意**：技能目录**由 Config 注入**；元数据 schema 化；加载隔离失败（坏技能不拖垮内核），提供 `doctor`/`list`；技能可调工具、工具不感知技能；路由策略（关键词/语义/显式）可插拔。
- **注入边界**：内核只提供注册/路由/隔离机制；技能实现是**项目内容**，不进内核。

### ToolManager（工具管理）
- **职责**：注册/暴露原子工具（函数、shell、外部 CLI、tmux、文件读写），生成给 LLM 的 tool schema，执行工具调用。
- **注意**：工具 = 纯能力 + 显式 schema，幂等性/副作用在元数据标注供 Guardrail 判级；危险工具（写/删/远端/执行）必须可被 Guardrail 拦截并要求审批；tmux/交互式工具委托 transport provider，不在此重写。
- **注入边界**：tool 实现由业务方注入，是内核业务无关的关键扩展点；内核只过 tool-use 协议。

### Memory（记忆系统）
- **职责**：短期（会话内）+ 长期（跨会话）记忆，向 Context 提供召回。
- **注意**：长期记忆若用向量库（embedding + reranker + 向量存储），**模型与连接必须常驻**（呼应库优先），切勿每次召回重载；召回/写入分离，写入可异步（经 EventBus）、召回低延迟同步；记忆 schema 与 Persistence 解耦；提供「记忆类型」（事实/偏好/反馈/引用）。
- **注入边界**：内核只定义 Memory 接口；具体 backend（如 KB 检索栈）作为一种 **Memory backend 注入**——KB 是业务数据，Memory 是机制。

### WorkspaceManager（工作区管理）
- **职责**：管理运行时文件工作区（输入/产物/临时/run 目录）的定位与生命周期。
- **注意**：工作区根**由 Config 注入**；runtime 自己 runs/logs 按 `project_id/run_id` 分区，调用方业务产物归项目 overlay 声明目录；提供路径解析/隔离/清理（GC），多 run 并发目录隔离；是 Persistence/Executor 产物路径的**唯一来源**，避免各组件各拼路径。

## L2 编排层

### ContextManager（上下文管理）
- **职责**：组装每轮 LLM 输入（system + 历史 + Memory 召回 + 能力清单 + 当前观察）+ 窗口管理。
- **注意**：token 预算管理（截断/摘要/分块可配置，与 LLMGateway 计量联动）；上下文构造**纯函数式**（给定状态产出 prompt，便于测试与重放）；能力清单由 Skill/ToolManager 提供，Context 只编排不发明。
- **注入边界**：薄「会话消息缓冲」入内核；富上下文策略（RAG/记忆注入细节）可由项目配置。

### StateManager（状态管理）
- **职责**：维护单次 run/会话的运行态（阶段、变量、待办、错误、计数器）。
- **注意**：**显式状态机**（idle/planning/executing/observing/blocked/succeeded/failed），转移有日志；状态与持久化分离（State 管语义转移，Persistence 管落盘 → 崩溃可恢复、断点续跑）；同 run 单写、跨 run 隔离。

### LoopEngine（循环执行引擎）
- **职责**：驱动 plan→guard→execute→observe→update 主循环，管理终止条件与对外动词。
- **对外稳定动词**（三姿势共用契约）：`start` / `run`（同步等结果）/ `step` / `status` / `logs` / `send`（注入消息）/ `interrupt`（取消当前轮、保会话）/ `stop` / `cancel`。语义对齐 `workbench-runtime` 已验证契约。
- **注意**：终止条件可配（步数上限、预算上限、Guardrail 否决、Planner 判完成）防无限循环；全程可取消/中断，信号穿透到 Executor 子进程；每步 emit 事件到 EventBus。
- **复用**：`workbench-loop` 的循环控制经验。

### Planner（任务规划）
- **职责**：基于上下文经 LLMGateway 产出下一步（动作/子计划/终止）。
- **注意**：输出**结构化可校验**（动作 schema），解析失败可重试/纠偏，不让自由文本直接驱动执行；策略可插拔（单步 ReAct / 计划-执行 / 树搜索），默认从最简单单步开始；只决策不执行、不碰副作用。
- **复用**：`agent/apps/agent/orchestrator.py` + `brain.py`（已成形，作上层参考；P6 才进核心）。

### Executor（动作执行）
- **职责**：执行 Planner 决策——调工具/技能、跑外部 CLI、流式捕获输出。
- **注意**：执行前**必过 Guardrail**；执行中流式回传（经 Observer/EventBus）；可被 interrupt；外部进程/tmux 直接复用 transport provider（含权限拦截 + terminate），**不重写**；区分「交互式会话执行」（tmux 长驻）与「一次性任务执行」（result_file 契约）。
- 注意与 runtime core 的关系：runtime core 已是**单 task 执行器**；这里 Executor 指**编排循环里的动作执行**，不要与 core 重复造。

### Observer（结果观察）
- **职责**：解析执行输出/产物，提取结构化结果/状态/错误信号，回灌 State/Memory。
- **注意**：观察规则可插拔（result_file 解析 / 输出模式匹配 / LLM 评判）；区分「进度信号」「最终结果」「异常/权限阻断」，分别驱动 LoopEngine。

## L3 治理（横切）

### Guardrail（安全与边界控制）
- **职责**：动作执行前/后施加策略——权限、预算、内容、速率、审批。
- **注意**：**拦截点统一**，所有副作用动作必经 Guardrail（Executor 调用前），不允许绕过；策略分级（自动放行 / 需审批 / 直接拒绝），高风险（写/删/远端/执行）默认需审批；与 LLMGateway 计量联动做**预算护栏**；护栏决策入 trace，审批请求/结果经 EventBus。
- **红线**：内核只提供**机制**；**领域 guardrail（stock 风控/投顾合规）留项目**，经 policy 注入，不进内核。

## 横切

### EventBus（事件系统）
- **职责**：内核内异步事件总线，解耦生产者（各组件）与消费者（UI / 日志 / trace / 审批）。
- **注意**：事件 schema 版本化、向后兼容；订阅者失败隔离（不回压主循环）；事件同步追加到 `events.jsonl`，上层 SDK/GUI 可自行 tail 或转推；事件**只读广播**，命令走显式接口，避免隐式耦合。

### Logger/Tracer（日志与追踪）
- **职责**：结构化日志 + 分轮次 trace（turn → step → tool call 的 span 树）。
- **注意**：trace 以 `run_id / turn_id / step_id` 串联，统一 schema；默认本地落盘 + 可订阅，为 OpenTelemetry 留接口不强依赖；**脱敏**：prompt/响应可选采样，凭证与 PII 永不入 trace。

## Loop 控制设计（P4 收口）

runtime core 的 LoopEngine 管「单 run 的 turn 循环」；更上层有一个 **LoopControl**，管「多任务的发现→准入→排程→执行→评审→收口」。它蒸馏 `workbench-loop` 的 interactive-serial 控制面和 `stock/task` 的结构化任务状态，不自治扩大权限。

```text
discovered -> triaged -> ready -> running -> reviewing -> succeeded
                            |        |          |
                            └────────┴──────────┴──> blocked
                                     └──────────────> cancelled
```

### LoopControl 职责

- **任务准入**：校验 `project_id`、owner、风险等级、输入引用、allowed paths、capability、done_when/block_when。
- **计划分解**：把高层 loop item 拆成一个或多个 runtime `task` / `turn`，但不直接执行危险动作。
- **派发执行**：`ready` → `RuntimeService.start_task(...)` → 记录 `runtime_ref` → `running`。
- **进度观察**：tail `status.json` / `events.jsonl` / `result.json`，不解析 tmux 屏幕作为完成信号。
- **结果评审**：result schema、validation、artifact、policy 都通过后进入 `succeeded`；partial 或需确认进入 `reviewing` / `blocked`。
- **人在环门禁**：远端写入、Git push、批量删除、权限扩大、真实 stock research/GLM 等高风险动作停在 `blocked` 或 `reviewing`，等待调用方显式 capability。
- **恢复与清理**：进程或 CLI 重启后从 loop store + runtime registry 恢复；`prune` 只清 runtime 运行态，不删除业务产物。

### Loop item schema

```json
{
  "schema_version": 1,
  "project_id": "workbench",
  "id": "loop-...",
  "title": "string",
  "source": "manual|scheduler|api|agent",
  "owner": "workbench-design|workbench-execute|stock-runtime",
  "state": "discovered|triaged|ready|running|reviewing|succeeded|blocked|cancelled",
  "priority": "low|normal|high",
  "risk": "low|medium|high",
  "input_refs": [],
  "done_when": [],
  "block_when": [],
  "runtime_refs": [],
  "review": {"required": true, "status": "pending", "notes": []}
}
```

### Connector 合同

LoopControl 不直接操作 provider，只调用 `RuntimeService`：

```text
ready
  -> start_task(project_id, prompt_file/input_ref, profile, result_schema, capabilities)
  -> running(runtime_ref)
  -> observe(result.json + status.json + events.jsonl)
  -> reviewing | succeeded | blocked | failed
```

状态映射：

| runtime status | loop state | 说明 |
| --- | --- | --- |
| `succeeded` | `reviewing` 或 `succeeded` | 需要人工/策略评审则先进 `reviewing` |
| `partial` | `reviewing` | 允许 partial 时保留产物并给 reviewer |
| `blocked` | `blocked` | 缺 capability、审批、依赖或输入 |
| `failed` | `blocked` | 默认停下，不自动重试；项目可配置有限 retry |
| `cancelled` | `cancelled` | 用户或上层取消 |

### 默认执行模型

- 第一版固定 `interactive-serial`：同 project 默认串行，避免多个 Codex/Claude/LLM 抢资源。
- `--dry-run` 只输出将要派发的 runtime request，不写 task result，不触发 provider。
- `--apply` / `--allow-write` 只允许写 loop store 和 runtime runs；项目业务写入仍需 capability。
- 不接 scheduler 生产任务作为首批迁移目标；workbench console 先读状态 + 跑 1 个 fake task 证明闭环。

### 持久化

```text
runs/loop/<project_id>/loop-state.json      # 当前 loop items
runs/loop/<project_id>/events.jsonl         # loop 状态变更
runs/loop/<project_id>/reviews/<id>.json    # review 记录
```

P4 可选择 SQLite，但第一版优先 JSON/JSONL，便于 diff、恢复和跨项目审查。
