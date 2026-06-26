import {
  Activity,
  Copy,
  FolderOpen,
  Play,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  Square,
  Trash2
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { api } from "./api/client";
import type {
  AppStatePayload,
  FilePreviewPayload,
  HealthPayload,
  KbRow,
  OperatorView,
  OutputRef,
  RuntimeConfig,
  RuntimeConfigPayload,
  RuntimeRun,
  SessionDetail,
  SessionSummary
} from "./types";

type TabKey = "progress" | "materials" | "outputs" | "settings" | "diagnostics";

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "progress", label: "进度" },
  { key: "materials", label: "素材" },
  { key: "outputs", label: "产出" },
  { key: "settings", label: "设置" },
  { key: "diagnostics", label: "诊断" }
];

function providerLabel(value?: string) {
  if (value === "claude_cli") return "Claude";
  if (value === "fake") return "测试 Runtime";
  return "Codex";
}

function statusClass(value?: string) {
  if (["done", "ok", "idle", "success"].includes(value ?? "")) return "ok";
  if (["running", "queued", "waiting_result", "warn"].includes(value ?? "")) return "warn";
  if (["failed", "stopped", "missing"].includes(value ?? "")) return "bad";
  return "";
}

function titleFromMessage(content: string) {
  const firstLine = content.split(/\r?\n/).map((line) => line.trim()).find(Boolean) ?? "新会话";
  const firstSentence = firstLine.split(/(?<=[。！？!?])/)[0] || firstLine;
  return firstSentence.length > 36 ? `${firstSentence.slice(0, 36)}...` : firstSentence;
}

function outputType(path: string, label = "") {
  const text = `${path} ${label}`.toLowerCase();
  if (text.includes("xiaohongshu") || text.includes("小红书")) return "小红书图文";
  if (text.includes("moments") || text.includes("朋友圈")) return "朋友圈文案";
  if (text.includes("wechat") || text.includes("群话术") || text.includes("家长群")) return "家长群话术";
  if (text.includes("compliance") || text.includes("审核")) return "合规审核报告";
  if (text.includes("campaign") || text.includes("活动")) return "活动计划";
  if (text.includes("knowledge") || text.includes("sync")) return "知识库同步报告";
  return "运营产出";
}

function normalizeOutput(item: OutputRef): OutputRef {
  const label = item.label || item.name || item.path.split("/").pop() || item.path;
  return {
    ...item,
    label,
    type: item.type || outputType(item.path, label),
    status: item.status || "草稿"
  };
}

export function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSession, setCurrentSession] = useState<string>("");
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("progress");
  const [chatInput, setChatInput] = useState("");
  const [sessionEditMode, setSessionEditMode] = useState(false);
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set());
  const [outputs, setOutputs] = useState<OutputRef[]>([]);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigPayload>({});
  const [health, setHealth] = useState<HealthPayload>({});
  const [runs, setRuns] = useState<RuntimeRun[]>([]);
  const [kbRows, setKbRows] = useState<KbRow[]>([]);
  const [kbQuery, setKbQuery] = useState("");
  const [kbModality, setKbModality] = useState("all");
  const [preview, setPreview] = useState<FilePreviewPayload | null>(null);
  const [runtimePrompt, setRuntimePrompt] = useState("请完成一个健康检查任务，并把 result 写入指定文件。");
  const [runtimeLogs, setRuntimeLogs] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState("");

  const operator = sessionDetail?.operator;
  const config = runtimeConfig.config ?? {};

  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try {
      setError("");
      return await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return undefined;
    }
  }

  async function refreshState() {
    const data = await api<AppStatePayload>("/api/state");
    setSessions(data.sessions ?? []);
    setOutputs((data.outputs ?? []).map(normalizeOutput));
    setRuntimeConfig(data.runtime_config ?? {});
    setRuns(data.runtime_runs ?? []);
    return data;
  }

  async function refreshHealth() {
    setHealth(await api<HealthPayload>("/api/health"));
  }

  async function loadSession(sessionId: string) {
    const data = await api<SessionDetail>(`/api/chat/sessions/${sessionId}`);
    setCurrentSession(sessionId);
    setSessionDetail(data);
  }

  async function refreshCurrentSession() {
    if (!currentSession) return;
    const data = await api<SessionDetail>(`/api/chat/sessions/${currentSession}`);
    setSessionDetail(data);
  }

  async function createSession(title = "") {
    const data = await api<SessionSummary>("/api/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ title })
    });
    await refreshState();
    await loadSession(data.session_id);
  }

  async function deleteSession(sessionId: string) {
    if (!window.confirm(`确定物理删除会话 ${sessionId}？`)) return;
    await api(`/api/chat/sessions/${sessionId}`, { method: "DELETE" });
    const nextState = await refreshState();
    if (currentSession === sessionId) {
      setSessionDetail(null);
      setCurrentSession("");
      const next = nextState.sessions?.find((item) => item.session_id !== sessionId);
      if (next) await loadSession(next.session_id);
    }
  }

  async function deleteSelectedSessions() {
    const ids = Array.from(selectedSessions);
    if (!ids.length || !window.confirm(`确定物理删除选中的 ${ids.length} 个会话？`)) return;
    await api("/api/chat/sessions/delete", {
      method: "POST",
      body: JSON.stringify({ session_ids: ids })
    });
    setSelectedSessions(new Set());
    if (ids.includes(currentSession)) {
      setSessionDetail(null);
      setCurrentSession("");
    }
    const nextState = await refreshState();
    if (!currentSession && nextState.sessions?.[0]) await loadSession(nextState.sessions[0].session_id);
  }

  async function sendMessage() {
    const content = chatInput;
    if (!content.trim()) return;
    setChatInput("");
    let sessionId = currentSession;
    if (!sessionId) {
      const created = await api<SessionSummary>("/api/chat/sessions", {
        method: "POST",
        body: JSON.stringify({ title: titleFromMessage(content) })
      });
      sessionId = created.session_id;
      setCurrentSession(sessionId);
    }
    const data = await api<{ messages?: SessionDetail["messages"]; events?: SessionDetail["events"]; operator?: OperatorView; session?: SessionSummary }>(
      `/api/chat/sessions/${sessionId}/messages`,
      { method: "POST", body: JSON.stringify({ content, wait_seconds: 0 }) }
    );
    setSessionDetail((prev) => ({
      ...(prev ?? {}),
      ...(data.session ?? {}),
      session_id: sessionId,
      messages: data.messages ?? prev?.messages ?? [],
      events: data.events ?? prev?.events ?? [],
      operator: data.operator ?? prev?.operator
    }));
    await refreshState();
  }

  async function searchMaterials(event: FormEvent) {
    event.preventDefault();
    if (!kbQuery.trim()) return;
    const data = await api<{ ok: boolean; rows?: KbRow[]; error?: string }>(
      `/api/kb/search?query=${encodeURIComponent(kbQuery)}&modality=${encodeURIComponent(kbModality)}&topk=10`
    );
    if (!data.ok) throw new Error(data.error || "KB 搜索失败");
    setKbRows(data.rows ?? []);
  }

  async function previewFile(path: string) {
    const data = await api<FilePreviewPayload>(`/api/files?path=${encodeURIComponent(path)}`);
    setPreview(data);
    setActiveTab("outputs");
  }

  async function copyFile(path: string) {
    const data = await api<FilePreviewPayload>(`/api/files?path=${encodeURIComponent(path)}`);
    await navigator.clipboard.writeText(data.text ?? path);
  }

  async function openFileLocation(path: string) {
    await api("/api/files/open", { method: "POST", body: JSON.stringify({ path }) });
  }

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload: RuntimeConfig = {
      chat_provider: String(form.get("chat_provider") || "codex_cli"),
      runtime_provider: String(form.get("runtime_provider") || "codex_cli"),
      codex_command: String(form.get("codex_command") || "codex"),
      codex_sandbox: String(form.get("codex_sandbox") || "workspace-write"),
      codex_approval: String(form.get("codex_approval") || "never"),
      codex_extra_args: String(form.get("codex_extra_args") || ""),
      codex_no_alt_screen: form.get("codex_no_alt_screen") === "on",
      codex_bypass: form.get("codex_bypass") === "on",
      claude_command: String(form.get("claude_command") || "claude"),
      claude_permission_mode: String(form.get("claude_permission_mode") || "dontAsk"),
      claude_extra_args: String(form.get("claude_extra_args") || ""),
      claude_skip_permissions: form.get("claude_skip_permissions") === "on"
    };
    setRuntimeConfig(await api<RuntimeConfigPayload>("/api/config/runtime", {
      method: "POST",
      body: JSON.stringify(payload)
    }));
    await refreshState();
  }

  async function refreshRuns() {
    const data = await api<{ runs: RuntimeRun[] }>("/api/runtime/tmux/runs");
    setRuns(data.runs ?? []);
  }

  async function startRuntimeRun(event: FormEvent) {
    event.preventDefault();
    await api("/api/runtime/tmux/runs", {
      method: "POST",
      body: JSON.stringify({ runtime: config.runtime_provider || "codex_cli", prompt: runtimePrompt })
    });
    await refreshRuns();
  }

  async function stopSessionRuntime() {
    if (!currentSession || !window.confirm("确定停止当前会话里的助手任务？")) return;
    await api(`/api/chat/sessions/${currentSession}/runtime/stop`, { method: "POST", body: "{}" });
    await refreshCurrentSession();
  }

  async function loadRunLogs(runId: string) {
    const data = await api<{ text?: string }>(`/api/runtime/tmux/runs/${runId}/logs`);
    setRuntimeLogs(data.text ?? "");
  }

  useEffect(() => {
    guarded(async () => {
      const state = await refreshState();
      await refreshHealth();
      if (state?.sessions?.[0]) await loadSession(state.sessions[0].session_id);
    });
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      guarded(async () => {
        await refreshRuns();
        await refreshCurrentSession();
      });
    }, 4000);
    return () => window.clearInterval(timer);
  }, [currentSession]);

  const mergedOutputs = useMemo(() => {
    const current = (operator?.outputs ?? []).map(normalizeOutput);
    const seen = new Set(current.map((item) => item.path));
    return [...current, ...outputs.filter((item) => !seen.has(item.path))];
  }, [operator?.outputs, outputs]);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div>
            <h1>个人Agent工作台</h1>
            <span>本地运行</span>
          </div>
          <button className="icon-button" onClick={() => guarded(refreshState)} title="刷新">
            <RefreshCw size={17} />
          </button>
        </div>
        <div className="sidebar-actions">
          <button className="primary-button" onClick={() => guarded(() => createSession())}>
            <Plus size={16} /> 新会话
          </button>
          <button className={sessionEditMode ? "ghost-button active" : "ghost-button"} onClick={() => setSessionEditMode(!sessionEditMode)}>
            编辑
          </button>
        </div>
        {sessionEditMode && (
          <div className="bulk-actions">
            <button onClick={() => setSelectedSessions(new Set(sessions.map((item) => item.session_id)))}>全选</button>
            <button onClick={() => setSelectedSessions(new Set())}>清空</button>
            <button className="danger-button" disabled={!selectedSessions.size} onClick={() => guarded(deleteSelectedSessions)}>
              <Trash2 size={14} /> 删除
            </button>
          </div>
        )}
        <div className="session-list">
          {sessions.length ? sessions.map((session) => (
            <div className={session.session_id === currentSession ? "session-item active" : "session-item"} key={session.session_id}>
              {sessionEditMode && (
                <input
                  type="checkbox"
                  checked={selectedSessions.has(session.session_id)}
                  onChange={(event) => {
                    const next = new Set(selectedSessions);
                    if (event.target.checked) next.add(session.session_id);
                    else next.delete(session.session_id);
                    setSelectedSessions(next);
                  }}
                />
              )}
              <button onClick={() => guarded(() => loadSession(session.session_id))}>
                <strong>{session.title || session.session_id}</strong>
                <span>{providerLabel(session.runtime)} · {session.updated_at || ""}</span>
              </button>
              {sessionEditMode && (
                <button className="danger-icon" onClick={() => guarded(() => deleteSession(session.session_id))} title="物理删除">
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          )) : <div className="empty">暂无会话</div>}
        </div>
        <div className="health-strip">
          <span className="pill ok">可用 {health.summary?.ok ?? 0}</span>
          <span className="pill warn">提醒 {health.summary?.warn ?? 0}</span>
          <span className="pill bad">缺失 {health.summary?.missing ?? 0}</span>
        </div>
      </aside>

      <section className="chat-column">
        <header className="chat-header">
          <div>
            <h2>{sessionDetail?.title || "未选择会话"}</h2>
            <span>{currentSession ? `${providerLabel(sessionDetail?.runtime)} · ${currentSession}` : "创建会话后开始"}</span>
          </div>
          {operator?.progress?.status && <span className={`pill ${statusClass(operator.progress.status)}`}>{operator.progress.status_label || operator.progress.status}</span>}
        </header>
        <div className="message-list">
          {(sessionDetail?.messages ?? []).map((message) => (
            <article className={`message ${message.role === "user" ? "user" : "assistant"} ${message.pending ? "pending" : ""}`} key={message.id}>
              <div className="message-meta">{message.role === "user" ? "你" : "Agent"} · {message.ts}</div>
              <div className="message-text">{message.content}</div>
            </article>
          ))}
        </div>
        <form
          className="chat-form"
          onSubmit={(event) => {
            event.preventDefault();
            guarded(sendMessage);
          }}
        >
          <textarea
            value={chatInput}
            rows={4}
            placeholder="输入任务，例如：整理一下素材，同步知识库。"
            onChange={(event) => setChatInput(event.target.value)}
            onKeyDown={(event: KeyboardEvent<HTMLTextAreaElement>) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                guarded(sendMessage);
              }
            }}
          />
          <button className="primary-button" type="submit">
            <Send size={16} /> 发送
          </button>
        </form>
        {error && <div className="error-banner">{error}</div>}
      </section>

      <aside className="right-panel">
        <nav className="tabs">
          {tabs.map((tab) => (
            <button className={activeTab === tab.key ? "active" : ""} key={tab.key} onClick={() => setActiveTab(tab.key)}>
              {tab.label}
            </button>
          ))}
        </nav>

        {activeTab === "progress" && (
          <section className="panel">
            <ProgressPanel operator={operator} onStop={() => guarded(stopSessionRuntime)} onDiagnostics={() => setActiveTab("diagnostics")} onPreview={previewFile} />
          </section>
        )}

        {activeTab === "materials" && (
          <section className="panel">
            <form className="search-form" onSubmit={(event) => guarded(() => searchMaterials(event))}>
              <input value={kbQuery} onChange={(event) => setKbQuery(event.target.value)} placeholder="搜索图书、素材、caption" />
              <select value={kbModality} onChange={(event) => setKbModality(event.target.value)}>
                <option value="all">全部</option>
                <option value="doc">文档</option>
                <option value="image">图片</option>
                <option value="video">视频</option>
              </select>
              <button type="submit"><Search size={15} /> 搜索</button>
            </form>
            <div className="card-list">
              {kbRows.map((row, index) => (
                <article className="card" key={`${row.id || row.title}-${index}`}>
                  <h3>{row.title || row.id || "素材"}</h3>
                  <span>{row.modality || "素材"} · 已入库</span>
                  <p>{(row.caption || "").slice(0, 220)}</p>
                  <div className="action-row">
                    <button onClick={() => setChatInput((prev) => `${prev ? `${prev}\n\n` : ""}参考素材：${row.title || row.id}\n来源：${row.source_path || ""}\n摘要：${row.caption || ""}`)}>加入任务</button>
                    <button
                      disabled={!row.source_path}
                      onClick={() => {
                        const sourcePath = row.source_path;
                        if (sourcePath) {
                          void guarded(() => previewFile(sourcePath));
                        }
                      }}
                    >
                      查看来源
                    </button>
                  </div>
                </article>
              ))}
              {!kbRows.length && <div className="empty">暂无素材结果</div>}
            </div>
          </section>
        )}

        {activeTab === "outputs" && (
          <section className="panel">
            <div className="card-list">
              {mergedOutputs.map((item) => (
                <article className="card output-card" key={item.path}>
                  <h3>{item.label || item.path}</h3>
                  <span>{item.type} · {item.status}</span>
                  <div className="action-row">
                    <button onClick={() => guarded(() => previewFile(item.path))}>预览</button>
                    <button onClick={() => guarded(() => copyFile(item.path))}><Copy size={14} /> 复制</button>
                    <button onClick={() => guarded(() => openFileLocation(item.path))}><FolderOpen size={14} /> 打开</button>
                  </div>
                </article>
              ))}
              {!mergedOutputs.length && <div className="empty">outputs 暂无文件</div>}
            </div>
            {preview && <PreviewPanel preview={preview} />}
          </section>
        )}

        {activeTab === "settings" && (
          <section className="panel">
            <SettingsPanel config={config} health={health} onSubmit={(event) => guarded(() => saveSettings(event))} />
          </section>
        )}

        {activeTab === "diagnostics" && (
          <section className="panel diagnostics">
            <label className="toggle"><input type="checkbox" checked={advanced} onChange={(event) => setAdvanced(event.target.checked)} /> 高级模式</label>
            <div className="card">
              <h3>{operator?.progress?.title || "未选择会话"}</h3>
              <span>{operator?.progress?.status_label || "未启动"} · 事件 {operator?.diagnostics_ref?.event_count ?? 0}</span>
            </div>
            {advanced && (
              <>
                <details open>
                  <summary>当前会话 Runtime</summary>
                  <pre>{JSON.stringify(sessionDetail?.runtime_status ?? {}, null, 2)}</pre>
                  <pre>{sessionDetail?.runtime_log_tail?.text ?? ""}</pre>
                </details>
                <details>
                  <summary>事件流</summary>
                  <pre>{JSON.stringify(sessionDetail?.events ?? [], null, 2)}</pre>
                </details>
                <details>
                  <summary>Runtime Runs</summary>
                  <form className="runtime-form" onSubmit={(event) => guarded(() => startRuntimeRun(event))}>
                    <textarea value={runtimePrompt} onChange={(event) => setRuntimePrompt(event.target.value)} />
                    <button type="submit"><Play size={14} /> 启动测试任务</button>
                  </form>
                  <div className="card-list">
                    {runs.filter((run) => run.runtime !== "fake").map((run) => (
                      <article className="card" key={run.run_id}>
                        <h3>{run.run_id}</h3>
                        <span>{run.runtime} · {run.state} · {run.output_bytes || 0} bytes</span>
                        <div className="action-row">
                          <button onClick={() => guarded(() => loadRunLogs(run.run_id))}>日志</button>
                          <button onClick={() => guarded(async () => {
                            await api(`/api/runtime/tmux/runs/${run.run_id}/stop`, { method: "POST", body: "{}" });
                            await refreshRuns();
                          })}><Square size={14} /> 停止</button>
                        </div>
                      </article>
                    ))}
                  </div>
                  <pre>{runtimeLogs}</pre>
                </details>
                <details>
                  <summary>系统检查</summary>
                  <pre>{JSON.stringify(health, null, 2)}</pre>
                </details>
              </>
            )}
          </section>
        )}
      </aside>
    </main>
  );
}

function ProgressPanel(props: {
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

function PreviewPanel({ preview }: { preview: FilePreviewPayload }) {
  if (preview.kind === "image" && preview.data_url) {
    return <div className="preview"><img src={preview.data_url} alt={preview.path || "preview"} /></div>;
  }
  if (preview.kind === "text") {
    return <pre className="preview">{preview.text}</pre>;
  }
  return <pre className="preview">{JSON.stringify(preview.entries ?? preview.message ?? preview, null, 2)}</pre>;
}

function SettingsPanel({ config, health, onSubmit }: { config: RuntimeConfig; health: HealthPayload; onSubmit: (event: FormEvent<HTMLFormElement>) => void }) {
  const checks = Object.fromEntries((health.checks ?? []).map((item) => [item.id, item]));
  return (
    <form className="settings-form" onSubmit={onSubmit}>
      <div className="settings-grid">
        <div className="card"><h3>聊天助手</h3><span>{providerLabel(config.chat_provider)}</span></div>
        <div className="card"><h3>长任务助手</h3><span>{providerLabel(config.runtime_provider)}</span></div>
        <div className="card"><h3>执行模式</h3><span>shared runtime</span></div>
      </div>
      <div className="status-row">
        <span className={`pill ${statusClass(checks.codex?.status)}`}>Codex {checks.codex?.status || "unknown"}</span>
        <span className={`pill ${statusClass(checks.claude?.status)}`}>Claude {checks.claude?.status || "unknown"}</span>
        <span className={`pill ${statusClass(checks["shared-runtime"]?.status)}`}>Runtime {checks["shared-runtime"]?.status || "unknown"}</span>
      </div>
      <div className="form-grid">
        <label>聊天助手<select name="chat_provider" defaultValue={config.chat_provider || "codex_cli"}><option value="codex_cli">Codex</option><option value="claude_cli">Claude</option></select></label>
        <label>长任务助手<select name="runtime_provider" defaultValue={config.runtime_provider || "codex_cli"}><option value="codex_cli">Codex</option><option value="claude_cli">Claude</option></select></label>
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
      <button className="primary-button" type="submit"><Settings size={15} /> 保存助手配置</button>
    </form>
  );
}
