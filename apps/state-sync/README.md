# State Sync

本应用负责导出、导入和校验 ignored 本地状态，用于多设备同步。

稳定入口：

```bash
python3 apps/state-sync/bin/state-sync --help
PYTHONPATH=apps/state-sync/src python3 -m agent_state_sync --help
```

`scripts/state_sync.py` 只保留薄 wrapper；新增调用优先使用本应用入口。
