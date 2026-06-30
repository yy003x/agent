---
name: workbench-finalizer
description: "Finalize substantive personal workbench tasks by recording concise session summaries, validation results, changed-file context, residual risks, and optional handoff/resume data through apps/agent-memory/bin/finalize. Use after file changes, content generation, design output, execution, or explicit user requests to summarize or hand off."
metadata:
  short-description: "实质任务收尾、session 记录和 handoff"
---

# workbench-finalizer Skill（收尾类）

## 类别与触发

这是个人工作台的收尾类 skill，不由普通输入路由直接触发，而是在实质性任务结束时触发。

满足任一条件即收尾：

- 本轮创建、修改或删除了文件。
- 执行了 `content-generate`、`workbench-design`、`workbench-execute`、`workbench-research`、`agent-learn`、`agent-skill-create` 等处理类任务。
- 生成了 `outputs/`、`design/`、`workspace/` 中需要后续恢复或复盘的产物。
- 用户明确说“收尾”“记录一下”“总结本次任务”“做个 handoff”。

不收尾：

- 闲聊、纯问答、只读查询且未形成可复用结论。
- 用户取消任务且没有产物需要记录。

## 执行流程

### 步骤 1：判定是否实质性任务

先看本轮是否有文件变更、命令执行、内容成品、设计文档、调研报告或长期偏好。如果没有，跳过，不写空 session。

### 步骤 2：整理摘要

摘要只写 1-3 句：做了什么、产出了什么、验证结果、关键路径和剩余风险。不得写用户原始对话全文。

### 步骤 3：查看快照

必要时运行：

```bash
python3 apps/agent-memory/bin/finalize snapshot
```

用来辅助判断文件变更和状态，不替代人工总结。

### 步骤 4：写 session 记录

显式记录：

```bash
python3 apps/agent-memory/bin/finalize record \
  --skill <skill-name-or-none> \
  --status <success|partial|failed> \
  --summary "<1-3 句摘要>"
```

可选 `--handoff` 用于未完成任务或需要下次恢复的场景。

### 步骤 5：运行态标记

如果脚本写入了 `outputs/`、`workspace/` 等 ignored 目录，但暂时不能显式 record，可先标记：

```bash
python3 apps/agent-memory/bin/finalize mark \
  --skill <skill-name-or-none> \
  --status success \
  --summary "<运行态写入摘要>"
```

Stop hook 会消费标记；显式 `record` 会避免重复记录。

Stop hook 入口由 Codex 解析 stdout，`apps/agent-memory/bin/finalize hook` 必须只在 stdout 输出 hook JSON；诊断日志写 stderr。

## 输出契约

- session 输出：`workspace/daily/YYYY-MM-DD/session-<8位>.md`。
- handoff 输出：需要时写 `workspace/resume/`。
- 用户可见输出：完成状态、关键文件、验证结果、未验证原因、剩余风险。
- 成功标准：有实质任务就有简洁 session，纯查询不污染 daily。

## 安全边界

不记录原始对话全文，不记录 secret、token、cookie、private key、完整 JWT、账号密码、家长/学生隐私、内部未公开价格策略或外部发布凭证。

## 与脚本的关系

本 skill 规定“何时收尾、写什么、怎么调”；`apps/agent-memory/bin/finalize` 是实际工具，提供 `record`、`hook`、`mark`、`snapshot`。

## 验证

修改本 skill 后运行：

```bash
python3 apps/agent-memory/bin/finalize --help
bash scripts/validate.sh --quick
```

## 完成标准

最终回复必须说明是否触发收尾、记录状态、session/handoff 路径（如已写）、验证结果和剩余缺口。
