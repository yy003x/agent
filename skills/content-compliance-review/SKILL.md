---
name: content-compliance-review
description: "Review book-operation drafts and packages for K12 marketing compliance, platform fit, factual grounding, soft CTA, no anxiety amplification, and no fabricated parent testimonials before manual publishing."
metadata:
  short-description: "运营内容合规、平台适配和事实审核"
---

# content-compliance-review Skill

## 触发条件

当用户要求“审核文案 / 看合规 / 发布前检查 / 有没有硬广 / 会不会焦虑 / 平台适配 / 事实有没有编造”时使用本 skill。

它负责审核，不负责重写完整内容；需要重写时交给 `content-generate` 或 `workbench-execute` 修改对应文件。

## 执行流程

### 步骤 1：确认审核对象

输入可以是草稿文本、`draft.json`、`publish-checklist.md`、成品包目录或用户粘贴内容。

### 步骤 2：事实回读

涉及书名、卖点、价格、活动、评价、素材事实时，必须回读来源：`book-profile`、`sources.json`、KB source path 或用户提供材料。

### 步骤 3：合规检查

检查：

- 是否承诺提分、升学、保过、确定效果。
- 是否使用极限词或绝对化表达。
- 是否制造或放大教育焦虑。
- 是否伪造家长好评、学员案例或真实身份。
- 是否泄露内部价格策略、未公开活动或个人信息。

### 步骤 4：平台适配检查

- 小红书：标题、正文、标签、图片数量、种草语气。
- 朋友圈：长度、自然度、不过度营销。
- 家长群：老师/从业者视角，软提醒，不冒充家长。

### 步骤 5：输出审核结论

需要保存时写：

```text
outputs/YYYY-MM-DD/review/<content-slug>/review.md
```

结论分为 `pass`、`needs-edit`、`blocked`。

## 输出契约

- 输出：审核等级、问题列表、风险原因、建议改法、是否可手动发布。
- 成功标准：所有高风险问题明确定位，低风险优化建议可执行。
- 部分完成：来源不足时不能判定 pass，必须标记需补来源。

## 安全边界

不伪造评价、不替用户自动发布、不降低合规标准。不能把“没查到问题”说成“已合规”，除非完成事实来源回读。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际审核时至少给出 pass / needs-edit / blocked 之一。

## 完成标准

最终回复说明审核等级、关键问题、建议修改方向、是否需要返回 `content-generate` 或 `content-package`。
