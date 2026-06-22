# 输入路由规则

每轮输入先做语义分类，再选择一个主 owner。分类靠语义判断，不做关键词硬匹配；有歧义时优先选择事实更稳、风险更低的 owner。

---

## 当前背景

当前 Agent 是个人图书运营工作台：用于整理产品资料、文档、图片、视频，纳入本地知识库，并辅助生成小红书、朋友圈、家长群等运营内容。入口同时支持 GUI 工作台和 CLI；GUI 底层通过 tmux 启动真实 `codex` / `claude` CLI 会话。

---

## 分类表

| 分类 | 意图特征 / 关键词 | 主 owner |
|---|---|---|
| 轻量对话 / 问答 | 闲聊、解释、路径说明、状态查询、GUI 当前行为、runtime/provider 是什么 | `workbench-chat` |
| 本地事实检索 | 查本地知识库、素材、历史设计、outputs、daily、workspace、当前项目实现 | `knowledge-search` |
| 外部调研 | 查/搜/找、调研、最新、竞品、业界做法、平台规则、工具现状、教程 | `workbench-research` |
| 方案 / 设计 | 架构、方案、PRD、技术设计、工作台能力设计、流程规划、阶段计划 | `workbench-design` |
| 素材入库准备 | 整理素材、产品资料、图片/视频批次、素材去重、命名建议、准备入库 | `book-asset` |
| 知识库同步 | 同步知识库、ingest、index、素材正式入库、同步报告、抽检命中 | `knowledge-sync` |
| 图书运营档案 | 建图书档案、梳理卖点、适用年级、家长关注点、内容角度、禁用表述 | `book-profile` |
| 活动 / 选题计划 | 一周运营计划、活动节奏、多渠道联动、选题日历、渠道矩阵 | `book-campaign` |
| 内容生成 | 出一篇、生成内容、图文、小红书、朋友圈、家长群话术、书单、读后感、书评、推荐语、配图、短视频、视频脚本 | `content-generate` |
| 内容成品包 | 打包成品、发布包、checklist、版本整理、可复制话术包 | `content-package` |
| 内容审核 | 合规检查、平台适配、事实审核、是否硬广、是否焦虑、发布前检查 | `content-compliance-review` |
| 媒体素材准备 | 检查图片/视频、尺寸格式、caption 准备、配图适配、视频素材整理 | `book-media` |
| 执行 / 修改 | 实现、修改、修 bug、写脚本、生成文件、启动服务、验证、提交 | `workbench-execute` |
| 会话 / Runtime 运维 | UI 会话、tmux worker、provider 配置、投递失败、物理删除、服务启动排障 | `workbench-session-ops` |
| 任务收尾 | 收尾、记录一下、总结本次任务、handoff，或本轮已有实质文件变更 | `workbench-finalizer` |
| 自我优化 | 优化你自己、复盘、学习/借鉴其它项目、迁移能力、提炼候选 | `agent-learn` |
| Skill 管理 | 新建/迁移/改名/合并/维护本地 skill | `agent-skill-create` |

---

## 路由优先级

1. **素材链路先准备再同步**：用户说“整理素材 / 准备入库”走 `book-asset`；明确“同步知识库 / ingest / index”走 `knowledge-sync`。
2. **内容生成优先于搜索**：含“生成/出/做/写 + 成品形态”的图书运营任务直接走 `content-generate`。需要图书档案或素材依据时，先读 `book-profile` 和 KB。
3. **成品包和审核分开**：生成草稿走 `content-generate`；整理交付包走 `content-package`；发布前风险检查走 `content-compliance-review`。
4. **本地事实和外部事实分开**：查当前项目、workspace、KB、设计、outputs 走 `knowledge-search`；查平台/竞品/工具最新状态走 `workbench-research`。
5. **确认语义不升级执行**：用户说“确认下 / 评估 / 建议 / 看是否合理 / 只读核查”只做只读分析。只有“执行 / 修改 / 落地 / 按方案改 / 提交”等明确授权才走 `workbench-execute`。
6. **每轮一个主 owner**：复合任务分步处理，例如“先调研再生成”先 `workbench-research`，再 `content-generate`。
7. **实质任务结束再收尾**：写文件、执行命令、生成长期产物或用户要求 handoff 时，最后进入 `workbench-finalizer`；纯问答不收尾。

---

## 默认行为

### workbench-chat

轻量回答，不写文件，不触发收尾。涉及项目真实事实时，转 `knowledge-search` 后回读源文件。

### knowledge-search

知识库检索使用只读参数：

```bash
python3 skills/content-generate/scripts/content_runtime.py kb search \
  --query "<关键词>" \
  --modality all \
  --topk 10 \
  --json \
  --no-log \
  --no-touch
```

检索结果只是候选；回答前必须回读 `source_path` 或真实文件。

### workbench-research

需要外部可变事实时必须给来源。用户要求保存时写入 `outputs/YYYY-MM-DD/research/<topic>.md`。

### workbench-design

先读现状和约束，再给方案对比、推荐决策、阶段计划和验证方式。正式长期设计优先写 `design/`；一次性讨论稿可写 `outputs/YYYY-MM-DD/design/`。

### book-asset

整理图书产品资料、文档、图片、视频入库前批次，输出 manifest、去重/命名建议和需人工处理项；不正式写入 KB。

### knowledge-sync

正式执行 KB ingest / index / 抽检和同步报告；写操作必须带 `--allow-write`，向量重建和清理类动作需单独确认。

### book-profile

为单本图书建立可复用运营档案，记录卖点、适用年级、家长关注点、素材来源、内容角度和禁用表述。

### book-campaign

制定图书运营活动节奏、渠道矩阵、选题日历和素材缺口；确认后再拆给 `content-generate` 生成单条内容。

### content-generate

按 `skills/content-generate/SKILL.md` 执行。对外发布只产预览和成品包，不自动发帖、不群发。

### content-package

把草稿和媒体整理成小红书、朋友圈或家长群可手动发布的成品包和 checklist；不自动发布。

### content-compliance-review

发布前检查合规、平台适配和事实依据；结论必须是 `pass`、`needs-edit` 或 `blocked`。

### book-media

检查图片/视频可读性、格式、caption 准备和平台适配；不覆盖原始媒体。

### workbench-execute

先 `git status --short`，只改本轮必要文件，运行最小必要验证。Git 暂存/提交必须展示拟提交文件和 message，等用户确认。

### workbench-session-ops

处理 GUI 会话、tmux runtime、provider 配置、投递失败、物理删除和服务启动排障；删除和 kill pane 必须确认。

### workbench-finalizer

使用 `scripts/finalize.py` 写 session 摘要，不保存原始对话：

```bash
python3 scripts/finalize.py record \
  --skill <skill-name-or-none> \
  --status <success|partial|failed> \
  --summary "<摘要>"
```

### agent-learn

默认只生成候选，不直接改长期资产。候选落 `workspace/agent-learning/candidates-YYYY-MM-DD.md`，用户确认后才晋升。

### agent-skill-create

本项目 skill 发现以 `skills/<name>/SKILL.md` 为准；GUI Skill Registry 会扫描该目录。不要求旧式 `skills/index.json` 或 `agents/openai.yaml`。
