---
name: content-package
description: "Package generated book-operation drafts into delivery-ready local bundles for Xiaohongshu, Moments, and parent groups, including checklist, assets, source references, version notes, and manual publishing handoff."
metadata:
  short-description: "运营内容成品包整理、版本和发布交接"
---

# content-package Skill

## 触发条件

当用户要求“打包成品 / 整理发布包 / 做 checklist / 把草稿整理成小红书或朋友圈包 / 输出可复制话术包”时使用本 skill。

它负责交付包，不自动发布；内容初稿由 `content-generate` 产生，发布前审核可交给 `content-compliance-review`。

## 执行流程

### 步骤 1：确认输入目录

确认草稿目录、平台、是否已有 `draft.json`、`plan.json`、媒体文件和来源记录。

### 步骤 2：执行发布包命令

```bash
python3 apps/content-runtime/bin/content-runtime publish package \
  --platform <xiaohongshu|moments|wechat_group> \
  --in outputs/YYYY-MM-DD/content/<slug>/ \
  --allow-write
```

没有 `--allow-write` 时只做预览说明。

### 步骤 3：整理交付清单

检查包内是否包含标题、正文、图片/视频列表、标签、素材来源、版本号、人工发布提示和未完成项。

### 步骤 4：多平台适配

同一内容要发多个渠道时，不直接复制：小红书保留标题/标签/图文结构；朋友圈压缩表达；家长群话术更像老师视角提醒，不伪装真实家长。

### 步骤 5：交接审核

打包完成后建议进入 `content-compliance-review`。用户明确确认后才算可手动发布。

## 输出契约

- 输出：成品包目录、平台 checklist、素材清单、版本备注、人工发布说明。
- 成功标准：用户可以直接打开包手动发布或复制话术。
- 部分完成：缺媒体、缺来源或未审核时标记。

## 安全边界

不调用外部发布 API，不自动发帖、不群发。不得把绝对敏感路径、secret、用户隐私或内部价格策略写入对外 checklist。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际打包后检查 package 目录和 checklist 是否存在。

## 完成标准

最终回复说明成品包路径、平台、是否已审核、缺失项和人工发布提示。
