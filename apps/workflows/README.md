# Workflows

业务 workflow 编排层。真实代码放在 `src/agent_workflows/`，运行态写入 `runs/workflows/`。

- `src/agent_workflows/content_delivery.py`：内容交付 workflow。
- `src/agent_workflows/state.py`：workflow 状态模型和 JSON 写入工具。
- `tests/`：workflow 专属测试。
