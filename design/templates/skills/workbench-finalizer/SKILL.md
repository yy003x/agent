---
name: workbench-finalizer
description: "Finalize substantive personal workbench tasks by recording concise session summaries, validation results, changed-file context, residual risks, and optional handoff/resume data through scripts/finalize.py."
metadata:
  short-description: "实质任务收尾、session 记录和 handoff"
---

# workbench-finalizer Skill（收尾类）

## 类别与触发

实质性任务结束时触发：文件变更、内容生成、设计产物、执行任务、调研报告、自学习晋升，或用户明确要求“收尾/记录/总结/handoff”。

纯闲聊、纯问答、只读查询且没有长期结论时不收尾。

## 执行流程

### 步骤 1：判定是否需要收尾

只记录实质任务，不写空 session。

### 步骤 2：整理摘要

只写 1-3 句结构化摘要，不写原始对话。

### 步骤 3：写 session

```bash
python3 scripts/finalize.py record \
  --skill <skill-name-or-none> \
  --status <success|partial|failed> \
  --summary "<摘要>"
```

需要恢复点时加 `--handoff`。

## 输出契约

`workspace/daily/YYYY-MM-DD/session-<8位>.md`；必要时写 `workspace/resume/`。

## 安全边界

不记录 secret、token、cookie、private key、完整 JWT、账号密码、用户隐私或原始对话全文。

## 验证

```bash
python3 scripts/finalize.py --help
bash scripts/validate.sh --quick
```
