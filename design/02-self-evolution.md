# 自我进化规格

Agent 通过历史 session 记录，定期提炼学习候选，人工确认后晋升到规则/skill/memory，形成自我进化闭环。
本文描述完整循环：事实源 → 候选生成 → 用户确认 → 晋升 → 验证。

---

## 总体循环

```
事实源
  workspace/daily/**/*.md         ← 每轮 finalize 产生的 session 记录
  workspace/kb/search-log.jsonl   ← KB 检索日志（content-runtime 写入）
  outputs/**/*.md                 ← 成品包发布清单

        ↓ 每周一 09:00（scheduler 触发）
        
python scripts/agent_learning_review.py --days 7

        ↓
        
workspace/agent-learning/candidates-YYYY-MM-DD.md

        ↓ AI 读取候选，向用户逐条展示

用户：accept / reject / modify

        ↓ accept

python scripts/agent_learning_review.py promote --decision accept --patch <diff> --allow-write

        ↓

bash scripts/validate.sh --quick

        ↓ 通过

候选状态改为 accepted，生效
```

---

## 事实源说明

### Session 记录（`workspace/daily/**/*.md`）
finalize.py 每轮写入，格式见 01-framework.md。
关键字段：`skill_triggered`、`status`、`摘要`、`KB 命中`。

### KB 检索日志（`workspace/kb/search-log.jsonl`）
content-runtime 每次 `kb search` 时追加写入，每行一条 JSON（**唯一格式见 04-knowledge-base.md §检索日志**，本处仅示例）：
```json
{
  "ts": "2026-06-18T14:30:00+08:00",
  "query": "数学思维",
  "modality": "all",
  "topk": 10,
  "hits": 8,
  "hit_ids": ["abc123", "def456"],
  "vec_n": 30, "fts_n": 27, "graph_n": 12, "reranked": true
}
```
（`vec_n/fts_n/graph_n` 为三路召回数，供 kb-tuning 候选判定；无 `session_ref` 字段。）

### 成品包记录（`outputs/**/*.md`）
`content-runtime publish package` 产出的 `publish-checklist.md`，记录内容类型和素材组合。

---

## 候选判定标准

`agent_learning_review.py` 按以下标准生成候选：

| 判定条件 | 候选类型 | 置信度 |
|---|---|---|
| 同一搜索 query 模式出现 ≥3 次（近 7 天内）| rule 或 template | high |
| 某 skill 步骤被跳过或出错 ≥2 次 | skill 修复 | high |
| session 摘要中出现「用户纠正 AI」关键词 | rule 或 memory | high |
| 检索 hits < 2（topk=10，同主题出现 ≥2 次）| kb-tuning | medium |
| 成品包的某种格式连续 ≥3 次「成功」 | template | medium |
| status=failed 的 session ≥2 次且原因相似 | skill 修复 | medium |
| 新的内容类型在 outputs/ 出现但无对应模板 | template | medium |

**不生成候选**：
- 一次性偶发、无规律的情况
- 纯执行 bug（去 GitHub issue，不走候选流程）
- 已有对应 pending 候选未处理的情况（不重复生成）

---

## 候选文件格式

`workspace/agent-learning/candidates-YYYY-MM-DD.md`（AI 构建时必须严格遵守此格式）：

```markdown
# 学习候选 - YYYY-MM-DD

扫描范围：近 7 天（YYYY-MM-DD ~ YYYY-MM-DD）
Session 数：12  KB 搜索记录数：47  候选数：3

---

## 候选 #1

- **类型**: rule
- **置信度**: high
- **建议内容**:
  在 rules/core-routing.md「内容生成」分类中补充触发词：
  「读后感 / 书评 / 推荐语 / 总结」→ 路由到 content-generate skill
- **证据**:
  - session-3f9a2c1b.md：输入「写一篇《数学之美》读后感」，被路由到搜索而非内容生成
  - session-b7d4e0a6.md：输入「帮我写个书评」，同样误路由
- **晋升目标**: `rules/core-routing.md`，在「内容生成」分类触发词列表追加
- **状态**: pending

---

## 候选 #2

- **类型**: template
- **置信度**: medium
- **建议内容**:
  新增「知识卡片」内容模板文件 `skills/content-generate/templates/knowledge-card.md`
  格式：正方形配图（1:1）+ 核心知识点（3-5 条，emoji 开头）+ 书名来源标注
- **证据**:
  - outputs/2026-06-10/content/math-card/：用户标注「好，以后用这个格式」
  - outputs/2026-06-14/content/reading-card/：同格式再次成功
- **晋升目标**: `skills/content-generate/templates/knowledge-card.md`（新建文件）
- **状态**: pending

---

## 候选 #3

- **类型**: kb-tuning
- **置信度**: medium
- **建议内容**:
  图片 caption 质量偏低：视觉细节描述少，标签颗粒度粗。
  建议在 content_runtime.py 的 `caption_image_file()` 函数中，将 caption prompt 调整为：
  「描述图片的主题、视觉风格、构图要素和与教育/图书的关联，100字以内，最后列5个精确标签」
- **证据**:
  - KB 搜索「数学可视化」hits=1（topk=10），但 media-store 中实际有相关图片
  - KB 搜索「思维导图」hits=0（topk=10），同上
- **晋升目标**: `skills/content-generate/scripts/content_runtime.py`，`caption_image_file()` 函数 prompt 修改
- **状态**: pending
```

---

## 晋升目标分类规则

| 候选类型 | 晋升目标 | 判断依据 |
|---|---|---|
| `rule` | `rules/core-routing.md` 或 `rules/core-safety.md` | 需要修改 AI 的行为边界或分类逻辑 |
| `skill` | `skills/<name>/SKILL.md` | 需要修改执行步骤流程 |
| `memory` | `memory/profile.md` | 用户偏好或项目事实，不是行为约束 |
| `template` | `skills/content-generate/templates/` | 固化内容格式模板 |
| `kb-tuning` | `scripts/` 或 `content_runtime.py` | 检索参数或 ingest 逻辑 |

**分类判断口诀**：
- 「AI 每次必须 / 不得做 X」→ rule
- 「做 X 任务时按这个流程走」→ skill
- 「这个用户喜欢 Y 格式 / 这个项目背景是 Z」→ memory
- 「生成这类内容时用这个版式」→ template
- 「脚本参数或 prompt 要调整」→ kb-tuning

---

## 晋升执行流程

```
1. AI 读取 candidates-YYYY-MM-DD.md
2. 逐条展示候选（类型、置信度、建议内容、证据）
3. 询问用户：「候选 #N：accept / reject / modify？」
4. 按用户回应处理：

   accept  → AI 先生成明确 unified diff（不按自然语言建议直接自动改）
             → python scripts/agent_learning_review.py promote --decision accept --patch <diff> --allow-write
             → 命令内部执行 bash scripts/validate.sh --quick
             → 通过：候选状态改为 accepted
             → 失败：命令尝试回滚 patch，候选状态改为 failed，告知错误

   reject  → python scripts/agent_learning_review.py promote --decision reject --allow-write
             → 候选文件中把「状态: pending」改为「状态: rejected」
             → 不修改任何规则/skill/memory

   modify  → python scripts/agent_learning_review.py promote --decision modify --note "<修改意见>" --allow-write
             → 候选文件中把状态改为 modified 并记录处理备注
             → 后续确认后再执行 accept 流程

5. 所有候选处理完毕 → 汇总报告（accepted N / rejected M / pending K 条）
```

**硬约束**：
- 任何晋升都必须有用户明确 accept，不能自动晋升
- 一次只处理一条候选，不批量修改
- 晋升后必须跑 `validate.sh --quick`，失败则回滚
- 绝不自动修改 `rules/core-safety.md`
- 绝不自动删除规则或 skill 步骤

---

## `agent_learning_review.py` 实现规格

```python
"""
扫描事实源，生成学习候选文件。

用法：
  python scripts/agent_learning_review.py [--days N]    默认 7
  python scripts/agent_learning_review.py generate [--days N]
  python scripts/agent_learning_review.py --dry-run     打印候选不写文件
  python scripts/agent_learning_review.py promote \
    --file workspace/agent-learning/candidates-YYYY-MM-DD.md \
    --candidate N \
    --decision accept|reject|modify \
    [--patch /path/to/change.diff] \
    [--note "..."] \
    --allow-write

输出：
  workspace/agent-learning/candidates-YYYY-MM-DD.md

算法（按顺序执行）：

Step 1  收集事实
  - 读 workspace/daily/ 下最近 N 天的所有 session-*.md
  - 读 workspace/kb/search-log.jsonl 最近 N 天的记录
  - 读 outputs/ 最近 N 天的 publish-checklist.md

Step 2  模式检测（按「候选判定标准」7 项逐项检测）
  - 统计 search query 频次（query 归一化后 Counter，取 count≥3）→ rule/template
  - 统计 session status=failed 的 skill_triggered 分布（≥2 次且原因相似）→ skill 修复
  - 检测 session 摘要中「用户纠正 AI」关键词（纠正/不对/应该是/改成/下次）→ rule/memory
  - 检测 hits<2 的搜索记录（归类同一主题后频次 ≥2）→ kb-tuning
  - 检测 outputs/ 某成品类型连续 ≥3 次成功 → template
  - 检测 outputs/ 中无对应 template 的新内容格式 → template

Step 3  去重
  - 读 workspace/agent-learning/ 历史候选文件
  - 过滤掉已有 pending/accepted 候选覆盖的相同模式

Step 4  生成候选文件
  - 按置信度排序（high 在前）
  - 写入 workspace/agent-learning/candidates-YYYY-MM-DD.md

注意：
  - generate 只生成候选，不修改任何 rules/skill/memory
  - promote accept 只应用明确 patch，不从自然语言建议自动修改文件
  - 候选数量上限：单次 5 条（避免过多候选导致用户疲劳）
  - workspace/agent-learning/ 不存在时自动创建
"""
```

---

## 自我进化边界（明确不做的事）

| 不做 | 原因 |
|---|---|
| 自动晋升（跳过用户确认） | 规则影响 AI 全局行为，必须人工审核 |
| 自动修改 `core-safety.md` | 安全规则修改风险最高 |
| 自动删除规则或 skill 步骤 | 删除是不可逆操作 |
| 从候选自然语言直接改文件 | 必须先形成明确 patch，保证可审计、可回滚 |
| 跳过 `validate.sh --quick` 强制晋升 | 晋升后必须验证系统健康 |
| 批量处理多条候选 | 一次一条，减少误操作范围 |
| 存储原始对话文本 | 只存结构化摘要 |
