# 启动记忆

## 项目状态

构建阶段：P0–P4 基线代码已搭建。
- 已实现：路由/安全规则、`content-generate` / `finalize` skill、`content_runtime.py` 的 KB ingest/search/index/gc/related、媒体组装、发布打包、finalize activity 标记、自学习候选生成、scheduler。
- 待验证：首次 `content_runtime.py init`、最小 KB ingest/search、端到端内容生成、Stop hook activity 兜底、scheduler 常驻运行。
- 待补能力：文案/plan 脚本化命令、自学习候选晋升命令、旧 KB 残留清理。
- 当前知识库事实源以 `workspace/kb/lance/` 为准；若看到 `workspace/kb/catalog.db` 或 `workspace/kb/vector/`，视为旧栈残留，未确认前不删除。

## 关键偏好

- 内容领域：教育类图书（书单 / 读书笔记 / 知识卡片 / 读后感 / 书评）。
- 交付平台：小红书（默认）/ 朋友圈；成品包落 `outputs/`，**人工预览后手动发布**。
- 本地运行与本地存储；文案与图片/视频 caption 可调用 Claude / Anthropic API。
- 不自动发帖，不调用外部发布 API，不使用图片/视频生成模型（仅检索 KB 既有素材组装）。

## 上次更新

2026-06-18
