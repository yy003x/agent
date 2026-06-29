# Agent 工作台当前设计

`design/` 只保存当前项目事实和当前架构边界。可执行约定以仓库根目录的 `AGENTS.md`、`rules/`、`skills/`、`scripts/` 和 `apps/` 为准。

## 目标

本项目是本地运行的个人 Agent 工作台，面向图书运营、知识库检索、内容生成、任务执行和运行态管理。

核心链路：

```text
用户输入
  -> FastAPI API / React Web
  -> rules 路由与 skill 规程
  -> apps/workflows 或 runtime adapter 执行
  -> outputs / workspace / runs 写入结果
  -> finalize 与 agent_learning_review 做沉淀和候选晋升
```

## 当前 Owner

| 范围 | 当前 owner | 说明 |
|---|---|---|
| Web UI | `apps/web/` | React + TypeScript 工作台前端 |
| API | `apps/api/` | FastAPI 服务入口 |
| 业务 workflow | `apps/workflows/` | 内容交付流程和状态机 |
| 调度 | `apps/scheduler/` | 本地定时任务 |
| AgentRun runtime | `apps/agentrun/` | 通用 provider、session、task、result-file 契约和工作台适配 |
| skills | `skills/` | 可执行规程和本地脚本 |
| rules | `rules/` | 路由、安全和写入门禁 |
| config | `config/` | 示例配置和本地配置模板 |

## 应用目录

```text
apps/
├── api/          # FastAPI API
├── web/          # React + TypeScript Web UI
├── workflows/    # 业务 workflow
├── scheduler/    # APScheduler 本地任务
└── agentrun/     # AgentRun runtime 和工作台适配
```

新增功能必须放入当前应用目录或对应的 skill/runtime owner。

## Runtime 路径

API 通过 `apps/agentrun/` 执行本地 AgentRun。前端调用 provider-neutral API：

```text
GET    /api/runtime/runs
POST   /api/runtime/runs
GET    /api/runtime/runs/{run_id}
GET    /api/runtime/runs/{run_id}/logs
POST   /api/runtime/runs/{run_id}/send
POST   /api/runtime/runs/{run_id}/stop
```

交互式 provider 使用 AgentRun session；一次性任务使用 task result-file 契约。真实外部模型或远端写入必须经过本地配置和用户确认门禁。

## 内容与知识库

`skills/content-generate/scripts/content_runtime.py` 是内容生成与 KB CLI：

- `kb ingest`：资料入库
- `kb search`：检索本地 KB
- `kb index`：重建索引
- `kb gc`：清理可淘汰条目
- `kb related`：相关素材召回
- `text draft`：生成文案草稿
- `plan build`：生成组装计划
- `media assemble`：组装媒体包
- `publish package`：生成发布包

写入类命令必须带 `--allow-write`；发布只生成本地交付包，不调用外部平台发布 API。

## 自学习

`scripts/finalize.py` 负责记录任务完成后的本地摘要；`scripts/agent_learning_review.py` 负责生成和晋升候选。晋升长期规则、skill 或 memory 必须经过用户确认，并在晋升后运行 quick 校验。

## 验证入口

```bash
bash scripts/validate.sh --quick
```

完整环境具备外部依赖和素材后再运行：

```bash
bash scripts/validate.sh --e2e
```

Runtime 单独验证：

```bash
PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun doctor --json
PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun profiles --json
```

Web 验证：

```bash
cd apps/web && npm run typecheck && npm run build
```

## 文档规则

- 本目录只写当前状态，不写过期路线、替代实现或保留策略。
- 当前可执行事实优先于设计说明。
- 新增设计文档必须对应即将实现或已经实现的当前能力。
- 不复制 root `AGENTS.md`、`rules/`、`skills/` 或 `config/` 内容。
