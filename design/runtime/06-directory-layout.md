# 06 目录结构与逐项说明

> 项目: `shared-runtime`；类型: `shared-core`。把 01-05 落成可落地的 Python 目录树。每节点标注组件 / 层 / 阶段 / 用途。命名沿用决策（包 `runtime`、CLI `agent-runtime`）。
>
> 形态对齐 01：**runtime 只提供 lib + CLI，不内置 `serve`/HTTP server**；`llm_api` 的 HTTP/SSE 是 provider 调远端 LLM 的方式，上层 SDK 封装、把事件/结果写回文件契约。

## 仓库整体结构

```text
runtime/                              # git 仓根（live 共享依赖，pip install -e 给消费方）
├── README.md                         # 薄导航（指向 design/ 与 projects/）
├── pyproject.toml                    # 包元数据 + 依赖；entry_points 暴露 agent-runtime CLI
├── .gitignore                        # 忽略 runs/* / .venv / __pycache__
├── runtime/                     # 包本体（import runtime）—— 见「包内结构」
├── bin/agent-runtime                 # CLI 入口薄脚本（转 runtime.cli.main）
├── conf/                             # YAML 配置（人写）
│   ├── profiles.yaml                 # provider/profile 定义（含 fake）
│   └── runtime.yaml                  # 通用默认：runs_dir / env_prefix / 延迟 / policy 默认值
├── schemas/                          # 契约 schema（版本化，YAML 形式）
│   ├── run.schema.yaml               # session/turn/task 公共 run 对象
│   ├── result.schema.yaml
│   ├── event.schema.yaml             # events.jsonl 单条事件
│   ├── profile.schema.yaml
│   └── overlay.schema.yaml           # project overlay 机读校验
├── projects/                         # 项目接入
│   ├── README.md  workbench.md  agent.md  stock.md    # 人读差异
│   └── <id>.runtime.yaml             # 机读 overlay：project_root / runs_dir / 允许目录 / 默认 profile / policy
├── runs/                             # 运行态（不提交，仅 README）
│   ├── state/                        # registry.json + locks + pid（JSON 原子写）
│   ├── logs/                         # provider stdout/stderr + audit.jsonl
│   ├── sessions/<project_id>/<session_id>/   # tmux 长驻：status/events/output（可无 result）
│   ├── turns/<project_id>/<turn_id>/         # 单轮自动执行/LLM 单轮：五件套
│   ├── tasks/<project_id>/<task_id>/         # 可排队/重放/评审任务：五件套
│   ├── loop/<project_id>/                    # LoopControl：loop-state.json / events.jsonl / reviews/<id>.json
│   ├── tmp/  outputs/
│   └── README.md
├── tests/                            # runtime 自有测试（不依赖任何业务项目）
└── design/                           # 设计（README + 01-06）
```

约定：**conf / schemas / overlay 用 YAML（人写）；runs/ 下 request/status/result/registry 用 JSON、events 用 JSONL（机器原子写/追加）。** 每个 run 目录五件套：

```text
request.json  status.json  events.jsonl  output.log  result.json
```

## 包内结构 `runtime/`

```text
runtime/
├── __init__.py                  导出 AgentRuntime / RuntimeService / __version__
├── kernel.py                    AgentRuntime 门面：按 config 装配组件 + DI（Config/Logger/EventBus 注入）
├── service.py                   RuntimeService：lib 与 CLI 共用的唯一核心服务对象（无 web 假设）
│
├── core/                        # L0 基础设施 + 执行底座（P0/P1）
│   ├── config.py                ConfigManager：YAML 三层合并（默认←overlay←env←run 参数）+ 脱敏 effective + fail-fast
│   ├── run.py                   run 对象 + run_type（session/turn/task）+ 状态机
│   ├── result.py                五件套读写：request/status/events/output/result 原子写 + schema 校验
│   ├── events.py                events.jsonl：单调 seq 追加 + 脱敏 + 断点续读
│   ├── registry.py              runs/state/registry.json + 文件锁 + on-demand 状态 + prune
│   ├── rundir.py                run 目录定位（runs/{sessions,turns,tasks}/<project>/<id>/）唯一来源
│   ├── persistence.py           Persistence：Store 抽象（默认文件，可换 SQLite）
│   └── audit.py                 审计 jsonl + 脱敏
│
├── providers/                   # 三 transport —— 统一汇聚 result.json / events.jsonl（P1）
│   ├── base.py                  Provider 协议：run(run, profile) → result；进程治理（pid/pg/timeout/cancel/interrupt/stop）
│   ├── fake.py                  fake provider（纯 sh，无外部依赖 smoke）
│   ├── code_cli.py              结构化 exec（codex / claude -p）；蒸馏 agent/process_cli_provider + stock/codexexec
│   ├── tmux.py                  tmux 长驻会话 + 接管；蒸馏 workbench-runtime + agent/tmux_provider（吸收，决策 #5）
│   └── llm_api/                 llm_api transport（P1 进结构，真实 API 默认关）
│       ├── gateway.py           LLMGateway：provider/protocol 抽象、tool-use、超时/重试、token/成本计量
│       ├── stream.py            HTTP/SSE 调远端 LLM；chunk 写 events.jsonl，汇聚成 result.json
│       └── protocols/           openai.py（OpenAI-compatible）/ anthropic.py（原生）
│
├── guardrail/                   # L3 治理（机制，P1 基础）
│   └── guardrail.py             capability/allowlist/path 越界/审批机制；领域规则靠 policy 注入（不内置）
│
├── eventbus.py                  # 横切：异步事件总线（订阅者失败隔离；落 events.jsonl / 供上层 SDK 订阅）
├── logging.py                   # 横切：Logger/Tracer（run/turn/step span + 脱敏；不强依赖 OTel）
│
├── orchestration/               # L2 编排（P4 起，先留接口）
│   ├── loop.py                  LoopEngine：单 run 的 plan→guard→execute→observe→update + 对外动词 + 终止条件
│   ├── loopcontrol.py           LoopControl（P4）：多任务 发现→准入→派发→评审；只调 RuntimeService，不直接碰 provider
│   ├── state.py                 StateManager：显式运行态状态机（与 Persistence 分离，可断点续跑）
│   ├── context.py               ContextManager：组装每轮 prompt（纯函数式，token 预算）
│   ├── planner.py               Planner（P6）：结构化可校验输出，策略可插拔
│   ├── executor.py              Executor：编排循环里的动作执行（复用 providers，不与 core 单 run 执行重复）
│   └── observer.py              Observer：result/输出模式/LLM 评判，回灌 State/Memory
│
├── capabilities/                # L1 能力注入点（机制在内核，内容项目注入，P2）
│   ├── skills.py                SkillManager：注册/校验/路由/隔离机制（技能实现由项目注入）
│   ├── tools.py                 ToolManager：tool schema + tool-use 协议（工具实现由项目注入）
│   └── memory.py                Memory：接口 + backend 注入（KB 作为一种 backend；常驻模型）
│
├── sdk/                         # 上层 SDK（P3）—— 不是 runtime server
│   └── client.py                AgentRuntimeClient / LLMApiClient：调远端 LLM HTTP/SSE，流式回调，把 events/result 写回文件契约；供前后端分离工作台等上层应用用
└── cli/                         # CLI（P1）
    └── main.py                  agent-runtime <verb>：doctor / profiles / run / session / turn / task / prune
```

## 两入口 + 上层 SDK（无 runtime server）

`lib` 与 `cli` 都只构造并调用 `service.RuntimeService`（由 `kernel.AgentRuntime` 装配），不各自重写执行逻辑；上层 SDK 是调用方应用层，不拥有 runtime 状态：

```text
import runtime ─┐
agent-runtime CLI  ──┼──▶ RuntimeService ──▶ providers/* ──▶ runs/{sessions,turns,tasks}/.../result.json（唯一事实）
                     │                                          + events.jsonl（流式事件）
上层 SDK(AgentRuntimeClient)─ 调远端 LLM HTTP/SSE，回写文件契约 ┘
```

约束：内核**无 I/O 服务器假设**（不 import web 框架、不提供 `serve`）；SDK 只是远端 LLM 流式响应的适配层，把 chunk/event 落 `events.jsonl`、最终汇聚 `result.json`。

## 目录与阶段对照

| 阶段 | 落地目录 |
| --- | --- |
| P0 骨架 | `core/{config,run,result,events,rundir,persistence}` + `eventbus` + `logging` + `kernel/service` 空壳 + `conf/` + `schemas/` + `providers/fake` + `projects/*` |
| P1 执行闭环 | `providers/{code_cli,tmux,llm_api}` + `core/registry` + `guardrail` + `cli` + `orchestration/{loop,state,executor,observer,planner}` 单步→多步 |
| P2 能力体系 | `capabilities/{skills,tools,memory}` + `orchestration/context` |
| P3 接入 | `sdk`（上层 client）+ CLI 补齐；workbench / agent 接入（先吃自己狗粮），不启动 runtime server |
| P4 loop 收口 | `orchestration/loopcontrol` 接 stock task + workbench-loop connector + 确认门禁/评审态 |
| P5 stock 收敛 | stock `agent-runtime` 按 Python 重构接入（领域 guardrail 留 stock） |
| P6 agent-loop | `orchestration/planner` + 上层 classifier/executor 蒸馏自 agent |

## 边界自检（CI）

- `runtime/` 任何模块 `grep` 不应出现 `lark/kb/workbench/stock/选股` 等业务名，或写死的绝对/相对仓库路径。
- 业务能力只经 `capabilities/*` 注册 + `conf`/`overlay` 注入 + `eventbus` 订阅接入，不改包内核。
- 单向依赖：`sdk` / `cli` 只 import `service`；`core/providers` 不反向依赖 `sdk/cli/orchestration`。
