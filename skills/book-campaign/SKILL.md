---
name: book-campaign
description: "Plan book-operation campaigns across Xiaohongshu, Moments, and parent-group channels, including calendar, content angles, material needs, CTA, review gates, and handoff to content generation."
metadata:
  short-description: "图书运营活动节奏、渠道组合和内容计划"
---

# book-campaign Skill

## 触发条件

当用户要求“做一周运营计划 / 活动节奏 / 一本书怎么发 / 多渠道联动 / 小红书朋友圈群怎么排期 / 选题日历”时使用本 skill。

它负责计划，不直接生成全部文案；具体单条内容交给 `content-generate`。

## 执行流程

### 步骤 1：确认目标

明确目标是引流、转化、触达、资料整理、复盘还是活动预热；确认图书、周期、渠道、频次和素材现状。

### 步骤 2：读取图书档案和素材

优先读取 `book-profile` 产物；没有档案时先建议建档。需要素材证据时用 `knowledge-search`。

### 步骤 3：制定节奏

输出渠道节奏表：

- 小红书：选题、标题方向、配图/视频需求、标签方向。
- 朋友圈：短文案方向、配图、互动问题。
- 家长群：话术、发送时机、软 CTA、风险提示。

### 步骤 4：列出素材缺口

标记缺少的图片、视频、产品页、样张、家长问题、讲解素材，并交给 `book-asset` 或 `book-media`。

### 步骤 5：生成计划文件

需要保存时写入：

```text
outputs/YYYY-MM-DD/campaign/<book-or-topic>/campaign-plan.md
```

### 步骤 6：交接执行

计划确认后，把单条任务交给 `content-generate`，发布前交给 `content-compliance-review`。

## 输出契约

- 输出：周期计划、渠道矩阵、每日主题、素材需求、CTA、审核点、下一步生成任务。
- 成功标准：计划能直接拆成内容生成任务，且不硬广、不制造焦虑。
- 部分完成：图书资料不足或目标不明确时，列出待确认项。

## 安全边界

不承诺提分、升学、保过或确定效果。不安排自动发布或群发。涉及对外发布、批量群发或未公开活动，必须等用户确认。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际计划交付前，检查每个渠道是否有明确素材和审核点。

## 完成标准

最终回复说明计划路径、周期、渠道、核心主题、素材缺口和下一步内容生成入口。
