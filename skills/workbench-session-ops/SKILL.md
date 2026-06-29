---
name: workbench-session-ops
description: "Operate and troubleshoot personal workbench GUI sessions, tmux-backed Codex/Claude workers, session lists, physical deletion, runtime logs, pending turns, provider config, and local service health."
metadata:
  short-description: "工作台会话、tmux runtime 和服务运维排障"
---

# workbench-session-ops Skill

## 触发条件

当用户要求“会话在哪 / 删除会话 / 批量删除 / tmux 卡住 / runtime 投递失败 / provider 配置 / UI 服务启动 / 日志怎么看 / session 恢复或排障”时使用本 skill。

它负责工作台运行态运维，不负责内容生成、素材入库或方案设计。

## 执行流程

### 步骤 1：确认操作类型

区分只读排障、启动服务、停止会话、物理删除、批量删除、provider 配置检查。删除和批量操作必须等待用户确认。

### 步骤 2：读取状态

常用只读检查：

```bash
make status
curl -sS http://127.0.0.1:8765/api/health
curl -sS http://127.0.0.1:8765/api/skills
tmux list-sessions
find runs/workbench/sessions -maxdepth 2 -name state.json -print
```

### 步骤 3：定位 session

会话事实源：

```text
runs/workbench/sessions/<session_id>/
  state.json
  messages.jsonl
  events.jsonl
  turns/
  runtime/provider/
```

会话列表不是 Codex/Claude 原生会话记录，而是工作台自己的 session state。

### 步骤 4：处理 tmux runtime

查看 pane、日志、状态：

```bash
tmux list-panes -a
tail -n 120 runs/workbench/sessions/<session_id>/runtime/provider/<run_id>/output.log
cat runs/workbench/sessions/<session_id>/runtime/provider/<run_id>/status.json
```

不要把 pane 输出当作完成信号；完成以 `result.json` 为准。

### 步骤 5：删除或批量操作

优先调用 UI API，让服务先停止绑定 tmux pane，再物理删除目录。直接 `rm -rf` 只能在 API 不可用、用户明确确认且路径核对后执行。

### 步骤 6：服务启动与控制

推荐使用 Makefile 管理 API + Web，避免再把 API 服务挂在 tmux pane 里：

```bash
make start
make status
make logs
make stop
make restart
```

单独控制 API 或 Web：

```bash
make api-start
make api-stop
make web-start
make web-stop
```

服务管理只负责当前后台 API / Web 进程。若端口被其它进程占用，先用 `make status`、`make logs` 和系统端口检查定位来源，再按影响范围处理。

## 输出契约

- 输出：诊断结论、相关 session/run 路径、tmux pane、日志摘要、执行的 API 或命令。
- 成功标准：能定位会话状态、runtime 状态和下一步操作。
- 部分完成：服务未启动、API 不可用或日志缺失时，说明替代路径。

## 安全边界

批量删除、物理删除、kill tmux pane、覆盖 provider 配置前必须确认影响范围。不得删除非 `runs/workbench/sessions/<session_id>` 的目录。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际运维任务结束后，检查 `make status`、`/api/health` 或相关 session 路径。

## 完成标准

最终回复说明处理了哪些 session/runtime、是否有删除或停止操作、服务状态和剩余风险。
