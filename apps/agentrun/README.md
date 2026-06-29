# AgentRun Runtime

`apps/agentrun/` 是当前工作台的本地 runtime owner，包含：

1. `agentrun/`：provider、session、task、result-file 契约、配置加载和 CLI。
2. `tests/`：AgentRun 单元测试。
3. `external_cli.py`、`main.py`：工作台 API/Web 到 AgentRun 的适配。
4. `skill_registry.py`、`state.py`、`model_backends.py`：工作台专属运行能力。

运行命令示例：

```bash
PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --runs-dir runs/agentrun doctor --json
PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --runs-dir runs/agentrun profiles --json
```
