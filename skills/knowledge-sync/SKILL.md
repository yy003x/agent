---
name: knowledge-sync
description: "Synchronize prepared book-operation materials into the local knowledge base, run ingest and index commands, sample source readback, report failures, and prepare searchable knowledge for later content generation."
metadata:
  short-description: "图书素材正式入库、索引、抽检和同步报告"
---

# knowledge-sync Skill

## 触发条件

当用户要求“同步知识库 / 入库 / ingest / index / 把素材纳入知识库 / 检查知识库同步结果 / 重建索引”时使用本 skill。

它负责写入和校验 KB；只读查询仍走 `knowledge-search`，入库前整理走 `book-asset`。

## 执行流程

### 步骤 1：确认写入授权

KB 写入必须确认来源目录、modality、limit、是否 resume，以及用户是否允许 `--allow-write`。

### 步骤 2：执行 ingest

```bash
python3 skills/content-generate/scripts/content_runtime.py kb ingest \
  --src <prepared-dir> \
  --modality auto \
  --limit <N> \
  --resume \
  --allow-write
```

如果只是预览，不加 `--allow-write`，并说明不会实际写入。

### 步骤 3：重建必要索引

普通增量入库后优先重建 FTS / graph；全量或向量重建属于重型动作，除非用户明确要求。

```bash
python3 skills/content-generate/scripts/content_runtime.py kb index \
  --rebuild fts \
  --allow-write
```

### 步骤 4：抽样检索回读

对本次关键词做只读检索：

```bash
python3 skills/content-generate/scripts/content_runtime.py kb search \
  --query "<图书或主题关键词>" \
  --modality all \
  --topk 10 \
  --json \
  --no-log \
  --no-touch
```

必须回读命中的 `source_path`，确认 KB 可用。

### 步骤 5：生成同步报告

建议写入：

```text
outputs/YYYY-MM-DD/kb-sync/<batch-slug>/sync-report.md
```

报告包含来源、写入命令、成功/失败数量、抽检结果、低命中项、需补 caption/补资料项。

### 步骤 6：收尾

实质写入后转 `workbench-finalizer` 记录 session。

## 输出契约

- 输出：同步报告、执行命令、抽检关键词、命中来源、失败项和补救建议。
- 成功标准：素材已写入 KB，索引可检索，至少完成一次源文件回读抽检。
- 部分完成：ingest 成功但索引或抽检失败时，明确失败原因。

## 安全边界

KB 写入必须经过 `--allow-write`。不得把 secret、隐私、内部价格策略、未公开活动或不可对外资料当普通运营素材入库。不得自动执行 `kb gc` 正式清理。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际同步任务至少运行 ingest help、search 抽检或明确说明依赖缺失。

## 完成标准

最终回复说明同步状态、报告路径、抽检结果、失败项和下一步是否可进入 `content-generate`。
