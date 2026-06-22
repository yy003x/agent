# 纯 Python 本地 Agent 运行时

让本项目**脱离 Claude Code 等外部 Agent CLI 独立运行**。原本由外部宿主解释
`AGENTS.md` / `rules` / `SKILL.md` 完成的「路由 → 命中 skill → 按步骤执行 → 收尾」，
在这里收敛成一段 Python 主循环 + 几个窄任务的 Claude API 调用。

底层确定性工具（KB 检索 / 文案草稿 / 组装 / 打包 / 收尾 / 自学习 / 调度）完全复用，未重写。

## 组成

| 文件 | 职责 |
|---|---|
| `orchestrator.py` | 主循环（REPL + 单轮）：分类 → 分发 → `content-generate` 10 步状态机（确认门 + `--allow-write` 门禁）→ `finalize` 收尾。**同进程导入** `content_runtime`，向量/精排模型常驻 |
| `brain.py` | 认知层：输入分类 / 需求抽取 / 文案润色 / 对话问答；无 `ANTHROPIC_API_KEY` 自动降级（关键词分类 + 模板文案） |

对应设计：`design/01-framework.md`（路由→skill 桥梁、finalize）、`rules/core-routing.md`（分类表与默认行为）、`skills/content-generate/SKILL.md`（执行步骤）。

## 运行

```bash
pip install -r requirements.txt        # lancedb / sentence-transformers / jieba / anthropic ...
export ANTHROPIC_API_KEY=...           # 文案 AI 润色 / 对话需要；纯文档检索可不设

python apps/agent/orchestrator.py                      # 交互式 REPL
python apps/agent/orchestrator.py "出一篇数学思维书单"   # 单轮执行后退出
```

首次内容生成前需初始化并 ingest 知识库（REPL 内检测到 `workspace/kb/lance/` 缺失会提示 init）：

```bash
python skills/content-generate/scripts/content_runtime.py kb ingest \
  --src <素材目录> --allow-write
```

## 模型配置（环境变量）

| 变量 | 默认 | 用途 |
|---|---|---|
| `AGENT_ROUTER_MODEL` | `claude-haiku-4-5-20251001` | 输入分类 / 需求抽取（高频，用便宜快的） |
| `AGENT_WRITER_MODEL` | `claude-sonnet-4-6` | 文案润色 / 对话问答 |

## 降级行为（无 API key / 缺依赖）

- 无 `ANTHROPIC_API_KEY`：分类走关键词规则，文案用模板初稿不做 AI 润色，对话/问答返回离线提示。
- 缺 `lancedb` 等 KB 依赖：KB 检索/init 给出安装提示而非报错退出。
- 纯文档检索闭环（P1）可在无 key 下跑通；文案/组装链路在缺图片素材时产出文本成品包（`publish-checklist.md`）。

## 边界（与 `rules/core-safety.md` 一致）

- 写操作（draft/plan/assemble/package）走 `--allow-write`，关键节点停下等用户确认。
- **不自动发布**：步骤 9 只产成品包与预览，发布由人工手动完成。
- 实质性任务（内容生成 / 写文件）才触发 `finalize`；闲聊 / 纯问答 / 只读检索不收尾。
