# Agent 开发完整设计包

本文件夹是「本地 AI 内容生成 Agent」的**完整开发设计包**。
AI 工具读取本文件夹后，可在无任何外部上下文的情况下独立完成全部开发。

---

## 目标系统

一个运行在 macOS 本地的 AI Agent，核心循环：

```
用户输入
    ↓
规则路由（语义分类）
    ↓
Skill 执行（检索KB + 生成内容 + 组装）
    ↓
输出（outputs/ 成品包，预览后手动发布）
    ↓
finalize 沉淀（session 记录）
    ↓
定时自学习 → 候选晋升 → 规则/skill 更新 → 自我进化
```

能力边界：
- 日常对话输入，自动分类路由到对应 skill
- 检索本地知识库（文档/图片/视频），生成教育类图书内容（小红书/朋友圈图文、短视频）
- 每轮任务结束自动记录 session，定期提炼学习候选，人工确认后晋升规则/skill/memory
- **全本地，无外部服务，无自动发布，不生成图片/视频**

---

## 必须阅读的顺序

1. **本文件（README.md）** — 总览与约定
2. **01-framework.md** — 通用 Agent 框架（rules/skills/routing/finalize 机制；这是最关键的文件）
3. **02-self-evolution.md** — 自我进化完整规格（候选判定/晋升流程）
4. **03-content-agent.md** — 内容生成应用层（runtime/组装/平台规格）
5. **04-knowledge-base.md** — 知识库层独立设计（LanceDB + bge-small-zh-v1.5 + jieba FTS + hybrid + reranker）
6. **templates/** — 直接可用的文件模板（构建时复制到项目根目录后实现/补全）

---

## 文件夹地图

```
design/
├── README.md                            ← 你在这里
├── 01-framework.md                      ← 通用框架（必读）
├── 02-self-evolution.md                 ← 自我进化规格
├── 03-content-agent.md                  ← 内容生成应用层
├── 04-knowledge-base.md                 ← 知识库层独立设计
└── templates/                           ← 构建时复制/实现的文件模板
    ├── AGENTS.md                        ← 项目入口（完整内容，直接使用）
    ├── rules/
    │   ├── core-routing.md              ← 路由规则（完整内容，直接使用）
    │   └── core-safety.md              ← 安全规则（完整内容，直接使用）
    ├── skills/
    │   └── content-generate/
    │       └── SKILL.md                ← content-generate skill（完整内容）
    └── config/
        ├── settings-claude-code.json   ← Claude Code hook 配置
        └── validate.sh                 ← 启动自检脚本（需实现）
```

**约定**：
- `templates/` 下的文件是「最终产物」，按路径复制到项目根目录后直接可用（或按注释补全实现）
- `01/02/03/04.md` 是设计文档，解释「为什么」和「怎么实现脚本/代码」
- 实现语言：Python 3.11+（除 validate.sh 是 shell）

---

## 环境要求

| 依赖 | 版本/要求 |
|---|---|
| 操作系统 | macOS（Apple Silicon 或 Intel） |
| Python | 3.11+ |
| ffmpeg | `brew install ffmpeg` |
| Claude API Key | `ANTHROPIC_API_KEY` 写入 `.env` |
| whisper（可选） | `faster-whisper`，视频字幕用，默认后置 |
| 向量/精排模型 | BAAI/bge-small-zh-v1.5（向量, 512d）+ BAAI/bge-reranker-base（精排），首次自动下载 |
| AI 运行时 | Claude Code CLI 或 Codex CLI |

Python 包依赖（`pip install`）：
```
anthropic lancedb sentence-transformers jieba apscheduler pillow pdfplumber faster-whisper
```

---

## 测试数据准备

P1–P3 验收需要一个最小素材集。构建时在项目根创建 `test-data/`，放教育图书主题样例（不依赖外部下载）：

- **文档（P1 必需，无需 API key / ffmpeg）**：≥3 个 `.md`/`.txt`，按目录归类、文件名即书名/主题，内容含「数学思维 / 思维导图 / 书单」等关键词。例如：
  - `test-data/数学思维/《数学之美》读书笔记.md`
  - `test-data/数学思维/小学数学思维导图书单.md`
  - `test-data/几何启蒙/几何之美推荐语.txt`
- **图片（P2，需 ANTHROPIC_API_KEY）**：1–3 张教育主题图，放 `test-data/数学思维/`。
- **视频（P3，需 ffmpeg + key）**：1 个短视频 `.mp4`。

目录名 → `origin_dir`（category concept）、文件名 → `title`（book concept），便于验证二部图连边。
**纯文档链路（P1）可在无 key、无 ffmpeg 下完整验收检索闭环**（向量+jieba-FTS+图召回+rerank）。

---

## 完成定义（Definition of Done）

以下全部通过视为构建完成：

- [ ] `bash scripts/validate.sh` 无错误退出
- [ ] 输入 6 类对话，路由分类全部正确（闲聊/问答/搜索/设计/内容生成/执行）
- [ ] `python skills/content-generate/scripts/content_runtime.py kb ingest --src <test-folder> --limit 3 --allow-write` 成功写入 LanceDB items 表
- [ ] `python skills/content-generate/scripts/content_runtime.py kb search --query "数学思维" --topk 5` 返回有效结果
- [ ] 完整走一次内容生成流程（需求→检索→文案→成品包→预览确认）
- [ ] `python scripts/finalize.py record` 在 `workspace/daily/` 生成 session 文件
- [ ] Stop hook 触发后 finalize 自动执行（Claude Code）
- [ ] `python scripts/agent_learning_review.py` 在 `workspace/agent-learning/` 生成候选文件
- [ ] `python apps/scheduler/scheduler.py` 启动无错误，jobs 按 jobs.json 注册成功

---

## 项目根目录结构（构建目标）

```
<project-root>/
├── AGENTS.md                          ← 从 templates/AGENTS.md 复制
├── .env                               ← ANTHROPIC_API_KEY=... （进 .gitignore）
├── .gitignore
├── rules/
│   ├── core-routing.md                ← 从 templates/rules/ 复制
│   └── core-safety.md
├── skills/
│   └── content-generate/
│       ├── SKILL.md                   ← 从 templates/skills/ 复制
│       └── scripts/
│           └── content_runtime.py     ← 按 03/04 实现
├── memory/
│   └── summary.md                     ← 初始化后手动填写
├── scripts/
│   ├── finalize.py                    ← 按 01-framework.md 规格实现
│   ├── agent_learning_review.py       ← 按 02-self-evolution.md 规格实现
│   └── validate.sh                    ← 从 templates/config/ 复制后补全
├── apps/
│   └── scheduler/
│       ├── scheduler.py               ← 按 01-framework.md 规格实现
│       └── jobs.json                  ← 按 02-self-evolution.md 内容创建
├── workspace/                         ← 不进 Git（.gitignore 排除）
│   ├── kb/
│   │   └── lance/                     ← LanceDB 数据目录（首次 init 自动创建）
│   ├── media-store/
│   ├── daily/
│   ├── agent-learning/
│   └── resume/
└── outputs/
```
