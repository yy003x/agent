# 内容生成 Agent 架构设计

> 实现状态: ⏳ 未实现 — architecture 基线，无代码；可交付实现版见 `design/agent-dev/`

> design_state: architecture（全部分叉与参数已锁定，可开工基线；构建按 P0-P4）
> 目标读者：Claude / Codex —— 据此在**全新项目目录**完成完整构建，无需任何外部上下文。

---

## 系统概述

本系统是一个运行在本地 macOS 上的 **AI 内容生成 Agent**，核心能力：

- 接受用户日常对话输入，按语义自动分类并路由到对应处理流程
- **理解本地知识库（文档/图片/视频）**，按内容需求检索契合素材
- 用 Claude 生成文案/脚本/分镜，组装图文或短视频成品包
- 交付物落本地目录，发布前必须人工预览确认（无自动发帖）
- 每轮任务结束自动记录，定期自学习并晋升规则

**不做的事**：不生成图片/视频（无 gen 模型），不调外部生成 API，不运行独立远端服务，不自动发布。

---

## 环境假设（构建前必须满足）

| 依赖 | 要求 |
|---|---|
| 操作系统 | macOS（Apple Silicon 或 Intel 均可） |
| Python | 3.11+ |
| ffmpeg | 已安装（`brew install ffmpeg`），用于视频抽帧/字幕叠加/裁剪 |
| Claude API | 有效的 `ANTHROPIC_API_KEY`，用于文案生成和图片 caption |
| whisper（可选） | 本地 `faster-whisper` 或 `whisper.cpp`，用于视频字幕；**默认后置，不要求预装** |
| AI 运行时 | Claude Code（CLI）或 Codex CLI；两者均支持 hook 和 skill/rules 机制 |
| 存储 | 本地磁盘，知识库千级条目 + 媒体文件，SQLite + 本地向量库足够 |

---

## 从零目录结构（需全部创建）

```
<project-root>/
├── AGENTS.md                          # 项目入口（Claude/Codex 启动时读取）
├── rules/
│   ├── core-routing.md                # 输入路由规则（本文档末尾附全文）
│   └── core-safety.md                 # 安全边界规则（写门禁、发布确认）
├── skills/
│   └── content-generate/
│       ├── SKILL.md                   # content-generate skill 主文档
│       └── scripts/
│           └── content_runtime.py     # content-runtime CLI 实现（见接口规格）
├── apps/
│   ├── scheduler/
│   │   ├── scheduler.py               # APScheduler 定时任务主程序
│   │   └── jobs.json                  # 定时 job 配置（见调度规格）
│   └── content-runtime/               # 如果 content_runtime 做成独立 app 的落点
├── scripts/
│   ├── finalize.py                    # 每轮收尾记录脚本（见收尾规格）
│   ├── agent_learning_review.py       # 自学习候选生成脚本（见学习规格）
│   └── validate.sh                    # 启动自检脚本
├── memory/
│   └── summary.md                     # 最小启动记忆（Agent 首次启动后手动填写）
├── workspace/                          # 不进 Git
│   ├── kb/                            # 知识库文本/caption/索引
│   │   ├── catalog.db                 # SQLite 主库（str + fts + graph + 元数据）
│   │   └── vector/                    # 本地向量库（ChromaDB collection 目录）
│   ├── media-store/                   # 媒体原件（图片/视频，不进 keyword 索引）
│   ├── daily/                         # 每日 session 记录
│   │   └── YYYY-MM-DD/
│   │       └── session-<slug>.md
│   ├── agent-learning/                # 学习候选暂存
│   └── resume/                        # 未完成任务恢复点
└── outputs/                           # 任务产出（可进 Git，按需）
    └── YYYY-MM-DD/
        └── content/
            └── <slug>/
                ├── xiaohongshu/       # 小红书成品包
                └── moments/           # 朋友圈成品包
```

`.gitignore` 需排除：`workspace/`、`outputs/` 大文件、`*.key`、`*.env`。

---

## 锁定决策（全局前提）

| 维度 | 选择 |
|---|---|
| 运行形态 | **本地化**：Claude/Codex 作为大脑，KB/检索/媒体用本地脚本 + SQLite + 本地向量库；**无独立远端服务、无 GPU、无外部生成 API** |
| 内容生成本质 | **文本生成 + 媒体检索组装**：文案由 Claude API 生成；图片/视频**从 KB 检索既有素材再组装，不调生成模型** |
| 触发机制 | **输入路由软**（规则 + 模型判语义）+ **收尾/学习硬**（hook + 调度，确定性） |
| 媒体检索方式 | **caption/字幕的文本向量**：图片用 Claude vision 生成 caption，视频抽帧 caption + 字幕；文本向量入 ChromaDB |
| 内容领域 | **教育类图书**（影响检索相关性与文案模板：书单 / 读书笔记 / 知识卡片等） |
| 交付去向 | **小红书 + 朋友圈**：产平台成品包 + 预览确认后**手动发布**（无开放发布 API） |
| 索引设计 | **统一 SQLite + 4 索引视图**：str / fts(FTS5 trigram) / vector(ChromaDB) / graph（最小化）；每条目带 `modality` 字段 |
| 素材量级 / 留存 | **千级**（半年窗口）；本地向量库 + SQLite 轻松承载；**半年清理 job** 控制规模 |
| 需求入口 | 输入是**日常对话（含闲聊）**；是否「内容需求」由**路由规则判别**，无独立选题队列 |

---

## 结论

Agent = **大脑（Claude/Codex + 本系统 rules/skills）** + **本地 KB（SQLite + ChromaDB，扩展多模态索引）** + **轻量本地编排（content-runtime）**。

输入由路由规则软分类到对应 skill；产出内容时大脑编排「多模态检索素材 → 生成文案 → 组装图文/视频」，**媒体取自 KB 既有图片/视频、不生成**；任务结束由硬 hook 触发 `finalize.py` 沉淀；调度器定时触发学习脚本把对话/任务/资料/规则提炼为候选并晋升，形成自我优化闭环。

---

## 总体架构

```text
┌──────────────────────── 大脑层（Claude / Codex）────────────────────────┐
│  输入 → [软] 路由规则（见下文"输入路由"）                                │
│           ├─ 闲聊 / 一次性问答         → 直接回答，不触发 skill          │
│           ├─ 搜索 / 调研               → research 模式（读 KB 回答）     │
│           ├─ 设计 / 方案 / 规划        → design 模式（讨论后落文件）     │
│           ├─ 执行 / 代码 / 任务        → execute 模式（改文件提交）      │
│           └─ 内容生成 / 出图文视频     → content-generate skill（新建）  │
│  输出 → [硬] Stop hook → finalize.py record → 沉淀(session/usage)       │
│  [硬] scheduler 定时 → agent_learning_review.py → 候选 → 规则晋升      │
└───────────────────────────────┬────────────────────────────────────────┘
                                 │ content-runtime（本地轻量编排：检索/ingest/组装）
            ┌────────────────────┴────────────────────┐
┌───────────▼───────────────┐          ┌──────────────▼──────────────┐
│   本地 KB 层（需从零构建） │          │   内容组装（无生成模型）     │
│  SQLite catalog.db        │          │  文案: Claude API 生成       │
│    ├─ str（精确过滤）     │◄─检索────┤  图片/视频: 取 KB 既有素材   │
│    ├─ fts（FTS5 trigram） │          │  组装: 图文拼版/短视频时间线  │
│    └─ graph（关系表）     │          │  轻编辑: 裁剪/字幕叠加(ffmpeg)│
│  ChromaDB（语义向量）     │          └──────────────────────────────┘
└────────────────────────────┘
   workspace/kb/（文本/caption/索引）+ workspace/media-store/（原件，不进 Git）
```

---

## 分层设计

### L1 输入路由层（软路由，规则 + 模型判语义）

路由逻辑写入 `rules/core-routing.md`，全文如下（构建时直接使用）：

```markdown
# 输入路由规则

## 分类与触发

每轮输入先做语义分类，映射到对应处理模式：

| 分类 | 关键词 / 意图特征 | 处理方式 |
|---|---|---|
| 闲聊 / 寒暄 | 问候、感谢、随便聊 | 直接回答，不写文件，不触发 skill |
| 一次性问答 | 单问题、状态查询、概念解释 | 直接回答，不写文件 |
| 搜索 / 调研 | "帮我找"、"查一下"、"有哪些"、"调研" | 检索 KB 并回答，结论落 outputs/ |
| 设计 / 方案 / 规划 | "怎么设计"、"架构"、"方案"、"计划"、"PRD" | 讨论后产设计文档，落 outputs/ 或 design/ |
| 执行 / 任务 / 代码 | "实现"、"改代码"、"修 bug"、"写脚本"、"提交" | 执行模式，改文件，git add/commit |
| 内容生成 | "出一篇"、"生成内容"、"做个图文"、"写小红书"、"朋友圈文案"、"书单"、"读书笔记"、"知识卡片"、"配图"、"视频" | 触发 content-generate skill |

## 路由原则

- 分类是模型语义判断，不是关键词硬匹配；有歧义时选覆盖范围更广的分类。
- "内容生成"优先级高于"搜索"：含"生成/出/做/写+成品形态"的意图直接走 content-generate。
- 每轮只触发一个主处理模式；复合需求（先调研再生成）分步处理。
- 路由决策不记录中间状态；只有执行结果（session）才沉淀。
```

### L2 执行 skill 层（content-generate，从零构建）

**`skills/content-generate/SKILL.md`** 内容规格：

```
skill: content-generate
触发条件: 见 rules/core-routing.md 「内容生成」分类
职责: 把「内容需求」编排成「检索素材 → 生成文案 → 组装 → 交付预览」完整流程

执行流程:
1. 解析需求: 主题 / 目标形态(图文|短视频|组合) / 风格 / 约束 / 数量
2. 检索素材: content-runtime kb search --query "<主题>" --modality <doc|image|video|all> --topk 10
3. 回读命中: 读取候选条目的 source_path，确认内容相关性
4. 文案生成: Claude 基于需求 + 命中素材生成文案/脚本/分镜（结构见下）
5. 组装: content-runtime media assemble --spec <plan.json> --out outputs/YYYY-MM-DD/content/<slug>/
6. 成品包: content-runtime publish package --platform xiaohongshu|moments --in <dir>
7. 预览确认: 展示预览，等用户确认后再手动发布；不自动发帖

文案输出结构（小红书）:
  - 标题（20字内，带钩子）
  - 正文（300-500字，含 emoji，末尾 3-5 个话题标签）
  - 配图说明（从命中图片中选，标注 source_path）

文案输出结构（朋友圈）:
  - 文案（100字内）
  - 配图（1-9张，从命中图片中选）

安全边界:
  - 所有产出默认落 outputs/，不直接上传
  - 发布外部平台必须预览确认
  - 不泄露 media-store 绝对路径到产出文件
```

### L3 收尾沉淀层（硬触发，从零构建 finalize.py）

**`scripts/finalize.py`** 功能规格：

```
子命令:
  record          写一条 session 记录到 workspace/daily/YYYY-MM-DD/session-<slug>.md
  record --handoff  同上，额外写 workspace/resume/<slug>.md（未完成任务恢复点）
  snapshot        读取 git status/diff，判定 success/partial/failed 并输出 JSON

session-<slug>.md 字段:
  - timestamp, session_id
  - skill_triggered（触发了哪个模式/skill）
  - summary（当轮产出摘要，1-3 句）
  - files_changed（git status 简报）
  - status（success/partial/failed）
  - usage（可选：Claude API token 用量）

触发方式（Claude Code）:
  settings.json 中配置 Stop hook:
  {
    "hooks": {
      "Stop": [{"command": "python scripts/finalize.py record"}]
    }
  }

触发方式（Codex）:
  config.toml 中配置:
  [hooks]
  notify = ["python scripts/finalize.py record"]
```

### L4 自学习层（硬触发，从零构建 scheduler + 学习脚本）

**`scripts/agent_learning_review.py`** 功能规格：

```
输入（扫描以下事实层）:
  - workspace/daily/**/*.md        任务 session 记录
  - workspace/kb/              KB 检索日志（ingest/search 操作记录）

输出（写到 workspace/agent-learning/）:
  - candidates-YYYY-MM-DD.md   候选列表，每条包含:
      type: rule|skill|memory|template
      confidence: high|medium
      content: 建议内容
      evidence: 来源 session 引用

晋升流程（半自动）:
  1. 脚本生成候选文件
  2. 用户（或 Agent 读取后）确认候选
  3. 确认后写入 rules/ 或 skills/ 或 memory/
  4. python scripts/validate.sh 验证基本健康

不做的事: 不自动晋升（必须人工确认）；不删历史规则（只新增或提议修改）
```

**`apps/scheduler/jobs.json`** 配置规格：

```json
{
  "jobs": [
    {
      "id": "weekly_learn",
      "cron": "0 9 * * 1",
      "command": "python scripts/agent_learning_review.py",
      "description": "每周一 09:00 触发学习候选生成"
    },
    {
      "id": "media_ingest",
      "cron": "0 2 * * *",
      "command": "python skills/content-generate/scripts/content_runtime.py kb ingest --src workspace/media-inbox --limit 20 --resume",
      "description": "每天 02:00 后台 ingest 新增媒体，限流 20 条"
    },
    {
      "id": "kb_gc",
      "cron": "0 3 1 */6 *",
      "command": "python skills/content-generate/scripts/content_runtime.py kb gc --older-than 180d --dry-run",
      "description": "每半年首日 03:00 预览清理候选（dry-run，确认后手动去掉 --dry-run 执行）"
    }
  ]
}
```

**`apps/scheduler/scheduler.py`** 使用 `APScheduler`（`pip install apscheduler`），读取 `jobs.json` 启动定时任务。

### L5 知识库层（从零构建，SQLite + ChromaDB）

**索引设计（统一 SQLite + 独立 ChromaDB collection）**：

```sql
-- catalog.db 建表

-- 主表：每个知识条目
CREATE TABLE items (
    id          TEXT PRIMARY KEY,        -- 内容 hash（sha256[:16]）
    modality    TEXT NOT NULL,           -- doc | image | video
    source_path TEXT NOT NULL,           -- 原文件绝对路径（media-store 或文档目录）
    title       TEXT,                    -- 书名/文件名/标题
    tags        TEXT,                    -- JSON array，书名/主题/标签
    caption     TEXT,                    -- 图片/视频的 Claude vision 描述
    transcript  TEXT,                    -- 视频字幕（可选）
    duration_s  REAL,                    -- 视频时长（秒）
    width       INTEGER,
    height      INTEGER,
    file_hash   TEXT,
    ingest_at   TEXT,                    -- ISO8601
    last_hit_at TEXT,                    -- 最后被检索命中时间
    status      TEXT DEFAULT 'active'    -- active | archived | deleted
);

-- FTS5 全文索引（trigram tokenizer，支持中文子串模糊）
CREATE VIRTUAL TABLE items_fts USING fts5(
    id UNINDEXED,
    title, tags, caption, transcript,
    tokenize = "trigram"
);

-- graph 最小关系表
CREATE TABLE edges (
    src   TEXT,  -- item id
    dst   TEXT,  -- item id
    rel   TEXT   -- same_book | same_tag | same_source | cited_by
);
```

ChromaDB 使用 `chromadb` 包（`pip install chromadb`），collection 名 `content_kb`，embedding 用 `text-embedding-3-small`（Claude API 或 OpenAI）。每条 item 的 embedding 输入 = `title + " " + caption + " " + transcript`（截断至 2000 tokens）。

**多模态 ingestion 流程**（逐类型，**全部后台批处理**）：

```
文档（.md/.txt/.pdf）:
  1. 解析文本，按 1000 token chunk，每 chunk 一条 item
  2. title = 文件名，tags = 路径推断，caption = 首 200 字
  3. 写 catalog + fts + vector

图片（.jpg/.png/.webp）:
  1. 调 Claude vision API：生成 200 字描述 + 5 个标签 → caption / tags
  2. 原图 copy 到 workspace/media-store/<hash>.<ext>
  3. 写 catalog（modality=image）+ fts（caption）+ vector（caption embed）

视频（.mp4/.mov）:
  1. ffmpeg 抽关键帧（每 30s 一帧，max 10 帧）
  2. 逐帧 Claude vision caption，拼接为 transcript
  3. （可选）本地 whisper 提取字幕，追加到 transcript
  4. 原片 copy 到 workspace/media-store/<hash>.<ext>
  5. 写 catalog（modality=video，duration_s/width/height）+ fts + vector

所有类型:
  - 写 graph edges：同书名的 item 互连（same_book），同 tag 互连（same_tag）
  - 新增/变更检测：比对 file_hash，已处理跳过（断点续跑）
  - 并发限制：图片最多 3 并发，视频 1 并发（macOS 本地友好）
```

**检索接口**：

```
content-runtime kb search --query "<text>" --modality doc|image|video|all --topk N

实现步骤:
  1. vector search: ChromaDB.query(query_texts=[query], n_results=topk*2, where={modality: ...})
  2. fts search: SELECT id FROM items_fts WHERE items_fts MATCH '<query>' LIMIT topk*2
  3. 合并去重，按向量相似度排序，取 topk
  4. 返回: [{id, modality, source_path, title, caption, score}]
  5. 使用方必须回读 source_path 原文件（索引是候选，原文件是事实源）
```

**留存 / 清理**：千级规模，半年清理 job（`kb gc --older-than 180d`）按 `last_hit_at` 先归档候选（`status=archived`）再删；不动 `workspace/media-store/` 中用户放置的原始文件，只删由 ingest 复制进来的副本。

### L6 内容组装层（content-runtime，从零构建）

**`skills/content-generate/scripts/content_runtime.py`** —— 统一 CLI：

```
content-runtime kb ingest   --src <folder> [--modality auto|doc|image|video] [--limit N] [--resume]
content-runtime kb search   --query "<text>" [--modality doc|image|video|all] [--topk N] [--json]
content-runtime kb index    --rebuild [str|fts|vector|graph|all]
content-runtime kb gc       --older-than 180d [--dry-run]

content-runtime media probe  <file>
content-runtime media assemble --spec <plan.json> --out <dir>
  # plan.json 格式:
  # {
  #   "type": "xiaohongshu",
  #   "cover": "<image source_path>",
  #   "body": "<文案文本>",
  #   "images": ["<path1>", "<path2>"],
  #   "clips": [{"src": "<video path>", "start": 10, "end": 30}]
  # }

content-runtime publish package --platform xiaohongshu|moments --in <dir>
  # 产成品包：resize 图片到平台规格，生成发布清单 publish-checklist.md，不自动发布
```

写操作（ingest/assemble/publish）均需 `--allow-write` 参数（写门禁），防止 Agent 误触发写入。

**组装编排流程**（由 content-generate skill 驱动）：

```
1. 需求解析: 主题 / 形态 / 风格 / 约束 / 数量
2. kb search: 取 topk 候选（图片/视频/文档）
3. 回读候选: 读 source_path，确认相关性，筛选 3-5 个素材
4. Claude 生成: 输入「需求 + 素材描述」→ 输出文案 + plan.json 草稿
5. media assemble: content-runtime media assemble --spec plan.json --out outputs/...
6. publish package: content-runtime publish package --platform xiaohongshu --in outputs/...
7. 预览确认: 列出成品包文件树 + 文案，等用户确认后手动发布
```

---

## 触发机制汇总（软/硬）

| 管道段 | 软/硬 | 实现 |
|---|---|---|
| 输入分类路由 | 软 | `rules/core-routing.md`（模型判语义） |
| 执行 | 软 | content-generate skill + content-runtime 调本地 KB/组装 |
| 输出收尾 | **硬** | `Stop`/`notify` hook → `finalize.py record` |
| 媒体 ingest | **硬（定时）** | scheduler job 每天 02:00 后台批处理 |
| 自学习 | **硬（定时）** | scheduler job 每周一 → `agent_learning_review.py` |
| 规则晋升 | 半（人确认） | 候选 → 用户确认 → 写 rules/memory |

---

## 数据流

```text
输入 → 路由 → 执行(检索素材+文案生成+组装) → outputs/ → [硬]收尾沉淀
                                                              ↓ 定时
                      事实层(workspace/daily/ + KB 检索日志)
                                        ↓ agent_learning_review.py
            候选(workspace/agent-learning/) → 用户确认 → 晋升(rules/memory)

媒体: 用户文件夹 → kb ingest(caption/抽帧/字幕) → media-store(副本) + KB(文本索引/向量/FTS) → 检索回读
```

---

## 技术栈（全部需安装/构建）

| 层 | 库/工具 | 用途 |
|---|---|---|
| 向量检索 | `chromadb` | 本地向量库，collection 存 workspace/kb/vector/ |
| 全文检索 | SQLite FTS5 trigram | 中文子串模糊搜索，内置于 Python sqlite3 |
| 文案生成 | Claude API（anthropic SDK） | 文案/脚本/分镜/caption 生成 |
| 媒体处理 | `ffmpeg`（CLI）, `Pillow` | 视频抽帧/裁剪/字幕叠加，图片 resize/拼版 |
| 图片 caption | Claude vision API | 图片描述 + 标签生成 |
| 视频字幕（可选） | `faster-whisper` 或 `whisper.cpp` | 本地 ASR，按需安装 |
| 调度 | `apscheduler` | 定时 job（ingest/learn/gc） |
| PDF 解析（可选） | `pdfplumber` | 文档 ingestion |

---

## content-runtime 接口完整规格

### CLI 接口（写操作需 `--allow-write`）

```text
content-runtime kb ingest   --src <folder> [--modality auto|doc|image|video] [--limit N] [--resume]
content-runtime kb search   --query "<text>" [--modality doc|image|video|all] [--topk N] [--json]
content-runtime kb index    --rebuild [str|fts|vector|graph|all]
content-runtime kb gc       --older-than 180d [--dry-run]

content-runtime media probe  <file>
content-runtime media assemble --spec <plan.json> --out <dir> [--allow-write]
content-runtime publish package --platform xiaohongshu|moments --in <dir> [--allow-write]
```

### ingestion 批处理细节

1. 扫描 `--src` 文件夹，读 catalog.db 比对 `file_hash`，只处理新增/变更。
2. 逐文件按 modality 处理（见 L5 多模态 ingestion 流程）。
3. 写四索引：catalog（str：modality/书名/标签/来源/时长/hash）+ FTS + ChromaDB + graph edges。
4. 限流：`--limit` 控制单次批量，图片 3 并发上限，视频 1 并发；长任务可断点续跑（`--resume`）。
5. 触发：手动 `kb ingest` 或 scheduler 定时；视频一律后台。

---

## 分阶段实现（P0 → P4，按顺序构建）

### P0 基础骨架（大脑层 + 路由 + 收尾）

**需构建**：
- `AGENTS.md` 项目入口（写明 skill/rules 加载约定）
- `rules/core-routing.md`（从 L1 路由规格直接复制）
- `rules/core-safety.md`（写门禁、发布确认、红线）
- `skills/content-generate/SKILL.md`（从 L2 规格直接复制）
- `scripts/finalize.py`（从 L3 规格实现，支持 `record` / `snapshot`）
- Claude Code `settings.json` 配置 Stop hook

**验证**：
- 输入 5 类对话（闲聊/问答/搜索/设计/内容生成），验证路由分类正确
- 手动调 `finalize.py record`，确认 `workspace/daily/` 生成 session 文件
- 改一个文件，触发 Stop hook，确认 finalize 自动执行

### P1 本地 KB（文档 + 图片）

**需构建**：
- `catalog.db` 建表 SQL（见 L5 索引设计）
- `content_runtime.py` 的 `kb ingest` 命令（支持 doc + image）
- `content_runtime.py` 的 `kb search` 命令（FTS + vector 混合检索）
- ChromaDB collection 初始化

**验证**：
- `kb ingest --src ./test-data --modality image --limit 5`，确认 catalog + FTS + vector 写入
- `kb search --query "数学思维" --modality all --topk 5`，确认召回并人工核对相关性

### P2 图文组装

**需构建**：
- `content_runtime.py` 的 `media assemble`（图文拼版，Pillow 实现）
- `content_runtime.py` 的 `publish package`（小红书/朋友圈成品包）
- content-generate skill 完整执行流程（SKILL.md 扩充）

**验证**：端到端出一组小红书图文（选题「数学思维书单」），检查 outputs/ 成品包结构完整

### P3 视频 ingestion + 短视频组装

**需构建**：
- `kb ingest --modality video`（ffmpeg 抽帧 + Claude vision caption + 可选 whisper 字幕）
- `media assemble` 扩展短视频时间线（选 KB 视频片段 + ffmpeg 拼接 + 字幕叠加）

**验证**：端到端出一条短视频（选取 KB 内某本书的视频片段 + 配文案字幕）

### P4 调度 + 自学习闭环

**需构建**：
- `apps/scheduler/scheduler.py`（APScheduler，读 jobs.json）
- `scripts/agent_learning_review.py`（扫 daily/、生成候选）
- `apps/scheduler/jobs.json`（三个 job：weekly_learn / media_ingest / kb_gc）

**验证**：触发一次 `agent_learning_review.py`，确认 `workspace/agent-learning/` 生成候选文件；人工确认一条候选并晋升到 `rules/`

---

## 运行态 / 存储 / 安全

- 运行日志：`runs/content-runtime/<YYYY-MM-DD>.log`（不进 Git）
- 媒体原件副本：`workspace/media-store/<hash>.<ext>`（不进 Git）
- 产出：`outputs/YYYY-MM-DD/content/<slug>/`
- 安全：外部发布预览确认；`--allow-write` 写门禁；不把 `media-store` 绝对路径写入产出文件；API key 存 `.env`（进 `.gitignore`）

---

## 验证策略

- **P0**：路由分类回归（5 类各 2 个样例）+ hook 触发实测
- **P1-P3**：每阶段一个端到端样例（ingest → search → assemble → package）+ KB 召回人工回读
- **P4**：一轮完整 learn → 候选 → 晋升 → validate.sh 通过
- **`scripts/validate.sh`**：依次检查 catalog.db 可读、ChromaDB collection 存在、content-runtime 各 domain help 可执行、finalize.py 无语法错误

---

## 待补充参数

**全部已锁定 —— 本设计已是可开工基线。**

- 媒体生成：不生成（只检索组装），文案由 Claude API 生成
- embedding：caption 文本向量，ChromaDB + text-embedding-3-small
- 部署/规模：全本地 macOS，千级 + 半年窗口 + 半年清理 job
- 交付：小红书 + 朋友圈，成品包 + 手动发布
- 领域：教育类图书；素材来源：用户指定本地文件夹
- 视频：macOS 本地可承受为准，后台限流，whisper 后置可选
- 索引：str(SQLite) / fts(FTS5 trigram) / vector(ChromaDB) / graph（最小化关系表）
- 需求入口：日常对话，router 规则判别，无独立队列

构建按「分阶段实现 P0 → P4」推进。
