---
name: book-media
description: "Prepare image and video assets for book-operation content, including probing, caption readiness, format checks, visual suitability notes, source linkage, and handoff to asset ingest or content packaging."
metadata:
  short-description: "图片视频素材探测、caption 准备和内容适配"
---

# book-media Skill

## 触发条件

当用户要求“处理图片 / 处理视频 / 看素材能不能用 / 检查尺寸格式 / 做 caption / 选配图 / 视频素材整理”时使用本 skill。

它只做媒体准备和适配建议；正式知识库入库交给 `knowledge-sync`，成品包交给 `content-package`。

## 执行流程

### 步骤 1：确认媒体目标

确认媒体用于入库、内容配图、短视频、封面、朋友圈配图还是家长群素材。

### 步骤 2：探测文件

单文件检查：

```bash
python3 skills/content-generate/scripts/content_runtime.py media probe <file>
```

记录分辨率、时长、格式、是否可读、是否需要转码或裁剪。

### 步骤 3：caption 准备

如果已有 caption，检查是否能支撑检索和内容生成；没有 caption 时标记“需 tmux CLI runtime 或人工 caption”。不要凭文件名编造画面内容。

### 步骤 4：内容适配

判断素材适合：

- 小红书封面 / 轮播图。
- 朋友圈配图。
- 家长群说明图。
- 短视频素材。
- 仅做 KB 参考，不适合对外。

### 步骤 5：输出媒体准备报告

建议写入：

```text
outputs/YYYY-MM-DD/media-prepare/<batch-slug>/media-report.md
```

### 步骤 6：交接

可入库素材交给 `book-asset` / `knowledge-sync`；可发布素材交给 `content-package`。

## 输出契约

- 输出：媒体报告、可用/不可用清单、caption 缺口、平台适配建议。
- 成功标准：每个媒体文件都有可读性和用途判断。
- 部分完成：依赖缺失或文件不可读时列明。

## 安全边界

不修改原始媒体，不自动转码覆盖，不输出隐私画面细节，不把不可对外素材标为可发布。

## 验证

修改本 skill 后运行：

```bash
bash scripts/validate.sh --quick
```

实际执行时至少对样本文件运行 `media probe` 或说明无法运行原因。

## 完成标准

最终回复说明媒体报告路径、可用素材、需补 caption、需人工确认项和下一步交接 skill。
