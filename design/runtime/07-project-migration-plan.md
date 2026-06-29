# 07 三项目接入迁移计划与审查结论

> 项目: `shared-runtime`；类型: `migration-note` + `project-adapter`。实现状态: ✅ P0-P5 已完成（runtime baseline + workbench shim + agent adapter + loop dispatch + stock subprocess adapter）；📌 P6 直接清理旧实现的方案已确认，待执行；真实 Codex/Claude/LLM 默认继续关闭。本文件是三项目迁移计划的事实源。

## 结论

推荐以 `~/agents/runtime` 作为唯一共享 Agent runtime 实现，先补齐并冻结它的真实 `tmux` / `code_cli` / `llm_api` 文件契约，再按 `workbench -> agent -> stock` 灰度迁移。`apps/workbench-runtime` 不再作为长期实现 owner，迁移期只保留兼容 shim；`~/agents/agent` 保留图书运营工作台 UI 和业务 session；`~/agents/stock` 保留 Go API、SQLite、Evidence、Report 和投顾 guardrail，只把通用执行和 result-file 契约接入共享 runtime。

本轮复核结论：总体方向正确，边界也正确；P0/P1 的 runtime 仓内门禁已经补齐：`code_cli` 有 result-file wrapper、`tmux` session 动词可用、`turn` 是一等 API、registry 写入有锁且同 run_id 默认幂等。P2-P5 已完成首轮消费方接入：workbench runtime CLI 转 shared shim，agent `MainRuntime` 通过 shared adapter 执行 turn/session，workbench loop 可显式 dispatch 到 shared `LoopControl`，stock mock research 先调用 shared runtime 再在 Go adapter 内完成 Evidence/guardrail/SQLite。P6 已确认采用直接清理旧实现策略；真实 Codex/Claude/LLM 不进入 P6 默认验收，继续保持 mock/off 或显式拒绝默认执行。

设计深度: full(跨 repo runtime 迁移 + Agent 资产变更 + 运行态路径 + tmux/CLI/LLM provider + stock 合规边界)。

## 已确认方案

- 推荐路径: `~/agents/runtime` 做 shared core，三项目通过 project overlay 和 adapter 接入；不把业务逻辑上移进 runtime。
- 本轮执行范围: P0-P5 已完成；P6 方案已确认，下一阶段直接清理旧实现，不保留长期 shim-only 冻结窗口。
- 阶段顺序: P0 baseline 门禁 -> P1 shared runtime 执行闭环补齐（真实 `tmux`、`code_cli` 命令契约、`turn` API、registry 锁） -> P2 workbench shim 迁移 -> P3 agent provider 迁移 -> P4 loop 控制统一 -> P5 stock subprocess/adapter 接入 -> P6 清理旧 runtime。
- P6 清理策略: 直接删除已由 shared runtime 接管的旧 provider / 旧 runtime owner；只保留必要迁移说明，不把旧实现继续作为可执行 owner。
- 真实 provider 策略: 真实 Codex/Claude/LLM API 继续默认关闭；P6 只验证 fake、`code-cli-wrapper`、mock/off 路径。
- 暂不做事项: 不把 runtime 做成本地 HTTP server; 不迁移 `lark-runtime` / `kb-runtime` / scheduler; 不把 stock 投顾合规、SQLite 事实写入、Evidence/Report schema 放进 shared core; 不默认启用后台 daemon/launchd。
- Minimality Check: 不新建第四套 runtime; 只把三套已有重叠能力收敛到 `~/agents/runtime`, 各项目保留自己的 UI/API/业务 adapter。
- 验证摘要: shared runtime 单测 + doctor/migration/review; workbench runtime/console/loop 语法与 doctor; agent 在 Python 3.11+ 下 quick validate; stock Go test + mock smoke。
- 回滚摘要: P6 通过独立提交执行；失败时恢复被删除的旧入口文件或回滚该提交；历史 `runs` 数据不删除。
- 用户确认状态: P0-P5 confirmed and implemented；P6 cleanup strategy confirmed and pending implementation。

## 正确性审查与遗漏补齐

| 发现 | 影响 | 处理结论 |
| --- | --- | --- |
| `conf/profiles.yaml` 里的裸 `codex-cli` / `claude-cli` / `stock-research` 不能保证写 `AGENT_RUNTIME_RESULT_FILE` | 直接作为迁移默认 profile 会卡住或失败 | ✅ P1 已新增 `code-cli-wrapper` 作为安全 smoke profile；裸 CLI 保留为 experimental |
| `tmux` provider 缺少 session 动词 | workbench / agent 不能迁交互 session | ✅ P1 已补 `start/send/logs/interrupt/attach/stop/prune` |
| `turn` 不是一等 API | agent GUI chat turn 会被迫伪装成 task | ✅ P1 已补 `turn run/status/logs/list` |
| `Registry.record()` 无文件锁和幂等 | 并发时可能丢 registry，重复 run_id 可能覆盖 | ✅ P1 已加 `fcntl.flock`，同 run_id 默认返回 existing，`--force` 才重跑 |
| overlay 字段叫 `allowed_providers`，实际检查的是 `profile.transport` | 读者容易把 provider profile 与 transport 混淆 | ✅ P0 已新增 canonical `allowed_transports`，兼容读取旧 `allowed_providers` |
| `llm_api` 当前仍以 mock/off 为默认 | 真实 provider/protocol/base_url/model/api_key_env 仍不能默认启用 | 真实 API 继续留在上层 SDK/client；shared runtime 不启动 HTTP server |
| shared result 最小校验只检查公共字段，stock 业务 schema 不能放进 shared core | stock 业务报告可能绕过领域 guardrail/schema | ✅ P5 已在 Go adapter 中保留 Evidence 校验、guardrail、SQLite 写入；shared core 只做执行底座 |
| `README.md` 仍写“设计阶段（P0），先设计后实现” | 状态描述会误导实现方 | ✅ P0 已修正文档状态 |
| runtime 是独立 repo 且需要 baseline | 三项目无法可靠 pin 依赖，迁移 diff 难回滚 | ✅ 恢复后 P0 baseline commit: `12c54ba feat: 完成共享 runtime P0-P8 基线`；P1 commit: `de6a727`；P2-P5 commit: `c48259d` |
| `pyproject.toml` 要求 Python `>=3.11`，系统 `python3` 是 3.9.6 | 直接跑 agent quick validate 会失败 | ✅ P3 使用临时 PATH 指向 Python 3.14 验证通过；长期仍建议固定项目 venv |

## 背景与目标

- 用户目标: 看清 `~/agents/runtime` 的实现, 把当前 apps 下的 runtime 迁移到统一实现, 且 `~/agents/agent` 和 `~/agents/stock` 后续也使用同一 runtime。
- 当前问题: 三处已有 runtime 能力重叠但形态不同:
  - workbench: `apps/workbench-runtime` 是真实 tmux named-session owner。
  - agent: `apps/workbench/runtime` 是 GUI 工作台 provider 集合, 含 tmux task、process CLI、LLM API。
  - stock: `agent-runtime` 是 Go 业务 runtime, 强绑定任务状态、SQLite、guardrail、GLM/report。
- 预期收益: 统一 provider/文件契约/status/result/schema/doctor/review; 降低跨项目重复实现; 保留业务边界和安全门禁。

## 现状

- shared runtime:
  - 位置: `~/agents/runtime`
  - 包: `runtime`
  - 入口: `scripts/agent-runtime`
  - 配置: `conf/runtime.yaml`, `conf/profiles.yaml`, `projects/{workbench,agent,stock}.runtime.yaml`
  - 已有能力: `RuntimeService`, CLI `doctor/profiles/task/turn/session/capabilities/loop/migration/review`, `fake`, `code_cli`, mock `llm_api`, real tmux session lifecycle, `LoopControl`, `MigrationProbe`, `RuntimeReview`
  - 缺口: P6 旧 runtime 清理尚未执行；真实 `llm_api` provider 继续默认 mock/off，不进入 P6 默认验收。
- workbench:
  - `apps/workbench-runtime` 已转为 shared runtime shim, 对外保留旧 CLI。
  - `apps/workbench-console` 继续代理 `/api/runtime/*` 到 `apps/workbench-runtime/bin/workbench-runtime`, 不直接操作 tmux。
  - `apps/workbench-loop` 已支持显式 dispatch 到 shared `LoopControl`, 默认仍保留 dry-run 安全行为。
- agent:
  - `apps/workbench/runtime` 的 `MainRuntime`/`external_cli.py` 已接 shared runtime adapter, 旧 provider 保留为兼容层。
  - UI/API 在 `apps/workbench/server.py`, chat turn 使用 `runs/workbench/sessions/<chat>/turns/<turn>/result.json` 作为回填信号。
  - 业务 session、图书运营 prompt、config、前端 API 不应迁入 shared runtime。
- stock:
  - Go `agent-runtime` 已通过 shared runtime subprocess client 接入 mock research 执行底座。
  - `codexexec.RunReal` 当前明确拒绝真实执行, 真实 Codex 需要单独确认。
  - `agent-api` 进程内调用 Go runtime client, 同步 API 禁止直接跑 Codex exec。

## 证据清单

| 类型 | 路径 / 命令 | 结论 | 状态 |
| --- | --- | --- | --- |
| shared runtime 设计 | `~/agents/runtime/design/{01,02,05}*.md` | 已定义库优先、无 runtime server、三 transport、run 五件套、三项目 overlay | 已回读 |
| shared runtime 代码 | `~/agents/runtime/runtime/service.py`、`providers/*.py` | `fake`/`code_cli`/`tmux` 已能走文件契约；`llm_api` 仍默认 mock/off | 已回读 |
| shared runtime 配置 | `~/agents/runtime/projects/*.runtime.yaml` | 三项目 overlay 已存在; stock 禁用 tmux; workbench/agent 允许 tmux + llm_api | 已回读 |
| workbench runtime | `apps/workbench-runtime/lib/cli.py` | 旧 CLI 外观已转 shared runtime shim，运行态写入 shared runs | 已回读 |
| workbench console | `apps/workbench-console/lib/cli.py` | 控制面只调用 workbench-runtime CLI | 已回读 |
| workbench loop | `apps/workbench-loop/lib/cli.py` | 保留 dry-run，显式 `--allow-dispatch` 时调用 shared `LoopControl` | 已回读 |
| agent runtime | `~/agents/agent/apps/workbench/runtime/*.py` | 三 provider + GUI runtime facade + result_file turn | 已回读 |
| agent UI | `~/agents/agent/apps/workbench/server.py` | 前端 API/session/turn 归 agent 业务层 | 已回读 |
| stock runtime | `~/agents/stock/agent-runtime/**` | Go 业务 runtime; SQLite/guardrail/report 不应上移 shared core | 已回读 |
| 当前验证 | `agent-runtime doctor/migration/review`, runtime unittest, workbench doctor, stock go test | shared/workbench/stock 轻量验证通过; agent quick validate 受 Python 3.9.6 阻塞 | 已执行 |

## 设计前提

- 目录层级约束:
  - shared core: `~/agents/runtime/runtime/`, `conf/`, `projects/`, `schemas/`, `tests/`
  - workbench adapter/shim: `~/agents/workbench/apps/workbench-runtime/`
  - agent adapter: `~/agents/agent/apps/workbench/runtime/` 或新增薄 wrapper, UI 仍在 `apps/workbench/`
  - stock adapter: `~/agents/stock/agent-runtime` 先通过 subprocess client 调 shared CLI, 后续再评估 Python 化
- Git / runs / workspace / outputs 边界:
  - shared runtime 自有运行态: `~/agents/runtime/runs/{sessions,turns,tasks,state,logs,tmp,outputs}`
  - workbench 业务产物: `~/agents/workbench/design|workspace|outputs|runs`
  - agent 业务产物: `~/agents/agent/workspace|outputs|runs`
  - stock 业务数据: `~/agents/stock/data|storage|runs|outputs`
- 安全红线:
  - runtime 配置只存 env name, 不存 secret。
  - 远端写入、Git push、批量删除、权限扩大、真实 stock Codex/LLM 都要显式确认。
  - terminal output 只作诊断, 成功只看 `result.json` 校验。
- 当前工作树约束:
  - `~/agents/runtime` 已有恢复后的 baseline commit `12c54ba`; P1 commit 为 `de6a727`, P2-P5 commit 为 `c48259d`; 三项目只依赖提交后的 runtime。
  - 三项目迁移提交必须 path-limited，不纳入无关工作区改动。

## Minimality Check

- 是否需要新增 runtime: 否, `~/agents/runtime` 已是目标 shared core。
- 是否需要保留 `apps/workbench-runtime`: P0-P5 迁移期作为 CLI 兼容 shim；P6 直接退役旧实现 owner，不保留长期 shim-only 冻结窗口。
- 是否需要改 agent UI/API: 首轮不需要改 API surface; 只替换 `MainRuntime` 下层 provider。
- 是否需要重写 stock 为 Python: 首轮不需要; Go 侧通过 CLI/JSON 接入 shared runtime, 业务逻辑继续 Go。
- 有意延后项:
  - `llm_api` 真实 stream/provider 继续默认关闭，后续单独设计安全 smoke。
  - stock Python adapter 只作为后续优化, 不是 P6 门禁。
- 不允许压缩的边界: stock 投顾合规、schema 校验、result_file 完成信号、workbench Git/远端写入确认、agent 发布不自动化、secret 脱敏。

## 目标架构

```text
~/agents/runtime
  runtime/
    service.py                  # RuntimeService, 唯一业务无关服务面
    providers/
      code_cli.py               # 结构化 argv + stdin + result_file
      tmux.py                   # 真实 tmux named-session + run/task 能力
      llm_api.py                # API client + events/result, 默认 mock/off
    loop.py                     # LoopControl 机制
    adapters/
      stock.py                  # stock adapter 只做边界桥接, 不含业务 schema
  conf/
    runtime.yaml
    profiles.yaml
  projects/
    workbench.runtime.yaml
    agent.runtime.yaml
    stock.runtime.yaml
  runs/
    sessions/<project_id>/<session_id>/
    turns/<project_id>/<turn_id>/
    tasks/<project_id>/<task_id>/

~/agents/workbench
  apps/workbench-runtime/       # P0-P5 迁移期 shim; P6 退役旧实现 owner
  apps/workbench-console/       # 本地 GUI/API, 只调 shared runtime 路径
  apps/workbench-loop/          # 已接 shared LoopControl dispatch

~/agents/agent
  apps/workbench/server.py      # UI/API/session/turn 不迁入 runtime
  apps/workbench/runtime/       # 迁移为 shared runtime adapter

~/agents/stock
  agent-api/                    # 保持 Go HTTP API
  agent-runtime/                # 保持 Go 业务 adapter + store + guardrail
  runs/data/storage/            # 业务事实和报告仍归 stock
```

## Contract 决策

| 主题 | 决策 |
| --- | --- |
| runtime 形态 | 库优先 + CLI, 不启动 shared runtime HTTP server |
| 项目身份 | 所有 run 必须显式带 `project_id` |
| 运行态事实源 | `request.json`, `status.json`, `events.jsonl`, `output.log`, `result.json` |
| 完成信号 | `turn/task` 只以 `result.json` 校验通过为成功 |
| 状态名 | shared 新写 `succeeded/failed/blocked/partial/cancelled`; adapters 兼容旧 `done/success` |
| tmux | 分成 `session` 和 `task/turn`; named interactive session 不强制 result, task/turn 必须 result |
| turn API | `turn` 是一等 run 类型, 不用长期伪装成 task; agent GUI 迁移前必须有 `run_turn/status/logs` 或明确 adapter 兼容 |
| registry | `registry.json` 写入必须加锁并支持 task_id 幂等、force、orphan/result_pending 检测 |
| code_cli 命令契约 | profile 不能只写裸 `codex`/`claude`; 必须保证子进程能写 `AGENT_RUNTIME_RESULT_FILE` 或由 wrapper 转成 result |
| profile | YAML `profiles.yaml` + project overlay allowlist; 不把业务 prompt 写进 profile |
| secrets | 配置存 env name; 日志只写 `*_set`、provider id、model/base_url 等脱敏信息 |
| stock | 业务 guardrail/schema/SQLite 不进 shared core; shared core 只执行和保存通用 run 文件 |

## 迁移映射

| 现有资产 | 目标归属 | 迁移方式 |
| --- | --- | --- |
| `workbench/apps/workbench-runtime/lib/cli.py` tmux lifecycle | `runtime.providers.tmux` + `RuntimeService.session_*` | 蒸馏 named session、registry lock、pipe-pane、paste-buffer、prompt-history |
| `workbench/apps/workbench-runtime task` | `RuntimeService.task_*` + tmux session task bridge | 保留旧 CLI 参数, shim 转 shared CLI/API |
| `workbench/apps/workbench-console` | workbench UI | 先不改 API; 后续把 CLI 路径从 app shim 切 shared CLI |
| `workbench/apps/workbench-loop` | shared `LoopControl` + workbench adapter | P4 已迁; 默认 dry-run, 显式 `--allow-dispatch` 才派发 |
| `agent/apps/workbench/runtime/process_cli_provider.py` | `providers.code_cli` | 合并 output mode、last-message、Codex/Claude 参数策略 |
| `agent/apps/workbench/runtime/tmux_provider.py` | `providers.tmux` | 合并 pane identity、idle detector、result-stable、prompt auto-submit |
| `agent/apps/workbench/runtime/llm_api_provider.py` + `model_backends.py` | `providers.llm_api` + profile/backend resolver | 保留 provider/protocol 分离; API key 只走 env |
| `agent/apps/workbench/server.py` sessions/turns | agent 业务层 | 不迁; adapter 返回原 UI 需要的 runtime_meta |
| `stock/agent-runtime/internal/codexexec` | stock adapter + shared `code_cli` task | 先保留 mock/guardrail; real Codex 需确认后走 shared runtime |
| `stock/agent-runtime/internal/store/guardrail/llm` | stock 项目 | 不迁入 shared core |

## 分阶段迁移计划

### P0: Shared Runtime Baseline

- 范围:
  - ✅ 确认恢复后的 `~/agents/runtime` baseline commit `12c54ba` 作为迁移基线。
  - ✅ 补齐 README 状态: 代码已存在, 不能再写成纯 P0 设计阶段。
  - 固定 `runtime` public API、CLI verbs、run 五件套、project overlay schema。
  - ✅ 明确 `allowed_transports` 是 canonical transport allowlist，并兼容读取旧 `allowed_providers`。
  - 固定 Python 版本门槛: runtime 和 agent 接入验证使用 Python 3.11+。
  - ✅ 明确 baseline pinning: 三项目迁移必须依赖 runtime commit, 不依赖未提交 worktree。
- 文件:
  - `~/agents/runtime/README.md`
  - `~/agents/runtime/design/*.md`
  - `~/agents/runtime/runtime/**`
  - `~/agents/runtime/tests/**`
- 不做:
  - 不改三项目消费方。
  - 不启用真实 tmux 替换。
- 验证命令:
  - `PYTHONPATH=/Users/yang/agents/runtime /Users/yang/agents/runtime/scripts/agent-runtime doctor --json`
  - `PYTHONPATH=/Users/yang/agents/runtime /Users/yang/agents/runtime/scripts/agent-runtime migration report --json`
  - `PYTHONPATH=/Users/yang/agents/runtime /Users/yang/agents/runtime/scripts/agent-runtime review --json`
  - `PYTHONPATH=/Users/yang/agents/runtime python3 -m unittest discover -s /Users/yang/agents/runtime/tests`
- 结果:
  - doctor/review ok。
  - 三项目 overlay 都能被 probe。
  - runtime repo 已形成恢复后的 baseline commit `12c54ba`。

### P1: Shared Runtime 执行闭环补齐

- 范围:
  - ✅ 修正 `code_cli` 真实命令契约: `code-cli-wrapper` profile 能产生 `AGENT_RUNTIME_RESULT_FILE`; 退出 0 但缺 result_file 必须失败。
  - ✅ 将 tmux session 生命周期接入 `runtime.providers.tmux`。
  - ✅ `RuntimeService` 增加 `session_list/status/logs/send/interrupt/attach/stop/prune`。
  - ✅ `RuntimeService` 和 CLI 增加 `turn run/status/logs/list`。
  - ✅ `Registry` 增加文件锁、run_id 幂等、force、orphan/result_pending 检测。
  - ✅ `task run --provider tmux-local` 支持 tmux-backed smoke，不用 terminal output 判成功。
- 文件:
  - `~/agents/runtime/conf/profiles.yaml`
  - `~/agents/runtime/runtime/providers/code_cli.py`
  - `~/agents/runtime/runtime/providers/tmux.py`
  - `~/agents/runtime/runtime/service.py`
  - `~/agents/runtime/runtime/cli/main.py`
  - `~/agents/runtime/runtime/core/{registry,run,validation}.py`
  - `~/agents/runtime/tests/**`
- 不做:
  - 不改 workbench/agent/stock 调用方。
  - 不做 daemon/launchd/HTTP server。
- 验证命令:
  - shared runtime unittest。
  - `agent-runtime session start --project workbench --provider tmux-local --id runtime-smoke --json`
  - `agent-runtime session send/status/logs/interrupt/stop ... --json`
  - `agent-runtime turn run --project agent --provider fake --prompt-file <file> --json`
  - `agent-runtime task run --project workbench --provider fake --prompt-file <file> --json`
  - `agent-runtime task run --project workbench --provider tmux-local --prompt-file <file> --json`
  - `agent-runtime task run --project workbench --provider code-cli-wrapper --prompt-file <file> --json` 使用无副作用 result-file wrapper smoke。
- 结果:
  - fake 和 tmux smoke 都能写五件套。
  - `send` 不落完整 prompt, 只落摘要/hash/字符数。
  - 并发提交同一 `run_id` 不破坏 registry; 重复提交默认返回已有状态。

### P2: Workbench `apps/workbench-runtime` 兼容迁移 ✅

- 范围:
  - `apps/workbench-runtime/bin/workbench-runtime` 保持旧 CLI 参数, 内部转调 shared runtime。
  - `apps/workbench-console` 先不改 API, 继续调用 `workbench-runtime` shim。
  - `runs/workbench-runtime` 只读兼容一个阶段; 新写运行态进入 `~/agents/runtime/runs/.../workbench/...`。
- 文件:
  - `apps/workbench-runtime/lib/cli.py`
  - `apps/workbench-runtime/README.md`
  - 必要时 `apps/workbench-console/lib/cli.py`
- 不做:
  - 不改 Lark runtime、KB runtime、scheduler。
  - 不删除旧 runs。
- 验证命令:
  - `apps/workbench-runtime/bin/workbench-runtime doctor --json`
  - `apps/workbench-runtime/bin/workbench-runtime start --profile fake --alias runtime-smoke --cwd . --json`
  - `apps/workbench-runtime/bin/workbench-runtime send runtime-smoke --text "ping" --json`
  - `apps/workbench-runtime/bin/workbench-runtime task start --runtime runtime-smoke --prompt-file apps/workbench-runtime/README.md --wait --json`
  - `PYTHONPYCACHEPREFIX=/tmp/workbench-pycache scripts/agent-python -m py_compile apps/workbench-runtime/lib/cli.py apps/workbench-console/lib/cli.py`
- 预期结果:
  - workbench-console runtime 面板无前端 API 变化。
  - 旧命令输出字段兼容, 新运行态能在 shared registry 看到。

### P3: `~/agents/agent` provider 迁移 ✅

- 范围:
  - agent repo 通过 editable install 或 `PYTHONPATH` 引入 `runtime`。
  - `MainRuntime` 改成 adapter: UI session/turn 仍写 `runs/workbench/sessions`, 执行层调用 shared runtime；shared runtime 返回 `turn` 元数据，agent adapter 负责转换为前端兼容字段。
  - 保留 `/api/runtime/tmux/runs` API surface, 返回前端兼容字段。
  - 结构化 `codex_exec/claude_print/llm_api` 对应 shared `code_cli/llm_api`; 交互 `codex_cli/claude_cli/fake` 对应 shared `tmux`。
  - `model_backends.py` 迁入 runtime 的 LLMGateway/backend resolver 前, agent 仍保留自身 provider 解析; secret 只通过 env name 注入。
- 文件:
  - `~/agents/agent/apps/workbench/runtime/main.py`
  - `~/agents/agent/apps/workbench/runtime/external_cli.py`
  - `~/agents/agent/apps/workbench/runtime/{process_cli_provider,tmux_provider,llm_api_provider}.py` 逐步退役或变 wrapper
  - `~/agents/agent/apps/workbench/server.py` 仅做最小兼容改动
- 不做:
  - 不改图书运营业务 skill。
  - 不改前端 API 一次性重写。
  - 不把 `.env` secret 迁到 shared runtime 配置。
- 验证命令:
  - 使用 Python 3.11+ 运行 `bash scripts/validate.sh --quick`。
  - `python3 scripts/workbench_smoke.py --help`。
  - UI runtime fake session smoke。
- 当前验证:
  - 本机系统 `python3` 仍是 3.9.6；使用临时 PATH 指向 `/opt/homebrew/opt/python@3/bin/python3.14` 后 `scripts/validate.sh --quick` 通过。
- 预期结果:
  - agent GUI 能创建会话、投递 turn、回填 `assistant_message`。
  - 旧 `runs/tmux`, `runs/process-runtime`, `runs/llm-api-runtime` 不再新增, 或只由兼容层只读。

### P4: Loop 控制统一 ✅

- 范围:
  - workbench `apps/workbench-loop` 从 dry-run 扩展为调用 shared `LoopControl.dispatch`。
  - 保持 `interactive-serial`, review required 时不自动 done。
  - agent 若需要任务 loop, 复用同一 `LoopControl` 但 project_id=agent。
- 文件:
  - `~/agents/runtime/runtime/loop.py`
  - `~/agents/workbench/apps/workbench-loop/lib/cli.py`
- 不做:
  - 不新增 scheduler job。
  - 不新增 reviewer agent。
  - 不自动执行远端写入/Git/删除。
- 验证命令:
  - `apps/workbench-loop/bin/workbench-loop doctor --json`
  - `agent-runtime loop add/dispatch/review/status --project workbench --json`
- 预期结果:
  - loop task -> runtime task -> review gate 链路可追踪。

### P5: Stock 接入 ✅

- 范围:
  - Go `agent-runtime` 增加 shared runtime subprocess client, 调可配置的 `agent-runtime` CLI 路径执行 `task run --project stock ... --json`。
  - 先只迁异步 Codex research 执行底座; SQLite task、Evidence ingest、Report、guardrail 留 stock。
  - `done`/`succeeded` 状态在 adapter 映射, stock API 对外先保持旧字段。
  - stock adapter 必须在 shared result 最小校验后再跑 stock schema + guardrail, 通过后才写 stock 事实库/报告。
- 文件:
  - `~/agents/stock/agent-runtime/internal/codexexec/runner.go`
  - `~/agents/stock/agent-runtime/internal/config/config.go`
  - `~/agents/stock/agent-runtime/pkg/client/client.go` 必要时补 task status 映射
  - `~/agents/stock/agent-runtime/internal/guardrail/**` 只接入校验调用, 不迁到 shared runtime
- 不做:
  - 不在同步 API 里跑 Codex。
  - 不启用真实 `--codex` 默认路径。
  - 不把 stock guardrail 放进 shared core。
- 验证命令:
  - `go test ./agent-runtime/... ./agent-api/...`
  - `bash scripts/smoke.sh` 使用 mock 路径。
  - shared runtime stock fake task: `agent-runtime task run --project stock --provider fake --prompt-file <file> --json`
- 预期结果:
  - stock mock smoke 不降级。
  - 真实 Codex 仍需要显式确认, 且 result 通过 stock schema + guardrail 后才采纳。

### P6: 清理旧实现

- 范围:
  - 直接删除旧 provider / 旧 runtime owner, 文档入口统一指向 shared runtime。
  - `apps/workbench-runtime` 不再作为长期可执行 owner；workbench 控制面改为 shared runtime 路径或保留不可执行迁移说明。
  - agent 旧 `process_cli_provider` / `tmux_provider` / `llm_api_provider` 只允许保留薄兼容 wrapper 或删除；不可再拥有执行事实源。
  - stock 旧真实 Codex 执行入口继续保持拒绝默认执行；mock research 只经 shared runtime 执行底座。
  - 更新三项目验证脚本, 加 shared runtime smoke。
- 不做:
  - 不删除历史 `runs` 数据; 只提供 `migration report/prune --dry-run`。
  - 不启用真实 Codex/Claude/LLM API smoke。
  - 不迁移 `lark-runtime`、`kb-runtime`、scheduler 或任何远端写入能力。
- 验证命令:
  - 三项目全量验证。
  - `rg "apps/workbench-runtime|runs/tmux|process_cli_provider|tmux_provider|codexexec.RunReal"` 确认旧引用只剩历史设计/兼容说明。
  - `agent-runtime doctor/migration report/review --json` 和 shared fake/code-cli-wrapper/tmux smoke。

## 验收标准

- shared runtime:
  - `doctor`, `migration report`, `review` 全部 ok。
  - `fake/code_cli/tmux/llm_api(mock)` 都产生统一五件套。
  - `result.json` 缺失/非法时不会进入 `succeeded`。
- workbench:
  - P0-P5 旧 `workbench-runtime` CLI 用户体验不变；P6 后旧 runtime owner 不再可执行。
  - console API 不直接操作 tmux。
  - 新运行态写入 shared runtime, 旧路径只读兼容。
- agent:
  - GUI chat turn 能启动、投递、轮询、回填。
  - `model_backends` secret 不进入 shared config/log。
  - Python 3.11+ 验证通过。
- stock:
  - Go unit + smoke 通过。
  - 研究任务仍异步, 不进入同步 API。
  - 禁止投顾建议、目标价、收益预测等 guardrail 仍有效。

## 当前验证记录

- 通过:
  - `/Users/yang/agents/runtime/scripts/agent-runtime --help`
  - `/Users/yang/agents/runtime/scripts/agent-runtime doctor --json`
  - `/Users/yang/agents/runtime/scripts/agent-runtime migration report --json`
  - `/Users/yang/agents/runtime/scripts/agent-runtime review --json`
  - `PYTHONPYCACHEPREFIX=/tmp/agent-runtime-pycache PYTHONPATH=/Users/yang/agents/runtime python3 -m unittest discover -s /Users/yang/agents/runtime/tests`
  - `agent-runtime task run --project workbench --provider code-cli-wrapper --prompt-file <file> --json`
  - `agent-runtime task run --project workbench --provider tmux-local --prompt-file <file> --json`
  - `agent-runtime turn run/status/logs --project agent --provider fake --prompt-file <file> --json`
  - `agent-runtime session start/send/logs/interrupt/attach/stop --project workbench --provider tmux-local --json`
  - `apps/workbench-runtime/bin/workbench-runtime doctor --json`
  - `apps/workbench-loop/bin/workbench-loop doctor --json`
  - `PYTHONPYCACHEPREFIX=/tmp/workbench-pycache scripts/agent-python -m py_compile apps/workbench-runtime/lib/cli.py apps/workbench-console/lib/cli.py apps/workbench-loop/lib/cli.py`
  - `go test ./agent-runtime/... ./agent-api/...`
- 未通过:
  - `PYTHONPYCACHEPREFIX=/tmp/agent-validate-pycache bash scripts/validate.sh --quick` in `~/agents/agent`: `python3` 为 3.9.6, 不满足 Python 3.11+; `model_backend_smoke.py --help` 因 `str | None` 在 3.9 下报错。
- 未执行:
  - `~/agents/stock/scripts/smoke.sh`: 会写 `data/sqlite/smoke.db` 并启动本地 API, 本轮设计阶段未跑。
  - 真实 Codex/Claude task smoke: 已确认不进入 P6 默认验收；后续单独确认后再启用。

## 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `~/agents/runtime` baseline 未提交 | 消费方无法可靠 pin 依赖 | ✅ baseline commit `12c54ba`; P1 commit `de6a727`; P2-P5 commit `c48259d` |
| `code_cli` profile 裸跑真实 CLI | 退出 0 但无 result_file, 或交互 CLI 卡住 | ✅ 默认 smoke 使用 `code-cli-wrapper`; 裸 CLI 标记 experimental |
| tmux 两种模型混淆(session vs task) | GUI 会话和 result task 行为互相污染 | shared core 明确 `session/turn/task` 三类 run |
| `turn` 没有一等 API | agent GUI 被迫用 task 伪装 turn, 后续状态语义混乱 | ✅ P1 已补 `turn run/status/logs/list`, P3 adapter 只做字段兼容 |
| registry 无锁 | 并发任务丢状态或覆盖 run 记录 | ✅ P1 已加文件锁和幂等/force 语义 |
| 状态名不兼容 | stock/agent UI 误判 done/succeeded | adapter 映射旧状态; shared 新写只用公共状态 |
| 运行态路径切换 | 旧 UI/脚本找不到日志 | P0-P5 保留 shim 和只读兼容; P6 只保留迁移说明与 dry-run report |
| stock 业务边界泄漏 | shared runtime 变成投顾业务 runtime | stock adapter 只传 capability/result, guardrail/store 留 stock |
| stock schema 扩展未执行 | 通用 result 通过但业务报告不合规 | P5 业务 adapter 二次校验, 不把业务字段塞进 shared schema |
| secret 泄漏 | API key/env 进入日志或配置 | 配置只存 env name; doctor 只报 `*_set` |
| Python 版本不一致 | agent 接入验证失败 | P3 前固定 Python 3.11+ venv |

回滚方式:

- P1 失败: 回滚 `~/agents/runtime` 变更, 三项目未受影响。
- P2 失败: `apps/workbench-runtime` shim 回滚到现有实现, shared runs 保留为诊断。
- P3 失败: agent `MainRuntime` adapter 回滚到本地 provider 实现, UI session 数据保留。
- P5 失败: stock `codexexec` 回滚到现有 mock/拒绝 real Codex 行为, SQLite 数据不迁移。

## 实现门禁

| 门禁 | 当前结论 | 证据 | 状态 | 未通过需回答的问题 |
| --- | --- | --- | --- | --- |
| 事实源 | 三项目入口、runtime 代码、overlay、验证入口已回读 | 本文证据清单 | pass |  |
| 存储边界 | shared runs 与三项目业务 runs/data/outputs 已区分 | `runtime/projects/*.runtime.yaml`, storage-boundary | pass |  |
| 脚本归属与可移植性 | shared core 归 `~/agents/runtime`; 业务 adapter 留各项目 | 迁移映射表 | pass |  |
| 设计深度 | full, 因跨 repo 迁移和 Agent 资产变更 | 本文 | pass |  |
| 风险与权限 | P6 直接清理策略已确认; 真实 Codex/Claude/LLM 继续默认关闭 | 已确认方案 | pass |  |
| 方案完整性 | 范围、阶段、验证、回滚、确认动作已列出 | 已确认方案 | pass |  |
| 最小性检查 | 不新增 runtime, 不迁业务逻辑 | Minimality Check | pass |  |
| 阶段切片 | P0-P6 每阶段单一风险面 | 迁移计划 | pass |  |
| 验证命令 | 每阶段有命令或检查点 | 迁移计划/验证记录 | pass |  |
| 回滚边界 | 每项目 shim/adapter 回滚明确 | 风险与回滚 | pass |  |
| 实现交接 | P0-P5 已完成；P6 直接清理方案已确认，待执行 | 实现交接 | pass |  |
| `code_cli` 可实现性 | wrapper 策略已落地，裸 CLI 保留 experimental | `conf/profiles.yaml`, `providers/code_cli.py`, tests | pass |  |
| `turn` 一等 API | service/CLI 已有 turn 动词 | `core/run.py`, `service.py`, `cli/main.py` | pass |  |
| registry 并发/恢复 | registry 已加锁、幂等、force、orphan/result_pending | `core/registry.py`, `service.py` | pass |  |
| stock schema 扩展 | Go adapter 保留 Evidence 校验、guardrail 和 SQLite 写入 | `agent-runtime/internal/codexexec/runner.go` | pass |  |
| 自我进化候选 | 本轮不触发, 先作为设计资产 | 复盘与学习候选 | n/a |  |

## 已确认决策与实现交接

- 下一步模式: P0-P5 已完成; P6 直接清理旧 runtime / provider owner，待单独执行。
- P6 默认策略: 直接删除已由 shared runtime 接管的旧实现，不保留长期 shim-only 冻结窗口。
- 真实 provider 策略: 真实 Codex/Claude/LLM API 不进入 P6 默认验收，继续保持 mock/off 或显式拒绝默认执行。
- 提交策略:
  - `~/agents/runtime` 单独提交设计和 shared core 变更。
  - workbench/agent/stock 各项目独立提交，不做跨 repo 混合提交。
  - P6 迁移提交必须 path-limited，不纳入无关工作区改动。
- 停止条件:
  - P6 执行尝试删除历史 `runs` 数据。
  - P6 执行尝试默认启用真实 Codex/Claude/LLM API。
  - 任一项目验证失败且无法通过回滚本阶段改动恢复。
