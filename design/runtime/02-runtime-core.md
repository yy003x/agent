# 02 Runtime Core：transport、task、result、存储

> 项目: `shared-runtime`；类型: `shared-core`。L0/L1 的执行底座。

## 三 transport / provider

`provider` 是执行后端，不等于 runtime；runtime 是调度和边界层。三种 transport 同一 `run(task, profile) → result` 签名，`profile.transport` 决定走哪种。三种**一起进 P1**（user 决策），但 `llm_api` 默认不自动启用真实 API，先 mock/dry-run。provider 层包路径 `runtime.providers.{tmux,code_cli,llm_api}`。

**三种 transport 统一用文件状态同步**（下文「run 五件套」）：`tmux` 屏幕、`code_cli` stdout、`llm_api` 的 HTTP/SSE stream 都只是诊断输入，**最终都汇聚成 `result.json`**，没有特殊化通道。需要流式模型响应时，由 `llm_api` provider 或上层 SDK client 调远端 HTTP/SSE，并把 chunk/event 写入 `events.jsonl` / `output.log`，**不启动本地 runtime HTTP server**。profile / project overlay / runtime config 等人写配置用 **YAML**；runtime 写的状态产物（request/status/events/result）仍是 JSON/JSONL 原子追加或原子写。

| transport | 用途 | 蒸馏来源 | 注意 |
| --- | --- | --- | --- |
| `code_cli` | Codex / Claude / 其它本地 CLI，非交互一次性任务 | `agent/apps/workbench/runtime/process_cli_provider.py`、`stock/codexexec` | 结构化 exec，**不拼 shell string** |
| `tmux` | 交互式长驻会话、长任务、人工接管、调试 | `workbench-runtime`、`agent/.../tmux_provider.py` | 只交互/接管，**不是去 shell 化替代** |
| `llm_api` | OpenAI / Anthropic / GLM 等 API，结构化生成、分类、轻量推理 | `agent/apps/workbench/model_backends.py`、`stock/llm/provider.go` | provider/protocol 分离；secret 只走 env name |

### 去 shell 化优先于 tmux 化

主执行路径优先结构化进程调用：`executable + argv + stdin + cwd + env`。不要把命令拼成整段 shell string 交给 `/bin/zsh -lc`。确需 shell 时必须作为**显式 provider** 并记录原因。`tmux` 是交互/接管能力，不是去 shell 化替代。

### code_cli provider
- 结构化 `exec` + `stdin`；支持 timeout / cancel / interrupt；stdout/stderr 写诊断日志。
- **不从 terminal 文本判断成功**；profile 只存 `binary / default_args / env allowlist / result_contract`。

### tmux provider
- 只作交互式 session 和接管；`send` 记录摘要/字符数/hash，不记完整 prompt。
- `attach` 只返回接管命令，不自动开终端；`interrupt`（取消当前轮）与 `stop`（关 session）语义分清；不做 screen scraping；会话恢复依赖 registry，不依赖前端内存。

### llm_api provider
- provider 与 protocol 分离：`anthropic`（原生）/ `openai-compatible`；新增 provider 优先复用已有 protocol，OpenAI-compatible 也保留 provider 维度（headers/base_url/模型名/错误结构仍不同）。
- HTTP/SSE client 在 provider/上层 SDK 封装；runtime core 不提供 `serve`。stream/non-stream 都必须写入 `events.jsonl` 并汇聚成 `result_file`。
- secret 只通过 env name 引用，不写配置明文；tool-call 必须 allowlist；不绕过与 code_cli 相同的 result schema / audit / forbidden_actions 校验。

## profile 模型

```yaml
id: claude-api
transport: llm_api | code_cli | tmux
# llm_api: provider / base_url / api_key_env / protocol(openai|anthropic) / model
# code_cli: binary(codex|claude) / default_args / env_allowlist / stream_format
# tmux: binary / prompt_delivery / interactive
default_args: []
timeout_seconds: 0
result_contract: none | optional | required
policy_ref: <project policy id>
```

必带 `fake` profile（纯 sh）用于无外部依赖全链路 smoke。

## run 模型：session / turn / task

runtime 同时承载三类 run，不再把所有交互都压成 task：

| 类型 | 用途 | 是否必须 `result.json` | 典型来源 |
| --- | --- | --- | --- |
| `session` | tmux 长驻会话、人工接管、持续上下文 | 否；但必须有 registry/status/events | workbench runtime 会话 |
| `turn` | 会话中的一轮自动执行、GUI chat turn、LLM API 单轮 | 是 | agent GUI、workbench chat |
| `task` | 可排队、可重放、可评审的一次性任务 | 是 | workbench-loop、stock research |

最小 run 对象：

- 身份：`project_id`（必填语义字段，不靠 cwd 隐式判断）、`project_root`、`project_policy_ref`、`task_id`、`caller`。
- 执行：`provider_kind` / `provider_profile`、`cwd`、`input_ref` 或 `prompt_file`、`deadline_seconds`。
- 结果：`result_file`、`result_schema`。
- 边界：`allowed_actions`、`forbidden_actions`。
- 状态：`status`、`created_at` / `updated_at`。
- 类型：`run_type=session|turn|task`；`session_id` / `turn_id` / `task_id` 按类型必填。

### 状态机

```text
created -> queued -> running -> result_pending -> succeeded
                              -> failed
                              -> blocked
                              -> cancelled
```

`succeeded` **只能**由 result_file 校验通过后进入。执行进程退出 0 但 result_file 缺失，仍是 `failed` / `result_pending`，不算完成。终态依据 `result.json`、恢复依据 `status.json`、重放依据 `request.json`。

兼容读取旧实现里的 `done`，但共享 runtime 新写文件只写 `succeeded`。provider 原始状态可放在 `provider_status.raw_state`，不污染公共状态机。

## run 五件套契约

每个 `session` / `turn` / `task` run 目录固定落：

```text
request.json     # 调用方输入，已脱敏，可重放
status.json      # 当前状态，允许频繁更新
events.jsonl     # 事件流，只追加；LLM API stream chunk、状态变更、审批点都落这里
output.log       # provider stdout/stderr 诊断，只 tail 暴露
result.json      # turn/task 的唯一完成信号，原子写（temp + rename）；session 可没有
```

`stdout` / tmux 屏幕 / HTTP response body / SSE chunk 都只能当诊断输入或事件输入，**不能绕过 `result.json`**。result 最小结构：

```json
{
  "schema_version": 1,
  "task_id": "string",
  "status": "succeeded|failed|blocked|partial",
  "summary": "string",
  "artifacts": [],
  "errors": [],
  "validation": { "commands": [], "passed": false }
}
```

每个自动任务必须明确：result 路径、result schema、是否允许 partial、错误结构、artifact 列表、验证命令/函数。

### events.jsonl 最小事件

```json
{"schema_version":1,"event_id":"run-1","run_id":"...","run_type":"turn","type":"status.changed","ts":"...","seq":1,"data":{"status":"running"}}
```

- `seq` 在单 run 内单调递增；重放/上层 SDK 可按 `seq` 做断点续读。
- 事件只读广播，不承载命令；命令仍走 `RuntimeService` 动词。
- 默认事件不写完整 prompt / secret / header；LLM stream chunk 可被截断或脱敏，完整落盘需 capability。

### events.jsonl 最小事件

```json
{"schema_version":1,"event_id":"run-1","run_id":"...","run_type":"turn","type":"status.changed","ts":"...","seq":1,"data":{"status":"running"}}
```

- `seq` 在单 run 内单调递增；重放/上层 SDK 可按 `seq` 做断点续读。
- 事件只读广播，不承载命令；命令仍走 `RuntimeService` 动词。
- 默认事件不写完整 prompt / secret / header；LLM stream chunk 可被截断或脱敏，完整落盘需 capability。

## registry 与可恢复

- `runs/state/registry.json`（`sessions` + `tasks`）+ 文件锁，**on-demand 状态**（不跑常驻 monitor）。
- `task_id` 可由调用方传入；重复提交同一 `task_id` 默认返回已有状态，除非显式 `force`。
- 服务重启后必须能列出未完成任务，区分 `running` / `orphaned` / `result_pending`；tmux 会话经 registry + pid 回读恢复。
- `prune` 只清过期 runs，不碰调用方业务产物。

## 进程治理

`code_cli` / shell / tmux 启动的进程必须有完整生命周期：记录 pid / process group / cwd / argv / env allowlist；支持 timeout / cancel / interrupt / stop 且语义分清；取消优先温和中断再 kill process group；避免孤儿进程；**并发有上限**（默认本机串行或低并发，避免多个 Codex/Claude/LLM 抢资源）。

## 存储边界

runtime 只拥有自己的运行态和审计：

```text
runs/state/                          registry、task status、locks、pid
runs/logs/                           provider stdout/stderr、audit jsonl
runs/sessions/<project_id>/<session_id>/   session registry/status/events/logs
runs/turns/<project_id>/<turn_id>/         request、result、events、diagnostic logs
runs/tasks/<project_id>/<task_id>/         request、result、schema、events、diagnostic logs
runs/tmp/                            临时文件
runs/outputs/                        一次性诊断产物
```

调用方业务产物仍归项目：workbench 设计在 `workbench/design/`、本地事实在 `workbench/workspace/`；stock 业务数据/报告在 stock 自己边界内。**runtime 不替调用方把产物晋升为 memory/design/docs/workspace**；产物归属由项目 overlay 声明。

## 安全门禁

默认只允许本地读写 runtime 自己的 `runs/`。以下动作必须显式确认或由调用方传入已确认 capability：远端写入；删除/批量移动/覆盖用户文件；权限扩大；后台常驻服务 / launchd 安装 / 开机自启；读取 secret/cookie/private key/完整 JWT；执行 shell provider。

路径与权限必须显式：允许目录用绝对路径归一化后判断；拒绝 `..`、软链逃逸、隐藏 secret 路径；删除/覆盖/远端写入/Git push/launchd 安装是显式高危 capability。secret 与日志边界、schema 版本化见 [04](04-contracts-and-security.md)。

## 不做事项

- 不把 runtime 做成业务编排中心；不替代各项目 skill/design/workspace/report 规则。
- 不默认启动后台 daemon、不默认装 launchd、不直接接管 Git commit/push。
- 不用 tmux 输出判断任务完成；不把 OpenAI/Anthropic/GLM key 写入配置或日志。
- 不把某项目 overlay 当成其它项目默认行为。
