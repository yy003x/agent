# 04 契约、安全与验证

> 项目: `shared-runtime`；类型: `shared-core`。所有项目接入默认遵守；例外必须写到 `projects/<id>.md` 并说明原因/风险/验收。

## 单一核心，多入口适配

`lib` / `CLI` / 上层 SDK 都必须调用同一个核心服务对象或同一文件契约，不允许三套逻辑：

```text
RuntimeService
├── profiles()
├── run_task()
├── start_session() / send() / status() / logs()
├── cancel() / interrupt() / stop()
├── loop_status() / loop_plan() / loop_step()
└── prune()
```

否则跨项目复用很快变成三份 runtime。

runtime core 不提供 `serve` / HTTP server。需要 HTTP/SSE 的场景只出现在两处：`llm_api` provider 调远端模型 API；上层应用自行封装 SDK/HTTP client 并把状态写回 runtime 文件契约。

## 配置分三层

- **provider config**：协议、base_url、model、api_key_env、headers 模板。
- **profile config**：运行方式、默认参数、timeout、result_contract、policy_ref。
- **project overlay YAML**：项目根、runs_dir、允许目录、业务 policy、默认 profile。

共享 runtime 只内置通用默认值；项目私有路径、业务规则、真实 key 都来自 `projects/<id>.runtime.yaml` / env，不写死。ConfigManager 合并「共享默认 ← overlay YAML ← 环境变量 ← 本次 run 参数」，输出**脱敏后 effective config**，启动即 fail-fast（pydantic/dataclass 校验）。区分静态配置（启动定）与运行时参数（每次 run 传入）。

## secret 与日志边界

- 配置只保存 `*_ENV` 名称，不保存真实 secret。
- 日志只记 `api_key_env=set|missing`，不记 key 值；`headers_set` 记 header 名不记值。
- prompt 默认只记来源路径、字符数、hash；完整 prompt 是否落盘由调用方 capability 控制。
- 出错时清洗 provider 原始报错，避免第三方 SDK 把 request header 打出来。
- 凭证红线：key/token/cookie/private key/完整 JWT 不入配置文件、日志、事件、trace、错误信息。
- runtime 日志不得记录：完整高敏 prompt、远端身份明文、未脱敏环境变量。

## 路径与权限必须显式

runtime 默认只写自己的 `runs/`。任何项目文件写入必须通过 request 的 allowed paths/capabilities 声明：

- 允许目录用绝对路径归一化后判断；拒绝 `..`、软链逃逸、隐藏 secret 路径。
- 删除/覆盖/远端写入/Git push/launchd 安装是显式高危 capability。
- runtime 不替调用方做业务目录晋升（不把产物自动移到 `design/`/`workspace/`/`docs/`）。

## 可恢复与幂等

- `request.json` 是重放依据、`status.json` 是恢复依据、`events.jsonl` 是事件重放依据、`result.json` 是 turn/task 终态依据。
- `task_id` 可调用方传入；重复提交默认返回已有状态，除非显式 `force`。
- 进程重启能列未完成任务并区分 `running`/`orphaned`/`result_pending`；`prune` 只清过期 runs。

## 版本化

所有跨项目契约带版本，破坏性变更走新版本并保留一段兼容读取：

- `runtime_api_version`
- `request_schema_version`
- `result_schema_version`
- `profile_schema_version`
- `event_schema_version`
- `project_overlay_schema_version`
- `loop_schema_version`
- `event_schema_version`
- `project_overlay_schema_version`
- `loop_schema_version`

否则多项目迁移会互相卡住。

## 错误语义与契约稳定性

- 分层错误类型（配置错/调用错/执行错/护栏拒绝/用户取消），不裸抛、不吞。
- 对外动词与事件 schema 视为公共 API，变更走版本与迁移说明。
- 公共状态只写 `created|queued|running|result_pending|succeeded|failed|blocked|partial|cancelled`；旧 `done` 只兼容读取。
- 可测试性：Planner/Context 纯函数化；LLMGateway 可注入 fake；全链路支持「录制-重放」回归。

## 解耦自检（CI 静态检查）

- 内核任何模块 `grep` 不应出现 lark/kb/workbench/stock/具体业务名，也不应出现写死的绝对/相对仓库路径。
- 业务方只通过「注册 Skill/Tool + 注入 Config/Memory backend + 订阅 EventBus」接入，不改内核。

## 验证矩阵

每阶段至少保留（这些测试属于 `runtime/` 自己，不依赖任一业务项目）：

- config schema 校验、`doctor` 命令。
- `fake` provider 无外部依赖全链路单测。
- `llm_api` stub：OpenAI-compatible 与 Anthropic 的 payload/header/response。
- `code_cli` fake binary：timeout/cancel/result_file 缺失。
- `tmux` smoke：start/send/status/logs/interrupt/stop。
- result_file 原子写、events.jsonl 追加、result schema 校验、forbidden_actions/allowlist、路径越界、日志脱敏。
- loop control：dry-run 不派发、ready→running→reviewing/succeeded、blocked capability、旧 done 状态兼容读取。

P1 起最小命令面：

```bash
runtime doctor --json
runtime profiles list --json
runtime task run --provider fake --prompt-file <file> --result-file <file> --json
runtime task status <task_id> --json
```
