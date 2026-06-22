# 当前设计实现步骤

本文是当前仓库从 design 到实现的执行顺序。执行时按 P0 → P5 推进；单个 phase 失败时先修复重试，连续 2 次仍失败则记录为跳过项，继续下一个 phase。

---

## P0 入口、规则与收尾基线

目标：确保 Agent 启动后能读到业务协作约定、路由规则、安全边界、当前状态和收尾机制。

执行步骤：
1. 对齐 `AGENTS.md` 与 `design/templates/AGENTS.md`：入口以“学而思图书运营”协作约定为主，并保留本地 Agent 运行约定。
2. 检查 `rules/core-routing.md`、`rules/core-safety.md` 与模板一致。
3. 检查 `skills/finalize/SKILL.md` 与 `scripts/finalize.py`：支持 `record` / `hook` / `mark` / `snapshot`。
4. 检查 `.claude/settings.json` Stop hook 指向 `python scripts/finalize.py hook`。
5. 跑 `python scripts/finalize.py --help` 与 activity hook 行为测试。

完成标准：
- `AGENTS.md` 能说明业务口吻、运行事实源、关键目录与 finalize 入口。
- Stop hook 无实质信号时跳过；存在 activity marker 时写 session。

---

## P1 知识库基线

目标：把本地文档/图片/视频素材统一进入 LanceDB 知识库，并支持 hybrid search。

执行步骤：
1. 运行 `python skills/content-generate/scripts/content_runtime.py init` 初始化 `items` / `concepts` / `graph_edges`。
2. 准备 `test-data/` 文档样例，优先用 doc-only 路径验证无 API key / ffmpeg 的最小闭环。
3. 运行 `kb ingest --src <test-folder> --modality doc --limit 5 --allow-write`。
4. 运行 `kb search --query "数学思维" --modality all --topk 5 --json`，人工回读 `source_path`。
5. 运行 `kb index --rebuild fts|graph|all --allow-write` 验证可重建。
6. 运行 `kb legacy` 检查旧 `catalog.db` / `vector/`；仅空残留允许 `--allow-write` 自动清理，非空残留保留。

完成标准：
- `workspace/kb/lance/` 是唯一 KB 数据事实源。
- `search-log.jsonl` 记录检索事实，`graph.jsonl` 可确定性重建。

---

## P2 文案与 plan 生成

目标：把“检索素材 → 文案草稿 → 组装计划”从纯 AI inline 步骤变成可重复 CLI。

执行步骤：
1. 用 `kb search --json` 或人工筛选结果生成 `outputs/YYYY-MM-DD/content/<slug>/sources.json`。
2. 运行：
   ```bash
   python skills/content-generate/scripts/content_runtime.py text draft \
     --brief "<用户需求摘要>" \
     --platform xiaohongshu \
     --style "<风格>" \
     --sources outputs/YYYY-MM-DD/content/<slug>/sources.json \
     --out outputs/YYYY-MM-DD/content/<slug>/draft.json \
     --allow-write
   ```
3. 必要时由 AI 基于 `draft.json` 做 inline 润色，但必须回读素材事实，不编造。
4. 运行：
   ```bash
   python skills/content-generate/scripts/content_runtime.py plan build \
     --draft outputs/YYYY-MM-DD/content/<slug>/draft.json \
     --out outputs/YYYY-MM-DD/content/<slug>/plan.json \
     --allow-write
   ```

完成标准：
- `draft.json` 至少包含 `title` / `body_text` / `tags` / `sources`。
- `plan.json` 至少包含 `type` / `title` / `body_text`，并尽可能引用本地存在的图片/视频素材。

---

## P3 媒体组装与发布包

目标：将 plan 中的图片/视频素材组装为平台成品包，不自动发布。

执行步骤：
1. 图片链路：运行 `media assemble --spec plan.json --out outputs/... --allow-write`。
2. 短视频链路：确认本机有 `ffmpeg` 后运行同一 assemble 命令，生成 `clips/clip_*.mp4`。
3. 运行 `publish package --platform xiaohongshu|moments|wechat_group --in outputs/... --allow-write`。
4. 回读 `publish-checklist.md`，检查标题、正文、标签、素材顺序、素材来源相对路径。
5. 展示预览，由用户手动发布；Agent 不调用外部发布 API。

完成标准：
- 成品包包含平台目录与 `publish-checklist.md`。
- 视频素材进入 `clips/` 并写入素材顺序。

---

## P4 自学习与调度

目标：把 session / search-log / outputs 提炼成候选，并提供可审计晋升命令。

执行步骤：
1. 运行 `python scripts/agent_learning_review.py --days 7 --dry-run` 查看候选。
2. 运行 `python scripts/agent_learning_review.py --days 7` 写入 `workspace/agent-learning/candidates-YYYY-MM-DD.md`。
3. 对 reject / modify 候选，用：
   ```bash
   python scripts/agent_learning_review.py promote \
     --file workspace/agent-learning/candidates-YYYY-MM-DD.md \
     --candidate <N> \
     --decision reject|modify \
     --note "<原因>" \
     --allow-write
   ```
4. 对 accept 候选，先准备明确的 unified diff，再用：
   ```bash
   python scripts/agent_learning_review.py promote \
     --file workspace/agent-learning/candidates-YYYY-MM-DD.md \
     --candidate <N> \
     --decision accept \
     --patch /path/to/change.diff \
     --allow-write
   ```
5. accept 命令必须执行 `bash scripts/validate.sh --quick`；失败要回滚 patch 并把候选标记为 failed。
6. 调度入口为 `python apps/scheduler/scheduler.py`，任务定义在 `apps/scheduler/jobs.json`。

完成标准：
- 候选生成、状态更新、patch 晋升、quick 校验入口均可执行。
- 不从候选自然语言自动修改规则/skill。

---

## P5 验证、收尾与发布

目标：把本轮改动变成可提交、可推送、可追溯的状态。

执行步骤：
1. 运行 `python3 -m py_compile scripts/*.py apps/scheduler/scheduler.py skills/content-generate/scripts/content_runtime.py`。
2. 运行 `bash scripts/validate.sh --quick`。
3. 环境具备时运行 `bash scripts/validate.sh --e2e`；缺少外部依赖或 API key 时记录失败项，不伪装通过。
4. 运行核心行为 smoke：text draft、plan build、promote reject、kb legacy dry-run。
5. 运行 `git diff --check`、敏感信息扫描、模板一致性 diff。
6. 显式 `python scripts/finalize.py record --summary "<本轮摘要>"`。
7. 精确 `git add <本轮文件>`，提交 Conventional Commit。
8. 用户明确授权时执行 `git push origin <branch>`。

完成标准：
- quick 验证通过。
- 本轮改动已提交并推送远端。
