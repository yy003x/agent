# finalize Skill（收尾类）

## 类别与触发

**收尾类 skill**：不由路由器按用户输入触发，而由**每轮结束规则 / 运行时 Stop hook 转交**。
- 显式：处理类 skill 或写文件任务结束时，AI 调
  `python scripts/finalize.py record --skill <name> --status <...> --summary "<...>"`
- 兜底：Stop hook 配置 `python scripts/finalize.py hook`，仅在检测到实质性任务信号时补写，避免漏记或空记。

> 与处理类 skill（content-generate）的区别：处理类由 `rules/core-routing.md` 按输入语义触发；
> 收尾类在**一轮处理结束后**触发，把「发生了什么」沉淀为 session（自我进化事实源，见 02）。

---

## 前置检查

- [ ] `workspace/daily/` 可写（不存在自动创建）

---

## 触发条件（实质性任务才收尾）

满足任一即收尾：
- 执行了处理类 skill（content-generate）
- 写了文件（代码 / 配置 / 设计文档 / outputs 成品包）
- 修改了 rules / skills / memory

**不收尾**：闲聊、纯问答、只读查询（kb search 后直接回答）、任务被用户取消。

---

## 执行流程

按序执行，**不得跳步**：

### 步骤 1：判定是否实质性任务
按上「触发条件」判断；不满足则跳过收尾，**不写空 session**。

### 步骤 2：组织摘要（不写原始对话文本）
1-3 句：本轮做了什么、产出了什么、关键 KB 命中 id（如有）。

### 步骤 3：写 session 记录
```bash
python scripts/finalize.py record \
  --skill <处理 skill 名，无则 none> \
  --status <success|partial|failed，省略则据 git 启发式自动判定> \
  --summary "<1-3 句摘要>"
```

### 步骤 4（可选）：未完成任务留恢复点
任务半途 / 有待续项：加 `--handoff`，额外写 `workspace/resume/`。

---

## 输出格式

`workspace/daily/YYYY-MM-DD/session-<8位>.md`，frontmatter（`session_id` / `timestamp` / `skill_triggered` / `status`）+ `## 摘要` / `## 文件变更` / `## KB 命中`。**完整格式以 01-framework.md §5 为准。**

---

## 安全边界

- 不写原始对话文本，只写结构化摘要。
- 不在闲聊 / 纯问答 / 只读查询 / 任务取消后触发（避免空记录污染自学习事实源）。
- session 是 `agent_learning_review.py` 的事实源（02），写入须保持可解析的格式。

---

## 与脚本的关系

本 skill 是**收尾规程**；`scripts/finalize.py` 是其工具（`record` / `hook` / `snapshot` 三个子命令）。
脚本实现规格见 01-framework.md §5；本 skill 不重复实现逻辑，只规定「何时收尾、写什么、怎么调」。
