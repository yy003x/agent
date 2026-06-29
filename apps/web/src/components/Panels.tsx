import { Activity, Settings } from "lucide-react";
import type { FormEvent } from "react";
import type { FilePreviewPayload, HealthPayload, OperatorView, RuntimeChoice, RuntimeConfig } from "../types";
import { providerLabel, statusClass } from "../utils";

export function ProgressPanel(props: {
  operator?: OperatorView;
  onStop: () => void;
  onDiagnostics: () => void;
  onPreview: (path: string) => Promise<void>;
}) {
  const progress = props.operator?.progress;
  if (!progress) return <div className="empty">选择或创建一个会话后显示任务进度。</div>;
  return (
    <article className="progress-card">
      <div className="progress-head">
        <div>
          <h3>{progress.title || "当前任务"}</h3>
          <span>{progress.current_step || "等待输入"} · {progress.provider_label || providerLabel(progress.provider)}</span>
        </div>
        <span className={`pill ${statusClass(progress.status)}`}>{progress.status_label || progress.status}</span>
      </div>
      <p>{progress.activity}</p>
      {progress.elapsed_seconds != null && <span>已运行约 {Math.floor(progress.elapsed_seconds / 60)} 分 {progress.elapsed_seconds % 60} 秒</span>}
      {progress.friendly_error && <div className="friendly-error">{progress.friendly_error}</div>}
      <div className="action-row">
        <button onClick={props.onDiagnostics}><Activity size={14} /> 打开诊断</button>
        {["running", "queued"].includes(progress.status || "") && <button className="danger-button" onClick={props.onStop}>停止任务</button>}
      </div>
      {(progress.outputs ?? []).length > 0 && (
        <div className="card-list compact">
          {(progress.outputs ?? []).map((output) => (
            <button className="output-link" key={output.path} onClick={() => props.onPreview(output.path)}>
              {output.label || output.path}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

export function PreviewPanel({ preview }: { preview: FilePreviewPayload }) {
  if (preview.kind === "image" && preview.data_url) {
    return <div className="preview"><img src={preview.data_url} alt={preview.path || "preview"} /></div>;
  }
  if (preview.kind === "text") {
    return <pre className="preview">{preview.text}</pre>;
  }
  return <pre className="preview">{JSON.stringify(preview.entries ?? preview.message ?? preview, null, 2)}</pre>;
}

export function SettingsPanel({ config, health, choices, onSubmit }: {
  config: RuntimeConfig;
  health: HealthPayload;
  choices?: RuntimeChoice[];
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const checks = Object.fromEntries((health.checks ?? []).map((item) => [item.id, item]));
  const runtimeChoices = choices?.length ? choices : [
    { id: "tmux", label: "Tmux", transport: "tmux", profile: "tmux-codex" },
    { id: "code_cli", label: "Code CLI", transport: "code_cli", profile: "configurable" },
    { id: "llm_api", label: "LLM API", transport: "llm_api", profile: "llm-api" }
  ];
  return (
    <form className="settings-form" onSubmit={onSubmit}>
      <div className="settings-grid">
        <div className="card"><h3>聊天助手</h3><span>{providerLabel(config.chat_provider)}</span></div>
        <div className="card"><h3>长任务助手</h3><span>{providerLabel(config.runtime_provider)}</span></div>
        <div className="card"><h3>执行模式</h3><span>AgentRun provider</span></div>
      </div>
      <div className="status-row">
        <span className={`pill ${statusClass(checks.codex?.status)}`}>Codex {checks.codex?.status || "unknown"}</span>
        <span className={`pill ${statusClass(checks.claude?.status)}`}>Claude {checks.claude?.status || "unknown"}</span>
        <span className={`pill ${statusClass(checks["shared-runtime"]?.status)}`}>Runtime {checks["shared-runtime"]?.status || "unknown"}</span>
      </div>
      <div className="form-grid">
        <label>聊天助手<select name="chat_provider" defaultValue={config.chat_provider || "tmux"}>
          {runtimeChoices.map((choice) => <option value={choice.id} key={`chat-${choice.id}`}>{choice.label} · {choice.transport}</option>)}
        </select></label>
        <label>长任务助手<select name="runtime_provider" defaultValue={config.runtime_provider || "tmux"}>
          {runtimeChoices.map((choice) => <option value={choice.id} key={`runtime-${choice.id}`}>{choice.label} · {choice.transport}</option>)}
        </select></label>
      </div>
      <details>
        <summary>高级启动参数</summary>
        <label>Code CLI profile<select name="code_cli_profile" defaultValue={config.code_cli_profile || "codex-cli"}>
          <option value="codex-cli">codex-cli</option>
          <option value="claude-cli">claude-cli</option>
        </select></label>
        <label>Codex 命令<input name="codex_command" defaultValue={config.codex_command || "codex"} /></label>
        <label>Codex sandbox<input name="codex_sandbox" defaultValue={config.codex_sandbox || "workspace-write"} /></label>
        <label>Codex approval<input name="codex_approval" defaultValue={config.codex_approval || "never"} /></label>
        <label>Codex extra args<input name="codex_extra_args" defaultValue={config.codex_extra_args || ""} /></label>
        <label className="toggle"><input name="codex_no_alt_screen" type="checkbox" defaultChecked={Boolean(config.codex_no_alt_screen)} /> Codex no-alt-screen</label>
        <label className="toggle"><input name="codex_bypass" type="checkbox" defaultChecked={Boolean(config.codex_bypass)} /> Codex bypass</label>
        <label>Claude 命令<input name="claude_command" defaultValue={config.claude_command || "claude"} /></label>
        <label>Claude permission mode<input name="claude_permission_mode" defaultValue={config.claude_permission_mode || "dontAsk"} /></label>
        <label>Claude extra args<input name="claude_extra_args" defaultValue={config.claude_extra_args || ""} /></label>
        <label className="toggle"><input name="claude_skip_permissions" type="checkbox" defaultChecked={Boolean(config.claude_skip_permissions)} /> Claude skip permissions</label>
      </details>
      <button className="primary-button" type="submit"><Settings size={15} /> 保存助手配置</button>
    </form>
  );
}
