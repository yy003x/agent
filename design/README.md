# Agent 工作台设计包

本文件夹是「学而思图书运营 Agent 工作台」的**设计包**。
从现在开始，`00-workbench-0-1-redesign.md` 是新的主设计入口；`01-07` 是专题设计、历史阶段路线和实现参考。

---

## 目标系统

一个运行在 macOS 本地的图书运营工作台，核心循环：

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
- GUI 工作台和 CLI 两种入口
- 产品资料、文档、图片、视频的整理和知识库入库
- 检索本地知识库（文档/图片/视频），生成学而思图书运营内容（小红书/朋友圈图文、家长群话术、短视频）
- 每轮任务结束自动记录 session，定期提炼学习候选，人工确认后晋升规则/skill/memory
- **本地运行与本地存储**；这是面向图书运营日常处理的工作台，用于整理产品资料、文档、图片、视频并纳入知识库；
  智能步骤通过工作台 runtime 完成：GUI 主路径用 tmux 托管真实 `codex` / `claude` CLI 会话，默认 `codex_cli`，`claude_cli` 通过配置启用，不走 `codex exec` / `claude -p`；LLM API backend 仅作为未来可选扩展；
  无自动发布，不调用外部发布 API，不使用图片/视频生成模型。

---

## 当前实现状态

截至当前仓库基线：
- 已实现：学而思图书运营入口、入口规则、`content-generate` / `workbench-finalizer` 与个人工作台 skill 集、图书运营业务闭环 skill（素材准备、KB 同步、图书档案、活动计划、成品包、合规审核、媒体准备、会话运维）、KB ingest/search/index/gc/related/legacy、文案草稿 CLI、plan 构建 CLI、媒体组装、发布打包、自学习候选生成与 promote 命令、scheduler。
- 已修正：`validate.sh` 拆为 quick/e2e；runtime 写操作会标记 finalize activity；ingest 部分失败返回非 0；GC 使用 `last_hit_at` / `ingest_at` 双时间判断；视频 clips 会进入发布包。
- 待验证：首次 `content_runtime.py init`、最小 KB ingest/search、端到端内容生成、Stop hook、scheduler 常驻运行、完整 e2e 依赖环境。
- 待补能力：高质量平台文案模板库、真实素材样例回归集、非空旧 KB 迁移脚本。

---

## 必须阅读的顺序

1. **本文件（README.md）** — 总览与阅读入口
2. **00-workbench-0-1-redesign.md** — 新主设计，从 0 到 1 的目标态、模块边界、路线和验收
3. **06-graphical-agent-workbench.md** — GUI 工作台与 tmux runtime 技术细节参考
4. **07-runtime-workbench-product-design.md** — 运行工作台产品设计：运营视角的信息架构、tab 分层和验收
5. **04-knowledge-base.md** — 知识库层独立设计
6. **03-content-agent.md** — 内容生成应用层和成品包规格
7. **01-framework.md** — rules / skills / routing / finalize 机制
8. **02-self-evolution.md** — 自我进化规格
9. **05-implementation-steps.md** — P0-P5 已有基线实现历史路线
10. **templates/** — 可复制的入口、规则、skill、配置模板

---

## 文件夹地图

```
design/
├── README.md                            ← 你在这里
├── 00-workbench-0-1-redesign.md         ← 新主设计：工作台 0-1 重设计
├── 01-framework.md                      ← 通用框架（必读）
├── 02-self-evolution.md                 ← 自我进化规格
├── 03-content-agent.md                  ← 内容生成应用层
├── 04-knowledge-base.md                 ← 知识库层独立设计
├── 05-implementation-steps.md            ← 当前实现步骤（P0-P5）
├── 06-graphical-agent-workbench.md       ← 工作台设计（Web UI + tmux CLI runtime）
├── 07-runtime-workbench-product-design.md ← 运行工作台产品设计（运营视角 UI 分层）
└── templates/                           ← 构建时复制/实现的文件模板
    ├── AGENTS.md                        ← 学而思图书运营协作入口（完整内容，直接使用）
    ├── rules/
    │   ├── core-routing.md              ← 路由规则（完整内容，直接使用）
    │   └── core-safety.md              ← 安全规则（完整内容，直接使用）
    ├── skills/
    │   ├── content-generate/
    │   │   └── SKILL.md                ← content-generate skill（处理类，完整内容）
    │   └── workbench-finalizer/
    │       └── SKILL.md                ← workbench-finalizer skill（收尾类，完整内容）
    └── config/
        ├── settings-claude-code.json   ← legacy Stop hook 兼容模板；tmux CLI runtime 主路径显式 finalize
        └── validate.sh                 ← 启动自检脚本（需实现）
```

**约定**：
- `00` 是后续项目升级的第一事实源。
- `01-06` 的稳定细节可继续引用，但与 `00` 冲突时以 `00` 为准。
- `templates/` 下的文件是可复制产物，按路径复制到项目根目录后直接可用或补全实现。
- 实现语言：Python 3.11+（除 validate.sh 是 shell）。

---

## 环境要求

| 依赖 | 版本/要求 |
|---|---|
| 操作系统 | macOS（Apple Silicon 或 Intel） |
| Python | 3.11+ |
| ffmpeg | `brew install ffmpeg` |
| CLI runtime | `codex` / `claude` 至少一种命令可执行且已完成本机登录，用于文案生成、问答讨论、需求抽取与 caption 等智能步骤 |
| LLM API backend | 未来可选扩展；不是第一版硬依赖，启用时 key 只能放本地环境 |
| 向量/精排模型 | BAAI/bge-small-zh-v1.5（向量, 512d）+ BAAI/bge-reranker-base（精排），首次自动下载 |
| AI 运行时 | 支持 AGENTS.md + 结束 hook + skill/rules 机制的 Agent CLI |

Python 包依赖（`pip install`）：
```
lancedb sentence-transformers jieba apscheduler pillow pdfplumber
```

---

## 测试数据准备

P1–P3 验收需要一个最小素材集。构建时在项目根创建 `test-data/`，放教育图书主题样例（不依赖外部下载）：

- **文档（P1 必需，无需智能 caption / ffmpeg）**：≥3 个 `.md`/`.txt`，按目录归类、文件名即书名/主题，内容含「数学思维 / 思维导图 / 书单」等关键词。例如：
  - `test-data/数学思维/《数学之美》读书笔记.md`
  - `test-data/数学思维/小学数学思维导图书单.md`
  - `test-data/几何启蒙/几何之美推荐语.txt`
- **图片（P2，需 tmux CLI runtime caption 或手工 caption 兜底）**：1–3 张教育主题图，放 `test-data/数学思维/`。
- **视频（P3，需 ffmpeg + tmux CLI runtime caption 或手工 caption 兜底）**：1 个短视频 `.mp4`。

目录名 → `origin_dir`（category concept）、文件名 → `title`（book concept），便于验证二部图连边。
**纯文档链路（P1）可在无智能 caption、无 ffmpeg 下完整验收检索闭环**（向量+jieba-FTS+图召回+rerank）。

---

## 完成定义（Definition of Done）

以下全部通过视为目标态构建完成：

- [ ] `bash scripts/validate.sh --quick` 无错误退出
- [ ] `bash scripts/validate.sh --e2e` 无错误退出（依赖、模型、至少一种 tmux CLI runtime 准备完成后）
- [ ] 输入 6 类对话，路由分类全部正确（闲聊/问答/搜索/设计/内容生成/执行）
- [ ] `python skills/content-generate/scripts/content_runtime.py kb ingest --src <test-folder> --limit 3 --allow-write` 成功写入 LanceDB items 表
- [ ] `python skills/content-generate/scripts/content_runtime.py kb search --query "数学思维" --topk 5` 返回有效结果
- [ ] `python skills/content-generate/scripts/content_runtime.py text draft ... --allow-write` 可生成 `draft.json`
- [ ] `python skills/content-generate/scripts/content_runtime.py plan build ... --allow-write` 可生成 `plan.json`
- [ ] 完整走一次内容生成流程（需求→检索→文案→成品包→预览确认）
- [ ] `python scripts/finalize.py record` 在 `workspace/daily/` 生成 session 文件
- [ ] 显式 `finalize.py record` 可写 session；Stop hook 通过 `finalize.py hook` 做兜底且无实质信号时跳过
- [ ] `python scripts/agent_learning_review.py` 在 `workspace/agent-learning/` 生成候选文件
- [ ] `python scripts/agent_learning_review.py promote ...` 支持 reject/modify 与基于 patch 的 accept，并在 accept 后跑 `validate.sh --quick`
- [ ] `python skills/content-generate/scripts/content_runtime.py kb legacy` 可检查旧 KB 栈残留，空残留可清理
- [ ] `python apps/scheduler/scheduler.py` 启动无错误，jobs 按 jobs.json 注册成功

---

## 项目根目录结构（构建目标）

```
<project-root>/
├── AGENTS.md                          ← 从 templates/AGENTS.md 复制；当前为学而思图书运营协作入口
├── .env                               ← 仅放本地可选配置；API backend 的 key 只在启用时需要（进 .gitignore）
├── .gitignore
├── rules/
│   ├── core-routing.md                ← 从 templates/rules/ 复制
│   └── core-safety.md
├── skills/
│   ├── content-generate/             ← 处理类 skill
│   │   ├── SKILL.md                   ← 从 templates/skills/ 复制
│   │   └── scripts/
│   │       └── content_runtime.py     ← 按 03/04 实现
│   └── workbench-finalizer/           ← 收尾类 skill（工具为 scripts/finalize.py）
│       └── SKILL.md                   ← 从 templates/skills/ 复制
├── memory/
│   └── summary.md                     ← 启动记忆；记录当前实现状态与待验证项
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
