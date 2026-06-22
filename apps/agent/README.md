# 纯 Python 本地 Agent 运行时（工作台 runtime 过渡层）

让 Python 服务负责「路由 → 命中 skill → 按步骤执行 → 收尾」的确定性编排。
当前旧 CLI 过渡层仍有 legacy `codex exec` 调用；工作台目标态会抽成 `llm_adapter`，
由 GUI / Python 服务通过 tmux 启动真实 `codex` / `claude` 交互会话，默认 `codex_cli`，
`claude_cli` 通过配置启用。未来可选 LLM API backend，但不是当前硬依赖。

底层确定性工具（KB 检索 / 文案草稿 / 组装 / 打包 / 收尾 / 自学习 / 调度）完全复用，未重写。

## 组成

| 文件 | 职责 |
|---|---|
| `orchestrator.py` | 主循环（REPL + 单轮）：分类 → 分发 → `content-generate` 10 步状态机（确认门 + `--allow-write` 门禁）→ `workbench-finalizer` 收尾（工具为 `scripts/finalize.py`）。**同进程导入** `content_runtime`，向量/精排模型常驻 |
| `brain.py` | 当前认知层：输入分类 / 需求抽取 / 文案润色 / 对话问答；仍有 legacy `codex exec` 调用，无 CLI 时自动降级（关键词分类 + 模板文案）。目标态迁移到 tmux 真会话 `llm_adapter` |
| `skills/content-generate/scripts/content_runtime.py` | 知识库 / 草稿 / 媒体组装；当前图片/视频 caption 仍有 legacy `codex exec --image` 生成，并写入本地 caption cache。目标态改为 tmux CLI runtime 或人工 caption 降级 |

对应设计：`design/01-framework.md`（路由→skill 桥梁、finalize）、`rules/core-routing.md`（分类表与默认行为）、`skills/content-generate/SKILL.md`（执行步骤）。

## 运行

```bash
pip install -r requirements.txt        # lancedb / sentence-transformers / jieba / pillow ...
command -v codex                       # 需可执行
codex login                            # 首次使用需完成本机登录
# 工作台目标态可选：command -v claude && claude 登录
# 未来可选：启用 LLM API backend 时，在本地环境配置对应 provider key

python apps/agent/orchestrator.py                      # 交互式 REPL
python apps/agent/orchestrator.py "出一篇数学思维书单"   # 单轮执行后退出
```

首次内容生成前需初始化并 ingest 知识库（REPL 内检测到 `workspace/kb/lance/` 缺失会提示 init）：

```bash
python skills/content-generate/scripts/content_runtime.py kb ingest \
  --src <素材目录> --allow-write
```

## 当前 Codex CLI 配置（环境变量）

| 变量 | 默认 | 用途 |
|---|---|---|
| `AGENT_CODEX_CMD` | `codex` | Codex CLI 可执行文件 |
| `AGENT_CODEX_MODEL` | 空 | legacy 可选，透传给旧 `codex exec --model` |
| `AGENT_CODEX_PROFILE` | 空 | legacy 可选，透传给旧 `codex exec --profile` |
| `AGENT_CODEX_TIMEOUT_S` | `180` | 单次 Codex CLI 窄任务超时时间 |

工作台目标态会新增 provider-neutral 配置，例如 `AGENT_LLM_BACKEND=tmux_cli|offline_template`、`AGENT_CLI_RUNTIME=codex_cli|claude_cli`。工作台托管路径不使用 `codex exec` 或 `claude -p`。

## 降级行为（无智能 runtime / 缺依赖）

- 无可用 tmux CLI runtime 或 legacy CLI 调用失败：分类走关键词规则，文案用模板初稿不做 AI 润色，对话/问答返回离线提示。
- 缺 `lancedb` 等 KB 依赖：KB 检索/init 给出安装提示而非报错退出。
- 纯文档检索闭环（P1）可在无智能 runtime 下跑通；文案/组装链路在缺图片素材时产出文本成品包（`publish-checklist.md`）。

## 边界（与 `rules/core-safety.md` 一致）

- 写操作（draft/plan/assemble/package）走 `--allow-write`，关键节点停下等用户确认。
- **不自动发布**：步骤 9 只产成品包与预览，发布由人工手动完成。
- 实质性任务（内容生成 / 写文件）才触发 `workbench-finalizer`；闲聊 / 纯问答 / 只读检索不收尾。
