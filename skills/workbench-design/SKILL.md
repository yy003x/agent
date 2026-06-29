---
name: workbench-design
description: "Design personal workbench capabilities, GUI/runtime architecture, book-operation workflows, local knowledge-base flows, skill/rule changes, PRDs, technical plans, and phased implementation proposals before execution."
metadata:
  short-description: "个人工作台方案设计、PRD、架构和分阶段计划"
---

# workbench-design Skill

## 触发条件

当用户要求方案、架构、PRD、技术设计、执行计划或长期能力设计时使用本 skill，包括：

- 个人 Agent 工作台、GUI、tmux runtime、provider、skill registry、知识库流程设计。
- 图书运营流程设计：素材整理、知识库纳入、内容生成、审核、发布包、复盘。
- 规则、skill、脚本、目录边界和运行时状态的长期设计。
- 多方案比较、风险评估、阶段拆分、实现前的 Proposed Plan。

不用于轻量问答、单纯资料检索、直接代码修改或最终收尾。

## 执行流程

### 步骤 1：识别设计模式

轻量讨论只在对话中给结论；正式设计需要明确目标、非目标、事实源、约束、成功标准和输出路径。

### 步骤 2：回读现状

根据任务读取真实文件：`AGENTS.md`、`rules/`、`design/`、`apps/api/`、`apps/web/`、`apps/agentrun/`、`skills/`、`scripts/`、`workspace/kb/` 或用户指定材料。涉及项目事实时使用 `knowledge-search`；外部可变事实不足时使用 `workbench-research`。

### 步骤 3：给出方案对比

多方案任务列出 2-3 个选项、优缺点、推荐方案和不做事项。只有一个可行方案时，说明其它方向为什么不选。

### 步骤 4：形成设计

正式设计默认写入：

```text
design/<topic>.md
```

一次性讨论稿或用户明确要求临时保存时，可写入：

```text
outputs/YYYY-MM-DD/design/<topic>.md
```

### 步骤 5：拆分执行计划

给出 P0/P1/P2 或 Step 1/2/3，写清每阶段目标、修改范围、不修改范围、验证命令、失败停止条件和回滚边界。

### 步骤 6：交接执行或收尾

用户确认执行后转 `workbench-execute`。正式设计写入文件后，任务结束时转 `workbench-finalizer` 记录摘要。

## 输出契约

- 输出：设计路径、核心决策、方案对比、阶段计划、验证方式、风险和待确认项。
- 成功标准：设计能直接指导执行，且事实源、边界、验证和不做事项清楚。
- 部分完成：缺关键材料或用户决策时，列出阻塞项，不把假设包装成结论。

## 安全边界

默认本地写入设计文档。涉及删除、远端写入、权限扩大、后台服务、批量搬移或修改核心安全规则时，只给方案和影响范围，等待用户确认后才能执行。

## 验证

修改本 skill 或生成正式设计后至少运行：

```bash
bash scripts/validate.sh --quick
```

如果只是纯讨论且未改文件，可不运行验证，但需要说明未产生文件。

## 完成标准

最终回复必须说明：使用了哪些事实源、输出到哪里、推荐方案是什么、哪些不做、下一步是否进入 `workbench-execute`。
