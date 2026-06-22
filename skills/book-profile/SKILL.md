---
name: book-profile
description: "Create and maintain reusable book product operation profiles with selling points, grade range, parent concerns, content angles, compliance notes, source materials, and reusable prompts for content generation."
metadata:
  short-description: "建立和维护单本图书运营档案"
---

# book-profile Skill

## 触发条件

当用户要求“给这本书建档 / 梳理卖点 / 总结适合年级 / 做图书运营档案 / 以后生成内容先参考这本书信息”时使用本 skill。

它产出可复用档案，不直接生成帖子；具体内容生成交给 `content-generate`。

## 执行流程

### 步骤 1：收集事实来源

优先使用用户提供材料、本地 KB、产品文档、历史内容包和已确认素材。需要本地事实时先走 `knowledge-search`。

### 步骤 2：回读来源

索引命中只是候选；写入档案前必须回读源文件。没有依据的字段标记为“待确认”。

### 步骤 3：生成档案

建议写入：

```text
workspace/book-profiles/<book-slug>/profile.md
workspace/book-profiles/<book-slug>/sources.json
```

档案字段：

- 书名 / 系列 / 学科 / 年级段。
- 核心卖点和证据来源。
- 适合家长关注点。
- 不适合或需谨慎表达的边界。
- 内容角度：小红书、朋友圈、家长群。
- 素材路径和历史成品包。
- 合规禁区和不可编造项。

### 步骤 4：交叉检查

检查是否存在夸大效果、提分承诺、焦虑化表达、无来源卖点。发现缺口时列为待确认，不写成事实。

### 步骤 5：交接内容生成

档案完成后，后续 `content-generate`、`book-campaign` 和 `content-compliance-review` 应优先参考该 profile。

## 输出契约

- 输出：profile 路径、来源清单、待确认字段、可复用内容角度。
- 成功标准：档案字段完整，关键卖点有来源支撑。
- 部分完成：缺产品资料、缺素材或证据不足时，保留待确认项。

## 安全边界

不得编造书名、效果、评价、价格、活动、真实家长反馈或学员信息。不得写入未公开价格策略、家长/学生隐私或内部敏感内容。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际建档后至少人工检查来源是否可追溯。

## 完成标准

最终回复说明档案路径、关键卖点、待确认项，以及后续可用于哪些内容场景。
