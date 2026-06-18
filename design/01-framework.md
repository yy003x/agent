# Agent 通用框架设计

本文描述适用于任何本地 AI Agent 的**通用框架**。内容生成 Agent 是这个框架的一个应用实例。
读完本文，你应该能回答：AI 启动后读什么？路由如何真正触发 skill 执行？finalize 怎么工作？

---

## 框架全景

```
┌──────────────────────────────── AI 大脑（Claude Code / Codex）─────────────────────────────┐
│                                                                                              │
│  启动：读 AGENTS.md → 加载 rules/core-*.md + memory/summary.md                             │
│                                                                                              │
│  每轮输入：                                                                                   │
│    Step 1  读 rules/core-routing.md → 做语义分类                                            │
│    Step 2  分类命中 skill → 读 skills/<name>/SKILL.md → 按步骤执行                          │
│    Step 3  分类未命中 skill → 按 core-routing.md 末尾「默认行为」内联处理                   │
│    Step 4  执行结束 → python scripts/finalize.py record（实质性任务才触发）                  │
│                                                                                              │
│  定时（scheduler.py）：                                                                      │
│    每周一 → python scripts/agent_learning_review.py → 候选 → 用户确认 → 晋升                │
│                                                                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                          │
              content-runtime CLI（检索/ingest/组装）
                          │
              ┌───────────┴───────────┐
         本地 KB 层               内容组装层
  （LanceDB + bge-small-zh）   （Claude 文案 + ffmpeg）
```

---

## 1. AGENTS.md 合约

`AGENTS.md` 是 Claude Code / Codex 在项目目录启动时**自动读取的唯一入口**。
所有「AI 应该知道什么」「应该怎么行动」的元信息都从这里出发。

### AGENTS.md 必须包含的六个模块

**① 项目概述**（1-3 句话）：这个 agent 是什么、运行在哪、核心能力。

**② 启动加载清单**：明确列出每次启动必须读取的文件。
```markdown
## 启动加载
每次启动默认读取（按顺序）：
- `rules/core-routing.md`   — 输入分类与路由
- `rules/core-safety.md`    — 安全边界与写门禁
- `memory/summary.md`       — 最小启动记忆（如存在）
```

**③ Skill 发现与触发约定**（关键，见「Skill 系统」章节）：
```markdown
## Skill 触发约定
Skills 位于 `skills/<name>/SKILL.md`。
路由命中 skill 时：先说「正在使用 <name> skill」，读 SKILL.md，严格按步骤执行，不跳步。
```

**④ 可用 Skill 索引**：每次新增 skill 时更新此表。
```markdown
## 可用 Skills
| Skill               | 触发类型                          |
|---------------------|----------------------------------|
| content-generate    | 内容生成/图文/短视频/书单/文案    |
```

**⑤ 收尾规则**：
```markdown
## 收尾
每轮实质性任务完成后执行：
  python scripts/finalize.py record
闲聊、纯问答、只读查询不触发。
```

**⑥ 冲突优先级**：
```markdown
## 优先级
system prompt > 用户当轮明确要求 > rules/core-* > skill > memory
```

完整 AGENTS.md 内容见 `templates/AGENTS.md`，直接复制使用。

---

## 2. Rules 系统

### 定义
Rules 是写给 AI 读的行为约束文件。AI 把 rules 当作「操作手册」，在每轮执行中遵守。

### 两类 Rules

| 类型 | 路径 | 加载时机 |
|---|---|---|
| Core rules | `rules/core-*.md` | AGENTS.md 列出，每次启动加载 |
| On-demand rules | `rules/od-*.md` | 由特定 skill 显式 `读取 rules/od-xxx.md` 时加载 |

### Rules 文件格式
```markdown
# 规则名称

## 规则
[用「必须 / 不得 / 优先 / 允许」等确定性语气描述行为边界]

## 例外
[例外情况，无例外则省略]
```

### 写规则的原则
- 描述「AI 必须怎么做」，不描述「代码怎么实现」
- 每条约束只在一处定义，不在多文件重复
- 规则简短：能一行说清的不写三行
- 「memory 描述事实，rules 约束行为」——不要在 rules 里存事实

---

## 3. Skill 系统

### Skill 是什么
Skill 是对一类复杂、有固定步骤序列的任务的**结构化执行规程**。
路由分类命中 skill 时，AI 读 SKILL.md 并按步骤执行，而不是自由发挥。

### Skill 解决的核心问题
| 问题 | Skill 的解法 |
|---|---|
| 任务步骤多，AI 可能跳步 | SKILL.md 强制按序执行 |
| 涉及写文件/调脚本等有副作用操作 | 明确在 SKILL.md 中写出命令，带 `--allow-write` |
| 输出格式有要求 | SKILL.md 指定输出结构 |
| 发布等操作需要人工确认 | SKILL.md 在关键步骤设「停下等确认」点 |

### 目录结构
```
skills/
└── <skill-name>/
    ├── SKILL.md              ← 主文档：触发条件 + 流程 + 输出格式 + 安全边界
    ├── scripts/              ← 该 skill 专用脚本
    │   └── *.py
    └── templates/            ← 输出内容模板（可选）
        └── *.md
```

### SKILL.md 格式规约（AI 构建 SKILL.md 时必须遵守此格式）

```markdown
# <Skill 名称>

## 触发条件
[与 core-routing.md 中该类别的描述保持一致]

## 前置检查
[执行前必须满足的条件：依赖存在/环境/权限]
- [ ] lance/ 存在（workspace/kb/lance/，运行 init 初始化）
- [ ] ANTHROPIC_API_KEY 已设置

## 执行流程
按序执行，**不得跳步**：

### 步骤 1：<名称>
<具体操作描述>
<如涉及脚本调用，写出完整命令，例如：>
python skills/content-generate/scripts/content_runtime.py kb search \
  --query "<主题>" --modality all --topk 10 --json

### 步骤 2：<名称>
...

## 输出格式
[产出物的路径结构和格式]

## 安全边界
[不能做什么；必须停下等用户确认的节点]

## 收尾
执行结束后：python scripts/finalize.py record
```

---

## 4. Routing → Skill 执行桥梁（最关键的机制）

这是整个框架最容易被误解的地方：**路由只是分类，Skill 才是执行**。
缺少这个"桥梁"约定，AI 知道分类结果却不知道下一步该干什么。

### 完整执行序列

```
用户输入
    │
    ▼
读 rules/core-routing.md
    │  按语义分类：闲聊 / 问答 / 搜索 / 设计 / 执行 / 内容生成
    │
    ├─── 命中 skill ──────────────────────────────────────────────────┐
    │                                                                   │
    │    查 AGENTS.md「可用 Skills 索引」，找到 skill name             │
    │    读 skills/<name>/SKILL.md                                     │
    │    说「正在使用 <name> skill，原因：<分类理由>」                  │
    │    按 SKILL.md「执行流程」逐步执行                               │
    │    遇写操作加 --allow-write                                       │
    │    遇发布操作停下展示预览，等用户确认                             │
    │    执行结束 → python scripts/finalize.py record                  │
    │                                                                   │
    └─── 未命中 skill ────────────────────────────────────────────────┘
         按 core-routing.md 末尾「默认行为」处理
         （闲聊：直接回答；问答：检索KB后回答；设计：讨论后产文档）
         轻量问答不触发 finalize
```

### 判断是否「命中 skill」的规则
1. 分类结果对应 AGENTS.md「可用 Skills 索引」中存在的 skill → 命中
2. 不在索引中 → 未命中，走默认行为
3. 有歧义时：选覆盖范围更广的分类；内容生成意图优先于搜索意图

---

## 5. Finalize 机制

### 目的
每轮实质性任务结束后，把「发生了什么」记录到本地事实层，供自我进化使用。

### 触发规则
| 情况 | 是否触发 finalize |
|---|---|
| 执行了 skill（content-generate 等） | ✅ 触发 |
| 写了文件（代码/配置/文档） | ✅ 触发 |
| 修改了 rules/skills/memory | ✅ 触发 |
| 纯问答、闲聊 | ❌ 不触发 |
| 只读查询（搜索 KB 后直接回答） | ❌ 不触发 |
| 任务被用户取消 | ❌ 不触发 |

### Hook 配置

**Claude Code（`.claude/settings.json`）**：
```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python scripts/finalize.py record"
          }
        ]
      }
    ]
  }
}
```

**Codex（`codex.toml`）**：
```toml
[hooks]
notify = ["python scripts/finalize.py record"]
```

### `finalize.py` 实现规格

```python
"""
每轮任务收尾记录脚本。

用法：
  python scripts/finalize.py record [--skill <name>] [--status success|partial|failed]
  python scripts/finalize.py snapshot   # 读 git status/diff，判定状态，输出 JSON

输出文件：
  workspace/daily/YYYY-MM-DD/session-<8位随机>.md

session 文件格式：
---
session_id: <uuid4>
timestamp: <ISO8601>
skill_triggered: <skill name 或 "none">
status: <success | partial | failed>
---

## 摘要
[1-3 句话：本轮做了什么、产出了什么]

## 文件变更
[git diff --stat 简报，或手动列出写入的文件路径]

## KB 命中
[检索命中的 catalog item id，如本轮未检索则省略]

实现要点：
- session_id 用 uuid4()
- timestamp 用 datetime.now(timezone.utc).isoformat()
- 摘要由 AI 在执行结束时传入（--summary "..." 参数），或从最后一条 AI 输出提取
- git diff --stat 用 subprocess 调用，失败时写 "git not available"
- workspace/daily/YYYY-MM-DD/ 目录不存在时自动创建
"""
```

---

## 6. Scheduler（定时任务）

### 实现规格

`apps/scheduler/scheduler.py`：
```python
"""
定时任务主程序，基于 APScheduler。

启动：python apps/scheduler/scheduler.py
配置：apps/scheduler/jobs.json

jobs.json 格式：
{
  "jobs": [
    {
      "id": "weekly_learn",
      "cron": "0 9 * * 1",
      "command": "python scripts/agent_learning_review.py --days 7",
      "description": "每周一 09:00 生成学习候选"
    }
  ]
}

实现要点：
- 用 BackgroundScheduler（不阻塞主线程）
- cron 字段格式：分 时 日 月 周（标准 crontab）
- command 用 subprocess.run() 执行
- 失败时记录到 runs/scheduler/YYYY-MM-DD.log
- 启动时打印所有已注册 job 及下次执行时间
"""
```

### 初始 `jobs.json`（构建时直接创建）

```json
{
  "jobs": [
    {
      "id": "weekly_learn",
      "cron": "0 9 * * 1",
      "command": "python scripts/agent_learning_review.py --days 7",
      "description": "每周一 09:00 生成学习候选"
    },
    {
      "id": "media_ingest",
      "cron": "0 2 * * *",
      "command": "python skills/content-generate/scripts/content_runtime.py kb ingest --src workspace/media-inbox --limit 20 --resume --allow-write",
      "description": "每天 02:00 后台 ingest 新增媒体"
    },
    {
      "id": "kb_gc",
      "cron": "0 3 1 */6 *",
      "command": "python skills/content-generate/scripts/content_runtime.py kb gc --older-than 180d --dry-run",
      "description": "每半年预览清理候选（dry-run，确认后手动去掉 --dry-run）"
    }
  ]
}
```

---

## 7. Memory 系统

### 用途
存储跨 session 的长期事实：用户偏好、项目阶段、已知约束。
Memory 描述「是什么」，rules 描述「必须怎么做」，两者不重叠。

### 路径与格式

`memory/summary.md`（启动时加载）：
```markdown
# 启动记忆

## 项目状态
[当前构建阶段：P0/P1/P2/P3/P4，有无未完成任务]

## 关键偏好
- [偏好 1]
- [偏好 2]

## 上次更新
YYYY-MM-DD
```

### 写入规则
- **只在自我进化候选晋升确认后**才写入，不在普通任务中自动修改
- 写入前必须展示拟写内容，等用户确认
- 不存储原始对话文本，只存抽象摘要和偏好

---

## 8. 目录结构一览

```
<project-root>/
├── AGENTS.md                          ← 入口（必须存在）
├── .env                               ← 敏感 key（进 .gitignore）
├── .gitignore
├── rules/
│   ├── core-routing.md                ← 启动加载，输入分类
│   ├── core-safety.md                 ← 启动加载，安全边界
│   └── od-*.md                        ← 按需加载（skill 指定时）
├── skills/
│   └── content-generate/
│       ├── SKILL.md
│       └── scripts/
│           └── content_runtime.py
├── memory/
│   ├── summary.md                     ← 启动加载
│   └── profile.md                     ← 按需加载
├── scripts/
│   ├── finalize.py
│   ├── agent_learning_review.py
│   └── validate.sh
├── apps/
│   └── scheduler/
│       ├── scheduler.py
│       └── jobs.json
├── workspace/                         ← .gitignore 排除
│   ├── kb/
│   │   └── lance/                     ← LanceDB（items + concepts + graph_edges 表）
│   ├── media-store/
│   ├── media-inbox/                   ← 用户放入待 ingest 的媒体
│   ├── daily/
│   ├── agent-learning/
│   └── resume/
├── outputs/
└── runs/                              ← 运行日志，.gitignore 排除
    └── scheduler/
```
