---
name: workbench-execute
description: "Execute concrete local tasks for the personal workbench, including code edits, file generation, shell commands, validation, service startup, tmux/runtime fixes, and Git delivery boundaries after the user has authorized execution."
metadata:
  short-description: "本地文件修改、命令执行、验证和交付边界"
---

# workbench-execute Skill

## 触发条件

当用户明确要求执行、修改、实现、修复、生成文件、启动服务、跑测试、验证或提交时使用本 skill。

不触发执行的情况：

- 用户只是说“确认下”“评估”“看是否合理”“提出建议”“只读核查”。
- 用户只说“继续”，但上一轮仍是只读确认流程。
- 任务涉及批量删除、远端写入、权限扩大或核心安全规则变更，且用户尚未明确授权。

## 执行流程

### 步骤 1：检查工作区

先运行：

```bash
git status --short
```

识别本轮要改的文件和已有未提交改动。不得回滚或覆盖用户已有改动。

### 步骤 2：读取相关上下文

改动前读取相关代码、规则、设计和脚本，沿用当前项目风格。搜索优先使用 `rg`。

### 步骤 3：实施最小变更

只改完成目标必需的文件。复杂任务按阶段执行；每阶段先说明目标、范围、验证方式和停止条件。

### 步骤 4：验证

按影响范围运行最小必要验证。工作台、skill、rules 或脚本变更通常至少运行：

```bash
bash scripts/validate.sh --quick
```

前端脚本变更补 `node --check`；Python 变更补 `python3 -m py_compile ...`；服务变更需要启动或健康检查。

### 步骤 5：交付边界

说明改了什么、验证结果、未验证原因和剩余风险。涉及 Git 暂存/提交时，先展示拟提交文件和 Conventional Commit message，等待用户确认后再执行。

### 步骤 6：收尾

本轮有文件修改或实质性任务完成时，转 `workbench-finalizer` 写 session 摘要。

## 输出契约

- 输出：改动摘要、关键文件、验证命令和结果、剩余风险、是否需要用户确认提交。
- 成功标准：目标实现、验证通过、未混入无关文件。
- 部分完成：实现或验证存在缺口时，明确阻塞原因和可恢复位置。
- 失败：核心目标未完成或验证失败，停止推进并说明下一步。

## 安全边界

不得主动执行 `git reset --hard`、`git checkout -- <file>`、强制覆盖、批量删除、远端写入、权限扩大或提交无关脏改。不得写入 secret、token、cookie、private key、完整 JWT 或带凭证的配置。

## 辅助脚本

本 skill 提供轻量本地脚本：

```bash
bash skills/workbench-execute/scripts/phase-status.sh
bash skills/workbench-execute/scripts/phase-checkpoint.sh P0
bash skills/workbench-execute/scripts/phase-validate.sh
```

这些脚本只做状态、checkpoint 和 quick validation，不替代人工判断。

## 验证

修改本 skill 后运行：

```bash
bash skills/workbench-execute/scripts/phase-status.sh
bash skills/workbench-execute/scripts/phase-checkpoint.sh P0
bash skills/workbench-execute/scripts/phase-validate.sh
bash scripts/validate.sh --quick
```

## 完成标准

最终回复必须说明实际执行内容、修改文件、验证命令与结果、未验证缺口，以及是否已经或是否需要 `workbench-finalizer` 收尾。
