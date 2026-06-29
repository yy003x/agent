import { Activity, RefreshCw, Settings } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
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

export function SettingsPanel({ config, health, choices, allChoices, onSubmit, onValidate }: {
  config: RuntimeConfig;
  health: HealthPayload;
  choices?: RuntimeChoice[];
  allChoices?: RuntimeChoice[];
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onValidate: () => void;
}) {
  const checks = Object.fromEntries((health.checks ?? []).map((item) => [item.id, item]));
  const runtimeChoices = choices ?? [];
  const allRuntimeChoices = allChoices ?? [];
  const validatedCount = runtimeChoices.length;
  const totalCount = allRuntimeChoices.length || validatedCount;
  return (
    <form className="settings-form" onSubmit={onSubmit}>
      <div className="settings-grid">
        <div className="card"><h3>聊天助手</h3><span>{providerLabel(config.chat_provider)}</span></div>
        <div className="card"><h3>长任务助手</h3><span>{providerLabel(config.runtime_provider)}</span></div>
        <div className="card"><h3>已验证配置</h3><span>{validatedCount}/{totalCount}</span></div>
      </div>
      <div className="status-row">
        <span className={`pill ${statusClass(checks.codex?.status)}`}>Codex {checks.codex?.status || "unknown"}</span>
        <span className={`pill ${statusClass(checks.claude?.status)}`}>Claude {checks.claude?.status || "unknown"}</span>
        <span className={`pill ${statusClass(checks["agentrun-runtime"]?.status)}`}>AgentRun {checks["agentrun-runtime"]?.status || "unknown"}</span>
      </div>
      <div className="form-grid">
        <RuntimeSelector label="聊天助手" prefix="chat" config={config} choices={runtimeChoices} />
        <RuntimeSelector label="长任务助手" prefix="runtime" config={config} choices={runtimeChoices} />
      </div>
      <details>
        <summary>高级启动参数</summary>
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
      <div className="action-row">
        <button className="primary-button" type="submit"><Settings size={15} /> 保存助手配置</button>
        <button type="button" onClick={onValidate}><RefreshCw size={15} /> 验证配置</button>
      </div>
    </form>
  );
}

function RuntimeSelector({ label, prefix, config, choices }: {
  label: string;
  prefix: "chat" | "runtime";
  config: RuntimeConfig;
  choices: RuntimeChoice[];
}) {
  const currentProfile = prefix === "chat" ? config.chat_profile : config.runtime_profile;
  const currentProvider = prefix === "chat" ? config.chat_provider : config.runtime_provider;
  const initialChoice = choices.find((choice) => choice.profile === currentProfile || choice.id === currentProfile);
  const initialType = selectedType(choices, initialChoice, currentProvider);
  const initialName = initialChoice?.provider_name || firstName(choices, initialType);
  const initialProfile = initialChoice?.profile || firstProfile(choices, initialType, initialName);
  const [providerType, setProviderType] = useState(initialType);
  const [providerName, setProviderName] = useState(initialName);
  const [profile, setProfile] = useState(initialProfile);
  const resetKey = `${choices.map((choice) => choice.id).join("|")}::${currentProvider || ""}::${currentProfile || ""}`;

  useEffect(() => {
    const nextChoice = choices.find((choice) => choice.profile === currentProfile || choice.id === currentProfile);
    const nextType = selectedType(choices, nextChoice, currentProvider);
    const nextName = nextChoice?.provider_name || firstName(choices, nextType);
    setProviderType(nextType);
    setProviderName(nextName);
    setProfile(nextChoice?.profile || firstProfile(choices, nextType, nextName));
  }, [resetKey]);

  const providerTypes = useMemo(() => unique(choices.map(choiceType).filter(Boolean)), [choices]);
  const providerNames = useMemo(
    () => unique(choices.filter((choice) => choiceType(choice) === providerType).map((choice) => choice.provider_name || choice.id)),
    [choices, providerType],
  );
  const profiles = useMemo(
    () => choices.filter((choice) => choiceType(choice) === providerType && (choice.provider_name || choice.id) === providerName),
    [choices, providerType, providerName],
  );

  function pickType(value: string) {
    const name = firstName(choices, value);
    setProviderType(value);
    setProviderName(name);
    setProfile(firstProfile(choices, value, name));
  }

  function pickName(value: string) {
    setProviderName(value);
    setProfile(firstProfile(choices, providerType, value));
  }

  if (!choices.length) {
    return (
      <fieldset className="runtime-selector">
        <legend>{label}</legend>
        <input type="hidden" name={`${prefix}_provider`} value={currentProvider || "tmux"} />
        <input type="hidden" name={`${prefix}_profile`} value={currentProfile || ""} />
        <select disabled><option>暂无已验证配置</option></select>
      </fieldset>
    );
  }

  return (
    <fieldset className="runtime-selector">
      <legend>{label}</legend>
      <label>类型<select name={`${prefix}_provider`} value={providerType} onChange={(event) => pickType(event.target.value)}>
        {providerTypes.map((item) => <option value={item} key={`${prefix}-type-${item}`}>{providerLabel(item)}</option>)}
      </select></label>
      <label>Provider<select name={`${prefix}_provider_name`} value={providerName} onChange={(event) => pickName(event.target.value)}>
        {providerNames.map((item) => <option value={item} key={`${prefix}-name-${item}`}>{item}</option>)}
      </select></label>
      <label>Profile<select name={`${prefix}_profile`} value={profile} onChange={(event) => setProfile(event.target.value)}>
        {profiles.map((choice) => (
          <option value={choice.profile} key={`${prefix}-profile-${choice.profile}`}>
            {choice.label || choice.profile_name || choice.profile}
          </option>
        ))}
      </select></label>
    </fieldset>
  );
}

function choiceType(choice?: RuntimeChoice) {
  return choice?.provider_type || choice?.transport || "";
}

function unique(values: string[]) {
  return Array.from(new Set(values));
}

function firstName(choices: RuntimeChoice[], providerType: string) {
  const choice = choices.find((item) => choiceType(item) === providerType);
  return choice?.provider_name || choice?.id || "";
}

function firstProfile(choices: RuntimeChoice[], providerType: string, providerName: string) {
  const choice = choices.find((item) => choiceType(item) === providerType && (item.provider_name || item.id) === providerName);
  return choice?.profile || choice?.id || "";
}

function selectedType(choices: RuntimeChoice[], choice?: RuntimeChoice, currentProvider?: string) {
  const byChoice = choiceType(choice);
  if (byChoice) return byChoice;
  if (currentProvider && choices.some((item) => choiceType(item) === currentProvider)) {
    return currentProvider;
  }
  return choiceType(choices[0]) || currentProvider || "tmux";
}
