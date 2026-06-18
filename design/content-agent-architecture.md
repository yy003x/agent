# 内容生成 Agent 架构概览

> 实现状态：✅ 骨架已构建于**仓库根目录**（AGENTS.md / rules / skills / scripts / apps）；本文为顶层概览。
> design_state：architecture（全部分叉与参数已锁定）。
> 本文只给「全景 + 锁定决策 + 分阶段路线」，**不重复细节**——分层规格见 `01-04`，可直接复制的产物见 `templates/`。

---

## 系统概述

运行在本地 macOS 上的 **AI 内容生成 Agent**：

- 接受日常对话输入，按语义自动分类并路由到对应处理流程
- 理解本地知识库（文档/图片/视频），按内容需求检索契合素材
- 用 Claude 生成文案/脚本/分镜，组装图文或短视频成品包
- 交付物落本地目录，发布前人工预览确认（无自动发帖）
- 每轮任务结束自动记录，定期自学习并晋升规则

**不做的事**：不生成图片/视频（无 gen 模型），不调外部生成 API，不运行独立远端服务，不自动发布。

---

## 锁定决策（全局前提）

| 维度 | 选择 |
|---|---|
| 运行形态 | **全本地**：Claude/Codex 作大脑，KB/检索/媒体用本地脚本；无远端服务、无 GPU、无外部生成 API |
| 内容生成本质 | **文案生成 + 媒体检索组装**：文案由 Claude API 生成；图片/视频从 KB 检索既有素材再组装，**不生成** |
| 触发机制 | **输入路由软**（规则 + 模型判语义）+ **收尾/学习硬**（hook + 调度，确定性） |
| 知识库 | **LanceDB**（向量+标量+FTS 同库）+ **bge-small-zh-v1.5** 向量 + **jieba** 中文分词 FTS + **document-concept 二部图**；**三路 RRF**（向量∥FTS∥图召回）+ **bge-reranker-base** 精排（详见 04） |
| 内容领域 | **教育类图书**（影响检索相关性与文案模板：书单/读书笔记/知识卡片等） |
| 交付去向 | **小红书 + 朋友圈**：产平台成品包 + 预览确认后手动发布 |
| 素材量级 | **千级**（半年窗口）；LanceDB 轻松承载；半年清理 job 控制规模 |
| 目标机器 | macOS **M1Pro 16G**：模型内存峰值 ~2GB，宽裕（见 04 §9） |

---

## 总体架构

```text
┌──────────────────────── 大脑层（Claude / Codex）────────────────────────┐
│  输入 → [软] 路由规则（rules/core-routing.md）                          │
│           ├─ 闲聊 / 一次性问答         → 直接回答，不触发 skill          │
│           ├─ 搜索 / 调研               → research（读 KB 回答）          │
│           ├─ 设计 / 方案 / 规划        → design（讨论后落文件）          │
│           ├─ 执行 / 代码 / 任务        → execute（改文件提交）           │
│           └─ 内容生成 / 出图文视频     → content-generate skill          │
│  输出 → [硬] Stop hook → finalize.py record → 沉淀(session)            │
│  [硬] scheduler 定时 → agent_learning_review.py → 候选 → 规则晋升      │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │ content-runtime（本地编排：检索/ingest/组装）
            ┌────────────────────┴────────────────────┐
┌───────────▼───────────────┐          ┌──────────────▼──────────────┐
│  本地 KB 层（LanceDB）     │          │   内容组装（无生成模型）     │
│  items 表                  │          │  文案: Claude API 生成       │
│    ├─ vector(bge-small-zh) │◄─检索────┤  图片/视频: 取 KB 既有素材   │
│    ├─ text_seg(jieba FTS)  │ 三路 RRF │  组装: 图文拼版/短视频时间线  │
│    └─ concepts 二部图召回  │  +rerank │  轻编辑: 裁剪/字幕(ffmpeg)    │
└────────────────────────────┘          └──────────────────────────────┘
   workspace/kb/lance/（索引）+ workspace/media-store/（原件，不进 Git）
```

---

## 分层索引（细节见对应文档）

| 层 | 职责 | 详细规格 |
|---|---|---|
| L1 输入路由 | 语义分类 → 处理模式 | `rules/core-routing.md`、03 §路由分类 |
| L2 执行 skill | content-generate 编排 | `templates/skills/content-generate/SKILL.md`、03 |
| L3 收尾沉淀 | Stop hook → session 记录 | **01 §5**（finalize 机制） |
| L4 自学习 | 候选生成 → 人工晋升 | **02**（自我进化规格，候选 5 类含 kb-tuning） |
| L5 知识库 | ingest / hybrid search / gc | **04**（知识库层独立设计） |
| L6 内容组装 | media assemble / publish package | 03 §内容组装规格 |

> 上层（L1-L4/L6）通过 `content-runtime kb search` 消费 KB，**对 L5 内部实现（LanceDB）透明**。

---

## 分阶段实现（P0 → P4，按顺序构建）

### P0 基础骨架（大脑层 + 路由 + 收尾）
构建：`AGENTS.md`、`rules/core-routing.md`、`rules/core-safety.md`、`SKILL.md`、`scripts/finalize.py`、Stop hook。
验证：6 类对话路由正确（闲聊/问答/搜索/设计/内容生成/执行）；finalize 手动/hook 触发生成 session。

### P1 本地 KB（文档 + 图片）
构建：`content_runtime.py` 的 `init` / `kb ingest`(doc+image) / `kb search`；**LanceDB 表 + bge-small-zh-v1.5 向量 + jieba-FTS + RRF + reranker**（见 04）。
验证：`kb ingest --src ./test-data --allow-write` 写入 items 表；`kb search --query "数学思维" --topk 5 --json` 经 hybrid+rerank 返回并人工回读核对。

### P2 图文组装
构建：`media assemble`（Pillow 拼版）、`publish package`（小红书/朋友圈成品包）、SKILL.md 完整流程。
验证：端到端出一组小红书图文，检查 outputs/ 成品包结构。

### P3 视频 ingestion + 短视频组装
构建：`kb ingest --modality video`（ffmpeg 抽帧 + Claude vision caption + 可选 whisper）、`media assemble` 短视频时间线。
验证：端到端出一条短视频（KB 视频片段 + 文案字幕）。

### P4 调度 + 自学习闭环
构建：`apps/scheduler/scheduler.py`（APScheduler 读 jobs.json）、`scripts/agent_learning_review.py`、`jobs.json`（weekly_learn / media_ingest / kb_gc，写操作带 `--allow-write`）。
验证：触发一次 `agent_learning_review.py` 生成候选；人工确认一条并晋升到 rules/。

---

## 触发机制汇总（软 / 硬）

| 管道段 | 软/硬 | 实现 |
|---|---|---|
| 输入分类路由 | 软 | `rules/core-routing.md`（模型判语义） |
| 执行 | 软 | content-generate skill + content-runtime |
| 输出收尾 | **硬** | `Stop`/`notify` hook → `finalize.py record` |
| 媒体 ingest | **硬（定时）** | scheduler job 每天 02:00 后台批处理 |
| 自学习 | **硬（定时）** | scheduler job 每周一 → `agent_learning_review.py` |
| 规则晋升 | 半（人确认） | 候选 → 用户 accept → 写 rules/memory |

---

## 环境与技术栈

环境要求、Python 依赖、目录结构见 **README.md**；KB 模型与内存预算（M1Pro 16G）见 **04 §9**。

核心栈：`anthropic`（文案/caption）、`lancedb` + `sentence-transformers`(bge-small-zh-v1.5 / bge-reranker-base) + `jieba`（KB）、`ffmpeg` + `Pillow`（媒体）、`apscheduler`（调度）。

---

## 安全与验证

- 写门禁：`kb ingest/index/gc`、`media assemble`、`publish package` 必须 `--allow-write`，否则 dry-run。
- 发布门禁：外部平台发布前预览确认，不自动发帖。
- 敏感信息：API key 存 `.env`（进 `.gitignore`）；不把 media-store 绝对路径写入对外产物。
- 验证策略：P0 路由回归 + hook 实测；P1-P3 各一个端到端样例 + KB 召回人工回读；P4 完整 learn→候选→晋升→`validate.sh`。

> 安全规则唯一事实源：`rules/core-safety.md`；自我进化边界：02。
