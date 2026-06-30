---
name: content-generate
description: "Generate book-operation content drafts and basic assembly plans for Xiaohongshu, Moments, parent groups, book lists, reading notes, recommendations, image-text posts, and short-video scripts using local knowledge-base materials."
metadata:
  short-description: "图书运营内容生成、草稿和基础组装"
---

# content-generate Skill

## 触发条件

用户输入被 `rules/core-routing.md` 分类为「内容生成」时触发。
关键意图词：出一篇、生成内容、做个图文、写小红书、朋友圈文案、书单、读书笔记、
知识卡片、读后感、书评、推荐语、配图、短视频、视频脚本。

边界：图书档案先走 `book-profile`；活动排期先走 `book-campaign`；入库素材先走
`book-asset` / `knowledge-sync`；成品包整理走 `content-package`；发布前审核走
`content-compliance-review`。

## 实现归属

本 skill 只负责内容生成流程、人工门禁和输出契约。真实 KB、检索、草稿、组装计划、
媒体处理和发布包实现归属 `apps/content-runtime/`。新增代码优先改 app；本 skill
只维护调用方式和操作约束。

---

## 前置检查

执行前确认以下条件满足，任一不满足则停下告知用户：

- [ ] `workspace/kb/lance/` 存在（运行 `apps/content-runtime/bin/content-runtime init` 初始化 LanceDB）
- [ ] 向量模型可用（bge-small-zh-v1.5；reranker 加载失败时允许降级）
- [ ] 需要新增图片/视频 caption、智能润色、QA 或需求抽取时，所选智能 runtime 可用：默认 `codex_cli`，或配置启用的 `claude_cli`；工作台路径通过 tmux 真会话执行
- [ ] 形态包含短视频或需要视频 ingest/assemble 时，`ffmpeg` 可执行

---

## 执行流程

**按序执行，不得跳步。**

### 步骤 1：解析需求

从用户输入提取以下信息（不清楚时向用户询问）：

| 字段 | 说明 | 示例 |
|---|---|---|
| 主题 | 内容主题 | 「数学思维书单」「《数学之美》读书笔记」 |
| 平台 | 目标平台 | `xiaohongshu`（默认）/ `moments` / `both` |
| 形态 | 内容形态 | `图文`（默认）/ `短视频` / `组合` |
| 风格 | 内容风格 | `知识科普`（默认）/ `情感共鸣` / `书单推荐` |
| 数量 | 生成条数 | `1`（默认） |
| 约束 | 特殊要求 | 「不超过 300 字」「用蓝色系图片」 |

### 步骤 2：检索素材

```bash
python apps/content-runtime/bin/content-runtime kb search \
  --query "<主题关键词>" \
  --modality all \
  --topk 10 \
  --json
```

如返回结果 < 3 条，尝试拆分主题关键词再搜一次（例如「数学思维书单」→ 先搜「数学思维」再搜「书单」）。
如仍 < 3 条，告知用户 KB 素材不足，询问是否先 ingest 素材或改用其他主题。

### 步骤 3：展示候选，等用户筛选

把检索结果整理为表格展示：

```
候选素材（共 N 条）：
#  | 类型   | 标题                | 相关描述                    | ID
1  | image  | 数学思维导图_01.jpg | 彩色思维导图，适合配图      | abc123
2  | doc    | 《数学之美》第3章   | 信息论与数学美感，核心内容  | def456
...

请选择使用哪些素材（输入编号，如 1,3,5；或「全部」）：
```

等用户明确选择后继续。

### 步骤 4：回读选中素材

对用户选中的每条素材，读取 `source_path` 对应的原文件（**索引是候选，原文件是事实源**）：
- 文档：读取文本内容（取前 500 字作为上下文）
- 图片：读取 catalog 中的 `caption`（已有 tmux CLI runtime caption 或人工 caption，不重复生成）
- 视频：读取 `transcript`（帧 caption 拼接）

### 步骤 5：生成文案草稿

先把选中素材整理成 `sources.json`，再调用 runtime 生成可编辑草稿：

```bash
python apps/content-runtime/bin/content-runtime text draft \
  --brief "<用户需求摘要>" \
  --platform xiaohongshu \
  --style "<知识科普|情感共鸣|书单推荐|读书笔记>" \
  --sources outputs/YYYY-MM-DD/content/<slug>/sources.json \
  --out outputs/YYYY-MM-DD/content/<slug>/draft.json \
  --allow-write
```

展示 `draft.json` 中的标题、正文、标签；如需智能润色，交给配置的 tmux CLI runtime 基于草稿和素材事实改写，
再写回 `draft.json`，不得编造素材事实。
询问：「文案是否满意？（可要求修改风格/长度/角度后重新生成或微调）」
等用户确认后继续。

### 步骤 6：生成 plan.json

根据确认后的 `draft.json` 和素材选择生成组装方案：

```bash
python apps/content-runtime/bin/content-runtime plan build \
  --draft outputs/YYYY-MM-DD/content/<slug>/draft.json \
  --out outputs/YYYY-MM-DD/content/<slug>/plan.json \
  --allow-write
```

展示 plan.json，询问用户确认后进入组装。

### 步骤 7：组装

```bash
python apps/content-runtime/bin/content-runtime media assemble \
  --spec outputs/YYYY-MM-DD/content/<slug>/plan.json \
  --out outputs/YYYY-MM-DD/content/<slug>/ \
  --allow-write
```

### 步骤 8：打包

如果用户只要求打包已有草稿或整理发布包，直接转 `content-package`，不要重新生成内容。

```bash
python apps/content-runtime/bin/content-runtime publish package \
  --platform xiaohongshu \
  --in outputs/YYYY-MM-DD/content/<slug>/ \
  --allow-write
```

### 步骤 9：预览确认

列出成品包完整结构：
```
成品包：outputs/2026-06-17/content/math-books/
├── xiaohongshu/
│   ├── cover.jpg          [1080×1080]
│   ├── img_01.jpg         [1080×1440]
│   └── publish-checklist.md
标题：[生成的标题]
正文：[生成的正文]
标签：[标签列表]
```

询问：「成品包已就绪，请检查后手动发布。是否需要调整？」
**不自动发帖。等用户手动发布。**
发布前如用户要求“检查 / 审核 / 看风险”，转 `content-compliance-review`。

### 步骤 10：收尾

```bash
python3 apps/agent-memory/bin/finalize record \
  --skill content-generate \
  --status success \
  --summary "生成<平台>内容：<主题>，使用素材 <N> 条，产出 outputs/<path>"
```

---

## 输出格式

```
outputs/YYYY-MM-DD/content/<slug>/
├── xiaohongshu/
│   ├── cover.jpg
│   ├── img_01.jpg  [..img_N.jpg]
│   └── publish-checklist.md
└── moments/                        ← 仅 platform=moments 或 both 时存在
    ├── img_01.jpg
    └── publish-checklist.md
```

---

## 安全边界

- 步骤 7/8 必须有 `--allow-write`；无此参数只输出 dry-run 预览
- 步骤 9 必须等用户明确确认后才结束 skill，不自动退出
- 不调外部发布 API
- 不把 `media-store` 绝对路径写入 publish-checklist.md（用相对路径）
- KB 素材不足时停下来告知，不用不相关素材凑数

---

## 异常处理

| 异常 | 处理方式 |
|---|---|
| KB 检索 < 3 条 | 告知用户，询问是否 ingest 素材或调整主题 |
| 智能 runtime 任务失败 | 重试一次，仍失败则告知用户检查 CLI 登录态、tmux/result file 或改用模板草稿 |
| ffmpeg 组装失败 | 展示错误信息，询问用户是否跳过视频只出图文 |
| 用户在步骤中途取消 | 停止执行，不触发 finalize，已生成的临时文件不自动删除（提示用户位置） |
