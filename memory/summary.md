# 启动记忆

## 项目状态

构建阶段：P0–P5 基线代码已搭建。
- 已实现：学而思图书运营入口、路由/安全规则、`content-generate` / `finalize` skill、`content_runtime.py` 的 KB ingest/search/index/gc/legacy/related、文案草稿、plan 构建、媒体组装、发布打包、finalize activity 标记、自学习候选生成与 promote 命令、scheduler。
- 待验证：首次 `content_runtime.py init`、最小 KB ingest/search、端到端内容生成、Stop hook activity 兜底、scheduler 常驻运行、完整 e2e 依赖环境。
- 待补能力：高质量平台文案模板库、真实素材样例回归集、非空旧 KB 迁移脚本。
- 当前知识库事实源以 `workspace/kb/lance/` 为准；`workspace/kb/catalog.db` 或 `workspace/kb/vector/` 是旧栈残留，`kb legacy --allow-write` 只删除空残留，非空残留保留等待迁移。

## 关键偏好

- 内容领域：教育类图书（书单 / 读书笔记 / 知识卡片 / 读后感 / 书评）。
- 交付平台：小红书（默认）/ 朋友圈；成品包落 `outputs/`，**人工预览后手动发布**。
- 本地运行与本地存储；文案与图片/视频 caption 可调用 Claude / Anthropic API。
- 不自动发帖，不调用外部发布 API，不使用图片/视频生成模型（仅检索 KB 既有素材组装）。

## 上次更新

2026-06-18
