# 本地内容生成 Agent

运行在 macOS 本地的 AI 内容生成 Agent。接受日常对话输入，路由到对应处理流程，
检索本地知识库（文档/图片/视频），生成教育类图书内容（小红书/朋友圈图文、短视频），
每轮任务结束沉淀 session 记录，定期自学习进化规则。
本地运行与本地存储；文案与图片/视频 caption 可调用 Claude / Anthropic API；
无自动发布，不调用外部发布 API，不使用图片/视频生成模型。

---

## 启动加载

每次启动按顺序读取：

1. `rules/core-routing.md` — 输入分类与路由规则
2. `rules/core-safety.md` — 安全边界与写门禁
3. `memory/summary.md` — 最小启动记忆（如文件存在则读取）

---

## Skill 触发约定

Skills 位于 `skills/<name>/SKILL.md`，分两类：
- **处理类**（content-generate）：由路由器（`rules/core-routing.md`）按用户输入语义触发。
- **收尾类**（finalize）：不由输入触发，而由**每轮结束规则 / Stop hook 转交**。

每轮流程：
1. 读 `rules/core-routing.md` 做语义分类
2. 命中处理类 skill → 先说「正在使用 <name> skill，原因：<分类理由>」，读 `skills/<name>/SKILL.md`，**严格按步骤执行，不跳步**
3. 未命中 → 按 `rules/core-routing.md` 末尾「默认行为」内联处理
4. 一轮结束 → 转交 **finalize skill**（`skills/finalize/SKILL.md`），实质性任务才记录

---

## 可用 Skills

| Skill | 类别 | 触发 |
|---|---|---|
| `content-generate` | 处理类 | 路由器按输入触发：内容生成 / 图文 / 短视频 / 书单 / 文案 / 读后感 / 书评 / 知识卡片 |
| `finalize` | 收尾类 | 每轮实质性任务结束，由结束规则 / Stop hook 转交 |

---

## 收尾规则

每轮结束转交 **finalize skill**（`skills/finalize/SKILL.md`）处理。

**记录的情况**（实质性任务）：执行了处理 skill / 写了文件 / 改了 rules/skills/memory。
- 显式：`python scripts/finalize.py record --summary "..."`
- 兜底：Stop hook 跑 `python scripts/finalize.py hook`（仅实质性任务信号时写，避免空记录）

**不记录**：闲聊、纯问答、只读查询、任务被取消。

---

## 冲突优先级

```
system prompt
  > 用户当轮明确要求
  > rules/core-*（启动加载的规则）
  > skill（SKILL.md 中的步骤）
  > memory（summary.md / profile.md）
```

---

## 关键路径

```
rules/core-routing.md       输入分类与路由（唯一事实源）
rules/core-safety.md        安全边界与写门禁（唯一事实源）
skills/content-generate/    内容生成 skill
scripts/finalize.py         收尾记录
scripts/agent_learning_review.py  自学习候选生成
apps/scheduler/             定时任务
workspace/kb/               本地知识库（LanceDB：lance/）
workspace/daily/            Session 记录（自我进化事实源）
outputs/                    任务产出
```

---

## 恢复点

若 `workspace/resume/` 存在最新恢复点文件（`YYYY-MM-DD-<id>.md`），提示用户：
「发现未完成任务恢复点（YYYY-MM-DD），是否继续？」
等待用户决定，不自动读取或继续。
