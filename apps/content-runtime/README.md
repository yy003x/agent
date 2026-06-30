# Content Runtime

本应用承载图书运营内容生产的确定性 runtime：本地 KB、检索、草稿、组装计划、媒体处理和发布包生成。

稳定入口：

```bash
python3 apps/content-runtime/bin/content-runtime --help
PYTHONPATH=apps/content-runtime/src python3 -m agent_content_runtime --help
```

`skills/content-generate/scripts/content_runtime.py` 只保留薄 wrapper；新增调用优先使用本应用入口。
