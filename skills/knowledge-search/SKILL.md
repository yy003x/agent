---
name: knowledge-search
description: "Search and verify facts from the local personal workbench knowledge base, workspace, outputs, design documents, daily session summaries, and project files. Use when Codex needs source-backed local context before answering, designing, or generating book-operation content."
metadata:
  short-description: "检索本地知识库、workspace 和项目事实源"
---

# knowledge-search Skill

## 触发条件

当回答依赖当前项目或图书运营资料的本地事实时使用本 skill，包括：

- 查询 `workspace/kb/` 中的图书资料、文档、图片/视频 caption、历史素材。
- 查找 `design/`、`outputs/`、`workspace/daily/`、`memory/summary.md` 中的既有方案、产物和 session 摘要。
- 内容生成、方案设计或执行前需要回读真实来源。
- 用户问“之前怎么定的”“会话记录在哪里”“某个素材/产物/设计在哪”。

不适用于外部网页、平台最新规则、竞品和工具现状调研；这些走 `workbench-research`。

## 执行流程

### 步骤 1：确认事实域

先判断需要查的是知识库素材、设计文档、运行产物、session 摘要，还是项目代码/配置。用户限定目录时只查限定范围。

### 步骤 2：检索候选

知识库检索优先使用只读模式：

```bash
python3 skills/content-generate/scripts/content_runtime.py kb search \
  --query "<关键词>" \
  --modality all \
  --topk 10 \
  --json \
  --no-log \
  --no-touch
```

项目文件、设计、outputs 和 daily 可使用 `rg`、`find` 或直接读取指定文件。索引结果只是候选，不是结论。

### 步骤 3：回读源文件

必须打开并读取最佳命中的 `source_path` 或真实文件内容，再基于源文件回答。不要只引用 snippet、文件名或记忆。

### 步骤 4：处理冲突与缺口

多个来源冲突时，优先使用用户本轮提供的材料、最新设计、当前实现和最近 session 摘要；同时说明冲突来源。查不到时明确说“本地未找到充分依据”，不要补造事实。

### 步骤 5：必要时同步索引

本 skill 只读时不刷新索引。若本轮确实写入了 `workspace/` 或 `outputs/` 中需要后续检索的资料，收尾时再按相关规则统一刷新；不要因为一次查询自动重建向量库。

## 输出契约

- 输出：结论、来源路径、必要的命中摘要和不确定项。
- 来源引用：优先给相对路径，例如 `workspace/kb/...`、`design/...`、`outputs/...`。
- 成功标准：关键结论有本地源文件支撑。
- 部分完成：只找到候选或证据不足时，标明缺口和下一步检索建议。

## 安全边界

默认本地只读。不得输出 `.env`、token、cookie、private key、完整 JWT、账号密码、家长/学生隐私或内部未公开价格策略。不要把运行日志、pid、临时 state 当成长期事实源，除非用户明确要求排障。

## 验证

修改本 skill 后至少运行：

```bash
bash scripts/validate.sh --quick
```

## 完成标准

最终回复必须说明是否检索了 KB、是否回读源文件、用了哪些路径，以及哪些结论仍未确认。
