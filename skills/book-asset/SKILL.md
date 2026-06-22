---
name: book-asset
description: "Prepare raw book-operation materials for the personal workbench, including product documents, images, videos, file naming, classification, deduplication, source notes, and ingest batches before knowledge-base synchronization."
metadata:
  short-description: "图书素材入库前整理、分类、去重和批次准备"
---

# book-asset Skill

## 触发条件

当用户要求整理一批图书运营素材、产品资料、文档、图片、视频，或说“把这些素材准备入库 / 整理素材 / 做素材批次 / 检查素材能不能入库”时使用本 skill。

本 skill 只负责入库前准备，不负责正式写入知识库；正式 ingest 和 index 交给 `knowledge-sync`。

## 执行流程

### 步骤 1：确认素材范围

确认来源目录、目标图书或主题、素材类型、是否只读检查、是否允许整理到 `workspace/media-inbox/` 或 `workspace/media-store/`。

### 步骤 2：扫描素材

读取文件清单，按类型分组：

- 文档：md、txt、pdf、docx、xlsx、csv。
- 图片：jpg、png、webp、heic。
- 视频：mp4、mov、m4v。
- 其它：先列为 unsupported，不直接丢弃。

### 步骤 3：建立批次清单

为本次素材生成批次记录，建议落到：

```text
outputs/YYYY-MM-DD/asset-ingest/<batch-slug>/manifest.md
outputs/YYYY-MM-DD/asset-ingest/<batch-slug>/manifest.json
```

记录原始路径、建议标题、图书/年级/渠道标签、素材类型、是否可入库、风险和备注。

### 步骤 4：媒体基础检查

图片和视频先做可读性检查。单文件媒体可用：

```bash
python3 skills/content-generate/scripts/content_runtime.py media probe <file>
```

需要 caption 的图片/视频，如果现有 runtime 能处理，后续交给 `knowledge-sync` ingest；如果不能处理，标记为“需人工 caption”。

### 步骤 5：去重和命名建议

不要擅自删除源文件。只输出重复候选、命名建议和归档建议；批量移动或删除必须用户确认后再由 `workbench-execute` 执行。

### 步骤 6：交接知识库同步

准备完成后，给出下一步命令建议：

```bash
python3 skills/content-generate/scripts/content_runtime.py kb ingest \
  --src <prepared-dir> \
  --modality auto \
  --resume \
  --allow-write
```

并转交 `knowledge-sync`。

## 输出契约

- 输出：素材批次路径、manifest、可入库清单、需人工处理清单、重复/命名建议。
- 成功标准：素材边界清楚，入库批次可被 `knowledge-sync` 直接消费。
- 部分完成：素材无法读取、类型不支持、缺少图书归属或 caption 时标记为待处理。

## 安全边界

不删除、不覆盖、不批量移动源文件；不把家长/学生隐私、内部未公开价格策略或敏感凭证写入 manifest。对外不可发布的素材必须标记。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际执行时至少验证 manifest 可读、路径存在、风险项已列出。

## 完成标准

最终回复说明批次目录、可入库数量、需人工处理项、是否已交接 `knowledge-sync`。
