# 安全边界规则

本规则在每次启动时加载，在所有操作中持续有效。
违反本规则的操作，必须停下来告知用户，不得继续执行。

---

## 写操作门禁

以下操作必须带 `--allow-write` 参数，且在执行前向用户展示将要写入的目标路径：

- `content_runtime.py kb ingest`
- `content_runtime.py kb index --rebuild`
- `content_runtime.py kb gc`
- `content_runtime.py media assemble`
- `content_runtime.py publish package`

**没有 `--allow-write` 时**：只做 dry-run，打印将要执行的操作，不实际写入。

---

## 发布门禁

向外部平台（小红书、朋友圈）发布内容之前：

1. 必须展示成品包完整预览（标题 + 正文 + 图片列表 + 标签）
2. 等用户明确说「发布」或「确认」
3. 不得自动发帖，不得调用任何外部发布 API
4. 发布动作由用户手动完成

---

## 敏感信息保护

- 不得把 `.env` 中的 key/token/password 写入任何 md/json/log 文件
- 不得把 `workspace/media-store/` 的绝对路径写入对外输出（publish-checklist.md 中可写相对路径）
- 不得把用户原始对话文本写入 workspace/daily/（session 记录只写摘要）

---

## 不可逆操作确认

执行以下操作前必须停下来，展示影响范围并等用户确认：

- 删除文件（任何目录）
- `git reset --hard` 或 `git checkout -- <file>`
- `kb gc` 正式执行（非 dry-run）
- 修改 `rules/core-*.md`（核心规则变更）
- 修改 `skills/*/SKILL.md` 中的执行步骤

---

## Git 操作边界

- 写操作前先看 `git status`，确认没有其他未提交改动
- 暂存只 `git add <本轮文件>`，不用 `git add --all`
- 不主动 `push`，除非用户明确要求
- main 分支不强制 push

---

## 自我进化边界

- 任何候选晋升必须有用户明确 accept，不能自动晋升
- `rules/core-safety.md`（本文件）不得通过候选晋升自动修改
- 晋升后必须跑 `bash scripts/validate.sh`，失败则回滚
