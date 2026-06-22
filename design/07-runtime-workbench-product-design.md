# 运行工作台产品设计

本文是「个人 Agent 工作台」的产品层设计，重点回答一个问题：

```text
一名图书运营人员打开工作台后，应该看到什么、能做什么、哪些技术细节默认不应该打扰她。
```

`00-workbench-0-1-redesign.md` 是总体主设计；`06-graphical-agent-workbench.md` 是 Web UI + tmux runtime 技术设计。本文补齐运营用户视角的信息架构、任务流、页面分层和落地验收。

---

## 1. 产品定位

工作台不是 tmux / runtime / provider 的可视化调试器，而是图书运营日常任务的运行台：

- 用聊天发起任务：整理素材、同步知识库、生成文案、审核内容、整理成品包。
- 用进度面板看长任务状态：是否在跑、卡在哪、是否需要确认、产出在哪里。
- 用素材库管理依据：产品资料、文档、图片、视频、caption、KB 命中。
- 用产出中心检查结果：小红书、朋友圈、家长群、审核报告、活动计划、素材清单。
- 用设置选择底层助手：默认 `codex_cli`，可配置 `claude_cli`。
- 用诊断面板处理问题：tmux 日志、raw event、provider config、skill 元数据和健康检查明细。

核心原则：

1. **任务优先，不是日志优先**：默认展示任务进度、下一步动作和产出，不展示 raw JSON。
2. **运营语言优先，不是工程语言优先**：使用「进度 / 素材 / 产出 / 设置 / 诊断」，少用 pane、bytes、result file 这类术语。
3. **可控长任务**：Codex / Claude 运行十几分钟时，用户能知道还在跑、最近是否有输出、是否可以停止或打开诊断。
4. **证据链可见**：生成内容必须能回到素材、来源和成品包。
5. **调试信息不丢失**：开发者需要的日志、事件、路径、命令仍保留，但默认折叠到「诊断」。

---

## 2. 目标用户与任务

### 2.1 目标用户

主要用户是图书运营人员，日常围绕 K12 家长做内容、素材、私域和渠道运营。她关注：

- 有没有可用素材。
- 素材是否已同步知识库。
- 文案是否能直接改、复制、发给人工发布。
- 当前任务有没有完成，失败了该怎么处理。
- 不希望被 tmux、日志、状态文件、命令参数淹没。

次要用户是项目维护者 / Agent 工程维护者，关注：

- runtime 是否真的启动了 Codex / Claude。
- prompt 是否投递成功。
- result file 是否写入。
- tmux pane、日志、健康检查、skill registry 是否正常。

默认界面服务主要用户；次要用户入口放在诊断和高级设置。

### 2.2 高频任务

| 任务 | 运营用户关心 | 技术细节归属 |
|---|---|---|
| 新建会话并发起任务 | 会话标题、任务是否已开始、谁在执行 | session_id、turn_id、provider_run_id |
| 整理素材 | 有哪些素材、缺什么、是否可入库 | source_path、caption cache、ingest log |
| 同步知识库 | 同步了多少、失败哪些、能否搜索到 | LanceDB 表、index、embedding |
| 生成内容 | 草稿、平台、素材依据、是否合规 | prompt、runtime log、result.json |
| 查看产出 | 按平台预览、复制、打开文件夹 | 文件大小、绝对路径、raw JSON |
| 切换助手 | Codex / Claude 是否可用、当前用哪个 | command、sandbox、approval、extra args |
| 排障 | 失败原因、可执行动作 | tmux pane、output.log、raw events |

---

## 3. 当前 UI 审查结论

当前右侧 tab 是：

```text
事件 / 素材库 / 产出 / Provider / 系统
```

问题不是 tab 数量，而是默认展示层级偏工程调试。

| 当前 tab | 当前主要内容 | 运营关注度 | 应调整 |
|---|---|---:|---|
| 事件 | Runtime 状态、日志尾部、事件 raw JSON | 低到中 | 改成「进度」，只展示任务卡、步骤、下一步和友好失败；raw event/log 放诊断 |
| 素材库 | KB 搜索、modality、title、source_path、caption | 高 | 保留，但改成素材卡；`source_path` 放详情，不做第一视觉 |
| 产出 | 文件列表、路径、大小、预览 | 高 | 保留，但按业务类型和状态组织：小红书/朋友圈/家长群/审核报告/活动计划 |
| Provider | Provider 配置、命令、sandbox、approval、extra args、runtime test | 低 | 改成「设置」；只露助手选择和可用状态，命令参数进高级设置 |
| 系统 | Skill 元数据、健康检查明细 | 中到低 | 拆成「能力概览」和「诊断」；raw commands、skill_file、依赖细节进诊断 |

结论：

- 「素材库」和「产出」是运营主功能，需要加强业务表达。
- 「事件」「Provider」「系统」里大部分内容是开发者调试信息，不应作为默认主界面。
- 默认右侧应改为 `进度 / 素材 / 产出 / 设置 / 诊断`。

---

## 4. 信息架构

### 4.1 总体布局

```text
┌────────────────────────────────────────────────────────────────────┐
│ 左侧：会话与概览        中间：聊天任务台            右侧：任务辅助区 │
│                                                                    │
│ - 新会话                  - 当前会话标题             - 进度          │
│ - 会话列表                - 聊天记录                 - 素材          │
│ - 批量删除                - 用户输入                 - 产出          │
│ - 健康摘要                - 任务卡 / 确认入口         - 设置          │
│                                                     - 诊断          │
└────────────────────────────────────────────────────────────────────┘
```

布局职责：

- 左侧：找会话、管理会话、看整体是否可用。
- 中间：像 Codex App 一样聊天；所有用户输入都交给真实 `codex` / `claude` tmux 会话处理。
- 右侧：围绕当前会话给出进度、素材、产出和设置。

### 4.2 右侧 tab 目标态

| 新 tab | 目标 | 默认用户 |
|---|---|---|
| 进度 | 看当前任务跑到哪、是否需要我操作、产出在哪里 | 运营 |
| 素材 | 搜索和查看本地 KB 素材，加入本次任务 | 运营 |
| 产出 | 查看、预览、复制、打开本次或历史产出 | 运营 |
| 设置 | 选择助手、查看能力可用性、打开高级设置 | 运营 + 维护者 |
| 诊断 | 查看 raw event、runtime log、tmux run、skill/health 明细 | 维护者 |

「诊断」默认可以显示，但视觉上应弱化；未来可通过「高级模式」开关显示。

---

## 5. 核心页面设计

### 5.1 进度

替代当前「事件」。

运营用户默认看到：

- 当前任务标题：来自第一句用户输入。
- 执行状态：`排队中 / 运行中 / 等待确认 / 已完成 / 失败 / 已停止`。
- 当前步骤：例如 `理解需求`、`检索素材`、`生成草稿`、`整理成品包`、`同步知识库`。
- 执行助手：`Codex` 或 `Claude`，只作为辅助信息。
- 最近活动：用自然语言摘要，例如「Codex 最近 30 秒仍有输出」。
- 下一步动作：
  - 等待中：显示「正在处理」。
  - 等待确认：显示确认按钮或提示去聊天区回复。
  - 失败：显示「重试 / 打开诊断 / 停止会话」。
- 关联产出：直接跳到产出 tab 或打开预览。

不默认展示：

- `pane_id`
- `output_bytes`
- `bytes_per_sec`
- `turn_id`
- `result.json`
- `prompt.md`
- raw event JSON
- raw `output.log`

这些进入「诊断」。

建议数据模型：

```json
{
  "task_id": "chat-xxx:turn-yyy",
  "title": "整理一下素材，同步知识库",
  "intent": "knowledge_sync",
  "status": "running",
  "current_step": "同步知识库",
  "provider": "codex_cli",
  "started_at": "2026-06-22T21:30:00+08:00",
  "elapsed_seconds": 620,
  "activity": "Codex 最近 45 秒仍有输出",
  "action_required": null,
  "outputs": [
    {
      "label": "同步报告",
      "path": "outputs/2026-06-22/knowledge-sync/report.md"
    }
  ],
  "diagnostic": {
    "session_id": "chat-...",
    "turn_id": "turn-...",
    "provider_run_id": "run-..."
  }
}
```

### 5.2 素材

替代当前「素材库」的 raw 结果展示。

默认卡片字段：

- 素材标题。
- 素材类型：文档 / 图片 / 视频。
- 关联图书或主题。
- 摘要 / caption。
- 入库状态：未入库 / 已入库 / 待补 caption / 入库失败。
- 可用动作：
  - 查看详情。
  - 加入本次任务。
  - 查看来源。
  - 准备入库。
  - 同步知识库。

详情里再展示：

- `source_path`
- `modality`
- `score`
- `chunk_id`
- `caption` 全文
- 原始文件预览

第一版可以先保留搜索表单，但展示顺序调整为：

```text
标题 / 类型 / 摘要 / 操作
来源路径放详情
```

### 5.3 产出

保留并强化。

默认分组：

- 本次会话产出。
- 今天产出。
- 历史产出。

业务类型：

- 小红书图文。
- 朋友圈文案。
- 家长群话术。
- 短视频脚本。
- 活动计划。
- 图书档案。
- 素材清单。
- 知识库同步报告。
- 合规审核报告。

每个产出卡片展示：

- 标题。
- 类型。
- 状态：`草稿 / 待审核 / 可手动发布 / 需修改 / 已归档`。
- 最近更新时间。
- 主要动作：
  - 预览。
  - 复制正文。
  - 打开文件夹。
  - 标记需修改。

不默认展示：

- 文件大小。
- raw JSON。
- 绝对路径。

这些放在详情或诊断。

### 5.4 设置

替代当前「Provider」。

默认只展示：

- 当前助手：`Codex` / `Claude`。
- 当前执行模式：`tmux 真实 CLI 会话`。
- 项目目录：`/Users/yang/agents/agent`。
- 可用状态：
  - Codex 可用 / 不可用。
  - Claude 可用 / 未启用 / 不可用。
  - tmux 可用 / 不可用。
- 切换按钮：
  - 聊天助手。
  - 长任务助手。
- 保存配置。

高级设置折叠展示：

- Codex 命令。
- Codex sandbox。
- Codex approval。
- Codex extra args。
- Codex no-alt-screen。
- Codex bypass approvals/sandbox。
- Claude 命令。
- Claude permission mode。
- Claude extra args。
- Claude skip permissions。
- Runtime 测试任务。

高级设置需要明确标识：

```text
这些配置会影响底层 CLI 启动方式，通常不需要修改。
```

### 5.5 诊断

承接当前「事件」里过多的调试信息、当前「Provider」高级 runtime run，以及当前「系统」里的 raw skill/health。

诊断内容：

- 当前会话 runtime 状态：
  - pane id。
  - turn id。
  - output bytes。
  - result file 是否存在。
  - output rate。
- raw `output.log` tail。
- raw event JSON。
- runtime runs：
  - run_id。
  - state。
  - command。
  - logs。
  - stop。
- Provider effective config JSON。
- Skill Registry 原始字段：
  - skill_file。
  - commands。
  - trigger。
  - capabilities。
- 健康检查明细：
  - Python。
  - tmux。
  - codex。
  - claude。
  - ffmpeg。
  - KB 依赖。

诊断面板也要做敏感信息屏蔽，不能展示 token、secret、cookie、private key 或完整 JWT。

---

## 6. 关键交互流

### 6.1 新会话发起任务

```text
用户输入「整理一下素材，同步知识库」
  -> 若没有当前会话，自动创建会话
  -> 会话标题使用第一句用户输入
  -> 中间聊天区不重复显示发送后的 pending 用户消息
  -> 进度 tab 出现任务卡：排队中
  -> tmux 会话投递给 Codex / Claude
  -> 进度更新为运行中
  -> 有产出后关联到产出 tab
```

产品要求：

- 用户不需要理解 session_id。
- 会话标题不能是固定「图书运营会话」。
- 发送失败要给可读原因和重试入口。

### 6.2 十几分钟长任务

```text
任务运行中
  -> 进度显示「正在处理」
  -> 最近活动显示是否仍有输出
  -> 超过阈值但仍有输出：保持运行中
  -> 长时间无输出：提示可能等待输入或卡住
  -> 用户可打开诊断查看日志
  -> result file 写入后标记完成
```

运营用户默认看到：

```text
Codex 正在处理，最近 1 分钟仍有输出。
当前步骤：生成草稿。
```

维护者在诊断看到：

```text
pane %12 · output 18420 bytes · 26 B/s · result.json 未生成
```

### 6.3 投递失败

典型错误：

```text
tmux run chat-... did not become idle before prompt submission
```

运营提示：

```text
助手还没有准备好接收这次输入。可以重试，或打开诊断查看 CLI 是否卡在启动页。
```

可操作项：

- 重试投递。
- 停止当前会话。
- 打开诊断。

诊断里保留原始错误。

### 6.4 查看产出并手动发布

```text
任务完成
  -> 进度卡显示已完成
  -> 关联产出卡
  -> 用户进入产出 tab
  -> 预览标题、正文、标签、配图建议、合规 checklist
  -> 复制正文或打开文件夹
```

必须提示：

```text
仅生成手动发布素材，不会自动发帖或群发。
```

---

## 7. 运营视角字段规范

### 7.1 状态文案

| 技术状态 | 运营显示 |
|---|---|
| `queued` | 排队中 |
| `starting` | 正在启动助手 |
| `idle` | 助手已就绪 |
| `running` | 正在处理 |
| `waiting_result` | 正在整理结果 |
| `done` | 已完成 |
| `failed` | 失败 |
| `stopped` | 已停止 |

### 7.2 Runtime 名称

| 内部值 | UI 显示 |
|---|---|
| `codex_cli` | Codex |
| `claude_cli` | Claude |
| `fake` | 不在普通 UI 显示，只在诊断或测试中显示 |

### 7.3 业务步骤

第一版统一这些步骤名：

- 理解需求。
- 检索素材。
- 准备素材。
- 同步知识库。
- 生成草稿。
- 审核合规。
- 整理产出。
- 记录收尾。
- 等待用户确认。

避免在默认 UI 使用：

- `runtime.queued`
- `workflow.step`
- `FileResultObserver`
- `TmuxProvider`
- `result.json`
- `output.log`

---

## 8. 后端适配策略

第一阶段不需要重写 runtime，只加一层面向 UI 的 operator view adapter。

现有事实源：

```text
runs/workbench/sessions/<session_id>/
  state.json
  messages.jsonl
  events.jsonl
  pending_turns.json
  current_turn.json
  turns/<turn_id>/
  runtime/provider/<provider_run_id>/

outputs/
workspace/kb/
skills/*/SKILL.md
```

建议新增后端聚合：

```text
GET /api/chat/sessions/{id}/operator
```

返回：

- `progress`：当前任务卡。
- `materials`：本次会话关联素材。
- `outputs`：本次会话关联产出。
- `settings_summary`：助手和健康摘要。
- `diagnostics_ref`：诊断需要的 id 和路径引用。

短期也可以继续复用 `/api/chat/sessions/{id}`，但前端需要把 raw 字段转换成运营字段。

---

## 9. 实施路线

### P0：视觉与信息分层，不改 runtime

目标：先把默认 UI 从调试器改成运营工作台。

任务：

1. tab 改名：`事件 -> 进度`，`素材库 -> 素材`，`Provider -> 设置`，`系统 -> 诊断`。
2. 进度 tab 默认只显示任务卡和友好状态。
3. raw event JSON、runtime log、pane/bytes/result file 移到诊断。
4. 设置 tab 默认只显示 Codex/Claude 选择和可用状态；命令参数进高级折叠。
5. 素材卡隐藏 `source_path` 到详情。
6. 产出卡隐藏 raw path/size 到详情。

验收：

- 默认右侧无 raw JSON。
- 默认右侧无 pane id / bytes / result.json。
- 运营用户能在 3 秒内判断当前任务状态。

### P1：Operator progress adapter

目标：让长任务可理解、可控。

任务：

1. 从 session state、pending turns、runtime status、events 生成 `progress`。
2. 统一状态中文映射。
3. 最近活动摘要：仍有输出 / 长时间无输出 / 等待结果。
4. 失败原因分层：运营友好原因 + 原始错误放诊断。
5. 输出关联：完成后在进度卡显示可点击产出。

验收：

- Codex/Claude 运行 10 分钟时，进度仍能说明「正在处理」或「可能卡住」。
- 投递失败时给出重试、停止、诊断入口。

### P2：素材与产出业务化

目标：把素材库和产出中心做成运营日常可用面板。

任务：

1. 素材结果改为业务卡片。
2. 增加「加入本次任务」和「查看来源」入口。
3. 产出按业务类型、日期、状态分组。
4. 增加复制正文、打开文件夹、标记需修改。
5. 合规审核结果以 `pass / needs-edit / blocked` 方式展示。

验收：

- 用户可以从素材检索到选择素材，再回到聊天继续任务。
- 用户可以从产出中心直接复制一条小红书/朋友圈/群话术草稿。

### P3：高级模式与诊断治理

目标：保留维护能力，但不干扰运营主路径。

任务：

1. 增加高级模式开关。
2. 诊断默认折叠 raw logs 和 raw JSON。
3. Provider effective config、skill commands、health detail 仅高级模式展示。
4. 增加一键导出诊断包，但过滤敏感信息。

验收：

- 普通模式看不到高风险参数。
- 高级模式足够排查 tmux 投递、provider 启动、skill registry 和 KB 依赖问题。

---

## 10. 非目标

第一版不做：

- 多用户权限。
- 公网访问。
- 自动发布小红书、朋友圈或微信群。
- LLM API 作为默认依赖。
- token 成本统计。
- 完整项目管理系统。
- 把 Codex / Claude 原生聊天历史直接导入会话列表。

---

## 11. 完成定义

满足以下条件，认为「运行工作台」产品改造完成：

- [ ] 默认右侧 tab 为 `进度 / 素材 / 产出 / 设置 / 诊断`。
- [ ] 普通模式默认不展示 raw JSON、tmux pane、bytes、result file、prompt path。
- [ ] 每个会话都有运营可读标题，默认来自第一句用户输入。
- [ ] 每个运行中的 turn 都有一个进度卡。
- [ ] 长任务可以看到最近活动和当前状态。
- [ ] 失败状态有友好原因、原始错误和下一步动作。
- [ ] 素材结果以业务卡片展示，来源路径在详情。
- [ ] 产出按业务类型和状态展示，支持预览和复制。
- [ ] 设置只暴露 Codex/Claude 选择和可用状态，高级参数折叠。
- [ ] 诊断保留所有排障信息，但过滤敏感值。
