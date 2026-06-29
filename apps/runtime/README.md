# Runtime Gateway

`apps/runtime/` 是当前工作台 API 到共享 AgentRun runtime 的适配层。

底层 provider、run directory contract、`task` / `session` 执行、`result.json`
校验和 CLI 都由 `~/agents/runtime` 负责。本目录只做三件事：

- 把 Web/API 的 `cli` / `api` / `tmux` 选择映射到 AgentRun profile。
- 调用 `agentrun` 或 `python -m agentrun.cli.main`。
- 把 AgentRun 的运行状态和结果转换成工作台 API 使用的数据结构。

不要在本目录新增 provider 内核实现；需要增强 provider 时改 `~/agents/runtime`。
