# Workbench Memory

本应用负责本地 session 收尾记录和自学习候选生成。

稳定入口：

```bash
python3 apps/agent-memory/bin/finalize --help
python3 apps/agent-memory/bin/agent-learning-review --help
PYTHONPATH=apps/agent-memory/src python3 -m agent_memory --help
```

根目录 `scripts/finalize.py` 和 `scripts/agent_learning_review.py` 只保留薄 wrapper。
