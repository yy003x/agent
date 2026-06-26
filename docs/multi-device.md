# 多电脑使用方案

本项目不要把 ignored 目录整体提交到 Git。推荐分成两条链路：

- Git：同步代码、规则、skill、设计文档、默认配置样例。
- state package：同步本地工作状态，例如知识库、素材、草稿和 session 摘要。

## 分层边界

### Git 负责

- `apps/`
- `skills/`
- `rules/`
- `design/`
- `memory/summary.md`
- `AGENTS.md`
- `.env.example`
- `requirements.txt`
- `docs/`
- `config/*.example.json`
- `scripts/`

### state package 负责

默认清单见 `config/state-sync.example.json`：

- `workspace/daily/`：session 摘要，便于换电脑后继续复盘。
- `workspace/resume/`：未完成任务恢复点。
- `workspace/media-inbox/`：待整理素材。
- `workspace/media-store/`：已整理素材。
- `workspace/kb/lance/`：当前本地知识库事实源。
- `outputs/`：草稿、成品包、研究或设计临时产物。
- `runs/workbench/config.json`：工作台 UI 的本地偏好配置，导入前需要人工确认是否适合目标电脑。

### 不同步

- `.env`、token、key、cookie、private key、完整 JWT。
- `.venv/`、`venv/`、`__pycache__/`、`*.pyc`。
- `runs/tmux/`、`runs/workbench/sessions/`、`runs/workbench/services/`、`runs/workbench/server*.log`、`runs/workbench/web*.log`。
- 临时 smoke 目录和纯运行日志。

## 常用流程

### 主电脑导出

先确保代码层已推到远端：

```bash
git status --short --branch
git push origin master
```

预览同步清单：

```bash
python3 scripts/state_sync.py plan
```

导出 state package：

```bash
python3 scripts/state_sync.py export
```

默认产物会写到 `runs/state-sync/<timestamp>-agent-state.tar.gz`。`runs/` 已被 Git 忽略，适合放本地临时包。

### 新电脑初始化

```bash
git clone git@github.com:yy003x/agent.git
cd agent
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

然后手动填 `.env` 中本机可用的 key 和模型配置。

### 新电脑导入

先只预览，不写文件：

```bash
python3 scripts/state_sync.py import --archive /path/to/agent-state.tar.gz --dry-run
```

确认后导入：

```bash
python3 scripts/state_sync.py import --archive /path/to/agent-state.tar.gz
```

默认不会覆盖已有文件。确实要覆盖时再显式加：

```bash
python3 scripts/state_sync.py import --archive /path/to/agent-state.tar.gz --overwrite
```

导入后检查：

```bash
python3 scripts/state_sync.py verify
bash scripts/validate.sh --quick
```

## 知识库路径

`content_runtime.py` 支持用环境变量覆盖知识库位置：

```bash
export CONTENT_RUNTIME_KB_DIR="$HOME/AgentState/kb"
export CONTENT_RUNTIME_LANCE_DIR="$CONTENT_RUNTIME_KB_DIR/lance"
export CONTENT_RUNTIME_MEDIA_STORE="$HOME/AgentState/media-store"
```

如果使用云盘目录承载这些路径，需要避免两台电脑同时写同一个 LanceDB 目录。更稳的方式仍是用 `state_sync.py export/import` 做明确快照。

## 冲突处理

`state_sync.py import` 的默认策略是保留目标电脑已有文件，跳过冲突。原因是 `workspace/` 和 `outputs/` 可能包含目标电脑新增内容，默认覆盖容易丢失本地工作。

需要迁移到一台空电脑时，通常不需要 `--overwrite`。需要把主电脑状态强制刷新到备机时，先在备机执行：

```bash
python3 scripts/state_sync.py import --archive /path/to/agent-state.tar.gz --dry-run --overwrite
```

确认预览后再去掉 `--dry-run`。
