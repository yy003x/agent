---
name: workbench-research
description: "Perform source-backed research for the personal workbench, including external current facts, platform rules, competitor references, tool comparisons, market context, tutorials, and evidence packs. Use when local knowledge is insufficient or facts may have changed."
metadata:
  short-description: "带来源的外部调研、平台规则和竞品资料整理"
---

# workbench-research Skill

## 触发条件

当任务需要外部来源或当前事实时使用本 skill，包括：

- 小红书、朋友圈、社群等平台规则和内容趋势。
- 图书运营竞品、同类账号、选题参考、活动玩法、渠道策略。
- 工具、模型、CLI、GUI、tmux、Python 依赖等现状调研。
- 用户说“查一下”“搜一下”“调研”“最新”“竞品”“业界怎么做”“有没有工具/方案/教程”。

如果只是查当前项目资料，使用 `knowledge-search`；如果调研后要形成架构方案，交给 `workbench-design`；如果要产出运营文案，交给 `content-generate`。

## 执行流程

### 步骤 1：明确研究问题

确认主题、时间范围、地域/平台、输出深度和决策目标。边界不清时只问一个关键问题。

### 步骤 2：制定来源计划

优先选择官方文档、平台规则、产品页面、公开案例、可信媒体或用户提供材料。避免把营销软文、未署名转载和过期内容当事实源。

### 步骤 3：收集并记录来源

对每个来源记录 URL/路径、发布时间或访问时间、发布方、适用边界。可变事实必须核对日期。

### 步骤 4：提取证据与判断

区分事实、推断、观点和未确认项。关键结论必须能追溯到来源；来源冲突时说明冲突。

### 步骤 5：输出报告或交接

轻量调研直接在回复中给结论和来源。需要沉淀时写入：

```text
outputs/YYYY-MM-DD/research/<topic>.md
```

需要进入设计时，补充设计输入摘要并交给 `workbench-design`；需要生成内容时，交给 `content-generate`。

## 输出契约

- 输出：核心结论、来源列表、适用边界、未确认项和下一步建议。
- 文件产物：用户要求保存或任务复杂时写 `outputs/YYYY-MM-DD/research/<topic>.md`。
- 成功标准：关键结论都有来源支撑，且日期和适用范围清楚。
- 部分完成：来源不足、冲突未解或访问受限时，标记为 partial。

## 安全边界

默认远端读取 + 本地写入报告。不执行登录态操作、远端写入、批量抓取、绕过访问控制或付费内容下载。不保存 secret、cookie、token、账号密码、完整 JWT 或用户隐私。

## 验证

修改本 skill 后至少运行：

```bash
bash scripts/validate.sh --quick
```

实际调研交付前，人工核对报告中的核心结论是否都有来源支撑。

## 完成标准

最终回复说明来源覆盖情况、输出路径、关键结论、未确认问题，以及下一步应回到 chat、design、execute 还是 content-generate。
