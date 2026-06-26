---
name: agent-skill-create
description: "Create, rename, update, review, and validate project-local skills for the personal workbench under skills/<name>/SKILL.md. Use when a reusable workflow should become a skill or an existing skill needs metadata, routing, scripts, or validation updates."
metadata:
  short-description: "创建、维护和验证项目本地 skill"
---

# agent-skill-create Skill

## 触发条件

当用户要求新增、迁移、改名、合并、审查或维护本项目 `skills/` 下的能力时使用本 skill。

当前项目的 skill 发现机制很简单：

```text
skills/<skill-name>/SKILL.md
```

`runtime/skill_registry.py` 会扫描这些文件，并通过 GUI 的 Skill 能力面板展示。当前项目不要求 `skills/index.json` 或 `agents/openai.yaml`。

## 执行流程

### 步骤 1：判断是否值得成为 skill

只有可重复、可触发、边界稳定、能验证的流程才沉淀为 skill。一次性任务线索留在 `outputs/`、`workspace/` 或当前对话中。

### 步骤 2：命名

使用小写 hyphen 名称，例如 `workbench-design`、`knowledge-search`。名称要表达 owner，不使用 `misc`、`helper`、`flow` 这类无信息量名称。

### 步骤 3：编写 SKILL.md

建议结构：

```text
---
name: <skill-name>
description: "<English trigger-focused description>"
metadata:
  short-description: "<中文短说明>"
---

# <skill-name> Skill

## 触发条件
## 执行流程
## 输出契约
## 安全边界
## 验证
## 完成标准
```

正文中文为主，保留命令、路径、字段名原文。

### 步骤 4：添加脚本（可选）

只有确定性、可复用、需要反复执行的动作才放到 `skills/<skill>/scripts/`。运行产物不得写入 skill 目录。

### 步骤 5：验证发现与执行

运行：

```bash
bash scripts/validate.sh --quick
python3 - <<'PY'
from pathlib import Path
from runtime.skill_registry import SkillRegistry
skills = SkillRegistry(Path("skills"), Path(".")).list()
print([item["name"] for item in skills])
PY
```

确认 GUI `/api/skills` 能看到新 skill 时，可重启工作台服务并检查。

## 辅助脚本

可用脚手架：

```bash
python3 skills/agent-skill-create/scripts/scaffold_skill.py \
  --name <skill-name> \
  --description "<English trigger description>" \
  --short-description "<中文短说明>" \
  --allow-write
```

没有 `--allow-write` 时只预览将写入的路径。

## 输出契约

- 输出：新增或更新的 skill 路径、触发边界、脚本清单、验证结果。
- 成功标准：`skills/<name>/SKILL.md` 存在，名称一致，GUI Skill Registry 可发现。
- 部分完成：缺脚本、缺验证或存在旧名残留时说明缺口。

## 安全边界

不得迁入旧项目的业务痕迹、运行日志、凭证、远端目标、用户隐私或一次性任务记录。删除或改名 skill 前必须说明影响范围并得到用户确认。

## 验证

修改本 skill 后运行：

```bash
python3 -m py_compile skills/agent-skill-create/scripts/scaffold_skill.py
bash scripts/validate.sh --quick
```

## 完成标准

最终回复必须说明：新增/修改/删除了哪些 skill，旧名残留是否清理，当前 registry 能否发现，以及运行了哪些验证。
