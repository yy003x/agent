---
name: agent-learn
description: "Review personal workbench usage, repeated friction, failed runs, and external project practices; extract learning candidates and promote confirmed improvements into memory, rules, skills, scripts, or templates only after validation and user confirmation."
metadata:
  short-description: "个人工作台自学习候选、复盘和能力晋升"
---

# agent-learn Skill

## 触发条件

当用户要求“优化你自己”“复盘”“学习/借鉴某个项目”“复用能力”“让你更懂我”“总结反复问题”“生成学习候选”时使用本 skill。

本 skill 适合：

- 从其它项目的 rules、skills、design 中提炼可复用机制。
- 审查本项目 session、KB 搜索、outputs 中的重复摩擦。
- 把稳定偏好、可复用流程、模板或执行规则整理成候选。
- 在用户确认后，交给 `workbench-execute` 落地并验证。

不适合把外部项目整包复制、搬移凭证/运行态/任务数据，或未经确认直接修改长期规则。

## 执行流程

### 步骤 1：读取来源

按用户指定范围读取来源项目、当前项目 session、rules、skills、design 或 outputs。只复用机制，不搬移来源项目业务痕迹、私有配置、远端目标或一次性任务数据。

### 步骤 2：抽象候选

区分三类内容：可复用机制、不搬移内容、待确认候选。候选必须说明来源、建议、影响范围、晋升目标、验证方式和不做事项。

### 步骤 3：生成候选文件

当前项目的确定性脚本是：

```bash
python3 scripts/agent_learning_review.py --dry-run
python3 scripts/agent_learning_review.py
```

脚本会写入：

```text
workspace/agent-learning/candidates-YYYY-MM-DD.md
```

### 步骤 4：等待确认

长期资产变更必须等待用户确认。确认后再进入 `workbench-execute` 修改 `memory/`、`rules/`、`skills/`、`scripts/` 或模板。

### 步骤 5：验证与记录

晋升后运行：

```bash
bash scripts/validate.sh --quick
```

失败则停止并说明原因；必要时用 `workbench-finalizer` 记录 partial。

## 输出契约

- 输出：候选摘要、来源路径、晋升目标、风险、验证方式和需要用户确认的问题。
- 文件产物：`workspace/agent-learning/candidates-YYYY-MM-DD.md` 或用户指定路径。
- 成功标准：候选可验证、可回滚、无来源项目业务痕迹。

## 安全边界

不得搬移 credential、private config、runtime records、远端写入目标、客户/学生/家长隐私、来源项目一次性任务数据。不得自动改 `rules/core-safety.md`。不得把一次观察直接写入长期 memory。

## 验证

修改本 skill 后运行：

```bash
python3 scripts/agent_learning_review.py --dry-run
bash scripts/validate.sh --quick
```

## 完成标准

最终回复必须说明读取了哪些来源、保留了哪些可复用机制、明确不搬移哪些内容、候选或改动落在哪里，以及验证结果。
