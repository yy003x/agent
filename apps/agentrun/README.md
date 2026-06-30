# AgentRun Runtime

`apps/agentrun/` 是当前工作台的本地 runtime owner，包含：

1. `src/agentrun/`：业务无关 runtime 内核，包含 provider、session、task、result-file 契约、配置加载和 CLI。
2. `src/agentrun_workbench/`：当前工作台 API/Web 到 AgentRun 的适配层。
3. `bin/`：应用内薄脚本入口。
4. `tests/`：AgentRun 单元测试。

AgentRun 的项目级可提交运行配置统一放在 `config/agentrun/`,该目录只保留三个 provider 配置文件：

- `config/agentrun/api.yaml`
- `config/agentrun/cli.yaml`
- `config/agentrun/tmux.yaml`

`runtime.yaml` 不放在项目配置目录里;`runs_dir/default_project/default_profile/max_concurrency` 使用 AgentRun 内置默认值或由调用参数覆盖。

运行命令示例：

```bash
PYTHONPATH=apps/agentrun/src python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun doctor --json
PYTHONPATH=apps/agentrun/src python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun profiles --json
```
