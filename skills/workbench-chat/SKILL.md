---
name: workbench-chat
description: "Handle lightweight personal workbench conversations, status/path questions, explanations, routing decisions, and casual Q&A. Use when the user can be answered directly or needs to be routed to local knowledge search, research, design, execution, content generation, skill creation, learning, or finalization."
metadata:
  short-description: "个人工作台轻量对话、解释和入口分流"
---

# workbench-chat Skill

## 触发条件

这是个人 Agent 工作台的默认轻量入口，适用于：

- 闲聊、寒暄、一次性解释、路径说明、状态查询。
- 询问 GUI 工作台、CLI runtime、tmux 会话、provider、目录边界的当前行为。
- 需要判断下一步应该走哪个本地 skill，但还不需要写文件或执行命令。

不适用于：

- 图书运营文案、话术、选题、脚本等成品生成：转 `content-generate`。
- 本地知识库、design、workspace 事实查询：转 `knowledge-search`。
- 外部可变事实、平台规则、竞品、工具现状调研：转 `workbench-research`。
- 架构、方案、PRD、长期能力设计：转 `workbench-design`。
- 改文件、跑命令、启动服务、验证、Git 交付：转 `workbench-execute`。
- 实质性任务收尾、handoff、session 记录：转 `workbench-finalizer`。

## 执行流程

### 步骤 1：判断意图

先判断用户是在问轻量问题，还是已经命中更具体的工作台能力。每轮选择一个主 owner，不为了形式加载所有 skill。

### 步骤 2：直接回答或升级

能直接回答时，先结论后原因，保持简短，不写文件。需要本地事实、外部来源、设计或执行时，说明将使用的 skill 和原因，再转入对应 skill。

### 步骤 3：保护事实边界

涉及当前项目实现、配置、路径或既有产物时，不凭记忆猜测；读取真实文件或使用 `knowledge-search`。涉及最新平台规则、工具状态、价格、政策等可变信息时，使用 `workbench-research` 并给来源。

### 步骤 4：收尾判断

轻量问答不触发 `workbench-finalizer`。如果本轮后来进入写文件、执行命令或生成长期产物，任务结束后再按 `workbench-finalizer` 收尾。

## 输出契约

- 输出：直接回复、必要的本地路径引用，或明确的 skill 转交说明。
- 不输出：正式设计稿、调研报告、内容成品、代码补丁、session 记录。
- 成功标准：回答准确、边界清楚，能把任务升级到正确 owner。

## 安全边界

默认只读，不执行破坏性命令，不远端写入，不保存用户原始对话，不输出 secret、token、cookie、private key、完整 JWT 或内部敏感信息。

## 验证

修改本 skill 后至少运行：

```bash
bash scripts/validate.sh --quick
```

## 完成标准

最终回复说明本轮是直接由 `workbench-chat` 承接，还是已转给其它 skill；如果没有查证事实，不能把推测说成已验证结论。
