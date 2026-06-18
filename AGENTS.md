# 本地内容生成 Agent

运行在 macOS 本地的 AI 内容生成 Agent。接受日常对话输入，路由到对应处理流程，
检索本地知识库（文档/图片/视频），生成教育类图书内容（小红书/朋友圈图文、短视频），
每轮任务结束沉淀 session 记录，定期自学习进化规则。
全本地，无外部服务，无自动发布，不生成图片/视频。

---

## 启动加载

每次启动按顺序读取：

1. `rules/core-routing.md` — 输入分类与路由规则
2. `rules/core-safety.md` — 安全边界与写门禁
3. `memory/summary.md` — 最小启动记忆（如文件存在则读取）

---

## Skill 触发约定

Skills 位于 `skills/<name>/SKILL.md`。

触发流程：
1. 每轮输入，按 `rules/core-routing.md` 做语义分类
2. 分类命中 skill → 先说「正在使用 <name> skill，原因：<分类理由>」，读 `skills/<name>/SKILL.md`，**严格按步骤执行，不跳步**
3. 分类未命中 skill → 按 `rules/core-routing.md` 末尾「默认行为」内联处理

---

## 可用 Skills

| Skill | 触发类型 |
|---|---|
| `content-generate` | 内容生成 / 图文 / 短视频 / 书单 / 文案 / 读后感 / 书评 / 知识卡片 |

---

## 收尾规则

**触发 finalize 的情况**（执行了实质性任务才触发）：
- 执行了 skill（content-generate 等）
- 写了文件（代码/配置/设计文档）
- 修改了 rules/skills/memory

触发命令：`python scripts/finalize.py record`

**不触发 finalize 的情况**：闲聊、纯问答、只读查询、任务被取消

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
workspace/kb/               本地知识库（catalog.db + vector/）
workspace/daily/            Session 记录（自我进化事实源）
outputs/                    任务产出
```

---

## 恢复点

若 `workspace/resume/` 存在最新日期目录，提示用户：
「发现未完成任务恢复点（YYYY-MM-DD），是否继续？」
等待用户决定，不自动读取或继续。
