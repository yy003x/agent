const state = {
  currentSession: null,
  currentSessionData: null,
  operator: null,
  events: [],
  runtimePoll: null,
  chatPoll: null,
  sessions: [],
  selectedSessions: new Set(),
  sessionEditMode: false,
  outputs: [],
  kbRows: [],
  runtimeConfig: {},
  health: {},
  projectRoot: "",
  currentPreviewText: "",
  advancedMode: false,
};

const $ = (id) => document.getElementById(id);

function visibleProvider(value) {
  return value === "claude_cli" ? "claude_cli" : "codex_cli";
}

function providerLabel(value) {
  if (value === "claude_cli") return "Claude";
  if (value === "fake") return "测试 Runtime";
  return "Codex";
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function titleFromMessage(content) {
  const firstLine = String(content || "").split(/\r?\n/).map((line) => line.trim()).find(Boolean) || "新会话";
  const firstSentence = firstLine.split(/(?<=[。！？!?])/)[0].trim() || firstLine;
  return firstSentence.length > 36 ? `${firstSentence.slice(0, 36)}...` : firstSentence;
}

function relativeProjectPath(path) {
  const text = String(path || "");
  if (state.projectRoot && text.startsWith(`${state.projectRoot}/`)) {
    return text.slice(state.projectRoot.length + 1);
  }
  return text.replace(/^\/Users\/yang\/agents\/agent\//, "");
}

function isPreviewablePath(path) {
  const rel = relativeProjectPath(path);
  return /^(design|rules|skills|memory|workspace|outputs|runs|apps|scripts)\//.test(rel);
}

function statusClass(status) {
  if (["done", "ok", "idle"].includes(status)) return "status-ok";
  if (["running", "queued", "waiting_result", "warn"].includes(status)) return "status-warn";
  if (["failed", "stopped", "missing"].includes(status)) return "status-failed";
  return "";
}

function outputType(path, label = "") {
  const text = `${path} ${label}`.toLowerCase();
  if (text.includes("xiaohongshu") || text.includes("小红书")) return "小红书图文";
  if (text.includes("moments") || text.includes("朋友圈")) return "朋友圈文案";
  if (text.includes("wechat") || text.includes("群话术") || text.includes("家长群")) return "家长群话术";
  if (text.includes("compliance") || text.includes("审核")) return "合规审核报告";
  if (text.includes("campaign") || text.includes("活动")) return "活动计划";
  if (text.includes("profile") || text.includes("档案")) return "图书档案";
  if (text.includes("knowledge") || text.includes("sync") || text.includes("ingest")) return "知识库同步报告";
  if (text.includes("script") || text.includes("video") || text.includes("短视频")) return "短视频脚本";
  if (text.includes("checklist")) return "发布检查清单";
  if (text.endsWith(".json")) return "数据文件";
  return "运营产出";
}

function outputStatus(path) {
  const text = String(path || "").toLowerCase();
  if (text.includes("compliance") || text.includes("审核")) return "待审核";
  if (text.includes("checklist") || text.includes("publish")) return "可手动发布";
  return "草稿";
}

function renderSessions(sessions) {
  state.sessions = sessions || [];
  $("sessionList").innerHTML = state.sessions.map((s) => `
    <div class="item session-row ${s.session_id === state.currentSession ? "active" : ""} ${state.sessionEditMode ? "editing" : ""}">
      ${state.sessionEditMode ? `
        <input
          class="session-checkbox"
          type="checkbox"
          aria-label="选择会话 ${escapeHtml(s.session_id)}"
          data-select-session="${escapeHtml(s.session_id)}"
          ${state.selectedSessions.has(s.session_id) ? "checked" : ""}
        />
      ` : ""}
      <button class="session-open" type="button" data-session="${escapeHtml(s.session_id)}">
        <div class="item-title">${escapeHtml(s.title || s.session_id)}</div>
        <div class="item-meta">${escapeHtml(providerLabel(s.runtime))} · ${escapeHtml(s.updated_at || "")}</div>
      </button>
      ${state.sessionEditMode ? `
        <button
          class="danger-button session-delete"
          type="button"
          title="物理删除会话"
          data-delete-session="${escapeHtml(s.session_id)}"
        >删除</button>
      ` : ""}
    </div>
  `).join("") || `<div class="muted">暂无会话</div>`;
  document.querySelectorAll("[data-session]").forEach((el) => {
    el.addEventListener("click", () => loadSession(el.dataset.session));
  });
  document.querySelectorAll("[data-delete-session]").forEach((el) => {
    el.addEventListener("click", async (event) => {
      event.stopPropagation();
      await deleteSession(el.dataset.deleteSession);
    });
  });
  document.querySelectorAll("[data-select-session]").forEach((el) => {
    el.addEventListener("change", () => {
      if (el.checked) {
        state.selectedSessions.add(el.dataset.selectSession);
      } else {
        state.selectedSessions.delete(el.dataset.selectSession);
      }
      updateBulkSessionButtons();
    });
  });
  updateBulkSessionButtons();
}

function updateBulkSessionButtons() {
  $("sessionBulkActions").hidden = !state.sessionEditMode;
  $("sessionEditModeBtn").textContent = state.sessionEditMode ? "完成" : "编辑";
  $("sessionEditModeBtn").classList.toggle("active", state.sessionEditMode);
  $("selectAllSessionsBtn").disabled = !state.sessionEditMode || state.sessions.length === 0;
  $("deleteSelectedSessionsBtn").disabled = !state.sessionEditMode || state.selectedSessions.size === 0;
  $("clearSelectedSessionsBtn").disabled = !state.sessionEditMode || state.selectedSessions.size === 0;
}

function toggleSessionEditMode() {
  state.sessionEditMode = !state.sessionEditMode;
  if (!state.sessionEditMode) {
    state.selectedSessions.clear();
  }
  renderSessions(state.sessions);
}

function renderMessages(messages) {
  $("messages").innerHTML = (messages || []).map((m) => `
    <article class="message ${m.role === "user" ? "user" : "assistant"} ${m.pending ? "pending" : ""}">
      <div class="message-role">${m.role === "user" ? "你" : "Agent"} · ${escapeHtml(m.ts || "")}</div>
      <div class="message-content">${escapeHtml(m.content)}</div>
    </article>
  `).join("");
  $("messages").scrollTop = $("messages").scrollHeight;
}

function renderProgress(operator) {
  state.operator = operator || null;
  const progress = operator?.progress;
  if (!progress) {
    $("progressList").innerHTML = `<div class="muted">选择或创建一个会话后，这里会显示任务进度。</div>`;
    renderDiagnosticSummary(operator);
    return;
  }
  const outputs = progress.outputs || [];
  const actions = progress.actions || [];
  $("progressList").innerHTML = `
    <section class="progress-card">
      <div class="progress-header">
        <div>
          <div class="item-title">${escapeHtml(progress.title || "当前任务")}</div>
          <div class="item-meta">${escapeHtml(progress.current_step || "等待输入")} · ${escapeHtml(progress.provider_label || providerLabel(progress.provider))}</div>
        </div>
        <span class="pill ${statusClass(progress.status)}">${escapeHtml(progress.status_label || progress.status)}</span>
      </div>
      <div class="progress-activity">${escapeHtml(progress.activity || "")}</div>
      ${progress.elapsed_seconds != null ? `<div class="item-meta">已运行约 ${Math.floor(progress.elapsed_seconds / 60)} 分 ${progress.elapsed_seconds % 60} 秒</div>` : ""}
      ${progress.friendly_error ? `<div class="friendly-error">${escapeHtml(progress.friendly_error)}</div>` : ""}
      ${outputs.length ? `
        <div class="progress-outputs">
          <div class="section-caption">关联产出</div>
          ${outputs.map((item, index) => `
            <button type="button" class="inline-action" data-progress-output="${index}">
              ${escapeHtml(item.label || item.path)}
            </button>
          `).join("")}
        </div>
      ` : ""}
      ${actions.length ? `
        <div class="action-row">
          ${actions.map((action) => `
            <button type="button" class="${action.style === "danger" ? "danger-button" : "ghost-button"}" data-progress-action="${escapeHtml(action.action)}">
              ${escapeHtml(action.label)}
            </button>
          `).join("")}
        </div>
      ` : ""}
    </section>
  `;
  document.querySelectorAll("[data-progress-output]").forEach((el) => {
    el.addEventListener("click", () => {
      const output = outputs[Number(el.dataset.progressOutput)];
      if (output?.path) {
        activateTab("outputs");
        previewFile(output.path);
      }
    });
  });
  document.querySelectorAll("[data-progress-action]").forEach((el) => {
    el.addEventListener("click", async () => {
      if (el.dataset.progressAction === "diagnostics") {
        activateTab("diagnostics");
      }
      if (el.dataset.progressAction === "stop_session_runtime") {
        await stopCurrentSessionRuntime();
      }
    });
  });
  renderDiagnosticSummary(operator);
  renderProviderConfig(state.runtimeConfig);
}

function renderDiagnosticSummary(operator) {
  const progress = operator?.progress;
  const ref = operator?.diagnostics_ref || {};
  $("diagnosticSummary").innerHTML = `
    <div class="item">
      <div class="item-title">${escapeHtml(progress?.title || "未选择会话")}</div>
      <div class="item-meta">
        ${progress ? `${escapeHtml(progress.status_label)} · ${escapeHtml(progress.provider_label)}` : "打开高级模式查看运行细节"}
      </div>
      ${progress?.raw_error ? `<details><summary>原始错误</summary><pre>${escapeHtml(progress.raw_error)}</pre></details>` : ""}
      <div class="item-meta">事件 ${ref.event_count || 0} · 运行中 turn ${ref.pending_turn_count || 0}</div>
    </div>
  `;
}

function renderEvents(events) {
  $("eventList").innerHTML = (events || []).slice().reverse().map((e) => `
    <div class="event">
      <div class="item-title">${escapeHtml(e.title || e.type)}</div>
      <div class="event-meta">${escapeHtml(e.status || "")} ${escapeHtml(e.ts || "")}</div>
      ${e.data ? `<pre>${escapeHtml(JSON.stringify(e.data, null, 2))}</pre>` : ""}
    </div>
  `).join("") || `<div class="muted">暂无事件</div>`;
}

function renderChatRuntime(session) {
  const status = session?.runtime_status || {};
  const pending = session?.pending_turns || [];
  const logTail = session?.runtime_log_tail || {};
  const stateLabel = pending.length
    ? `${pending.length} 个 turn 运行中`
    : (status.state || "未启动");
  $("chatRuntimeStatus").innerHTML = `
    <div class="item">
      <div class="item-title">${escapeHtml(stateLabel)}</div>
      <div class="item-meta">
        ${escapeHtml(status.runtime || "")}
        ${status.pane_id ? ` · pane ${escapeHtml(status.pane_id)}` : ""}
        ${status.phase ? ` · ${escapeHtml(status.phase)}` : ""}
        ${status.output_bytes != null ? ` · ${status.output_bytes} bytes` : ""}
        ${status.bytes_per_sec != null ? ` · ${status.bytes_per_sec} B/s` : ""}
      </div>
      ${pending.length ? `<div class="item-meta">当前 turn：${escapeHtml(pending.map((item) => item.turn_id).join(", "))}</div>` : ""}
      ${!pending.length && status.result_exists ? `<div class="item-meta status-ok">result.json 已生成</div>` : ""}
      ${status.error ? `<div class="item-meta status-failed">${escapeHtml(status.error)}</div>` : ""}
    </div>
  `;
  $("chatRuntimeLogs").textContent = logTail.text || "";
  $("chatRuntimeLogs").scrollTop = $("chatRuntimeLogs").scrollHeight;
}

function renderSkills(skills) {
  $("skillList").innerHTML = (skills || []).map((skill) => `
    <div class="item">
      <div class="item-title">${escapeHtml(skill.name)} · ${escapeHtml(skill.category || "未分类")}</div>
      <div class="item-meta">${escapeHtml(skill.title || "")}</div>
      ${skill.trigger ? `<div>${escapeHtml(skill.trigger)}</div>` : ""}
      ${(skill.capabilities || []).length ? `
        <div class="item-meta">能力：${escapeHtml(skill.capabilities.join(" / "))}</div>
      ` : ""}
      <details>
        <summary>原始信息</summary>
        ${(skill.commands || []).length ? `<pre>${escapeHtml(skill.commands.slice(0, 8).join("\n"))}</pre>` : ""}
        <div class="item-meta">${escapeHtml(skill.skill_file || "")}</div>
      </details>
    </div>
  `).join("") || `<div class="muted">未发现项目 skill</div>`;
}

async function refreshState() {
  const data = await api("/api/state");
  state.projectRoot = data.project_root || state.projectRoot;
  state.outputs = data.outputs || [];
  state.runtimeConfig = data.runtime_config || {};
  renderSessions(data.sessions || []);
  renderOutputs(state.outputs);
  renderRuns(data.runtime_runs || []);
  renderProviderConfig(state.runtimeConfig);
  renderSkills(data.skills || []);
  await refreshHealth();
  return data;
}

async function refreshHealth() {
  const data = await api("/api/health");
  state.health = data;
  const summary = data.summary || {};
  $("healthSummary").innerHTML = `
    <span class="pill status-ok">可用 ${summary.ok || 0}</span>
    <span class="pill status-warn">提醒 ${summary.warn || 0}</span>
    <span class="pill status-missing">缺失 ${summary.missing || 0}</span>
  `;
  $("healthList").innerHTML = (data.checks || []).map((c) => `
    <div class="health-item">
      <div class="item-title ${c.status === "ok" ? "status-ok" : c.status === "warn" ? "status-warn" : "status-missing"}">
        ${escapeHtml(c.label)} · ${escapeHtml(c.status)}
      </div>
      <div class="item-meta">${escapeHtml(c.detail)}</div>
    </div>
  `).join("");
  renderProviderConfig(state.runtimeConfig);
}

async function newSession(title = "") {
  const data = await api("/api/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  state.currentSession = data.session_id;
  await refreshState();
  await loadSession(state.currentSession);
}

async function loadSession(sessionId) {
  const data = await api(`/api/chat/sessions/${sessionId}`);
  state.currentSession = sessionId;
  state.currentSessionData = data;
  $("sessionTitle").textContent = `${data.title || "会话"} · ${providerLabel(data.runtime)} · ${sessionId}`;
  renderMessages(data.messages || []);
  state.events = data.events || [];
  renderEvents(state.events);
  renderChatRuntime(data);
  renderProgress(data.operator);
  renderOutputs(state.outputs);
  renderSessions(state.sessions);
}

function clearCurrentSession() {
  state.currentSession = null;
  state.currentSessionData = null;
  state.operator = null;
  $("sessionTitle").textContent = "未选择会话";
  renderMessages([]);
  state.events = [];
  renderEvents(state.events);
  renderChatRuntime({});
  renderProgress(null);
}

async function deleteSession(sessionId) {
  const ok = window.confirm(
    `确定物理删除会话 ${sessionId}？\n\n这会删除 runs/workbench/sessions/${sessionId} 下的消息、事件、turns 和 runtime 元数据，无法从 UI 恢复。`
  );
  if (!ok) return;

  await api(`/api/chat/sessions/${sessionId}`, { method: "DELETE" });
  state.selectedSessions.delete(sessionId);
  if (state.currentSession === sessionId) {
    clearCurrentSession();
  }
  const data = await refreshState();
  if (!state.currentSession) {
    const next = (data.sessions || []).find((item) => item.session_id !== sessionId);
    if (next) {
      await loadSession(next.session_id);
    }
  }
}

async function deleteSelectedSessions() {
  const sessionIds = Array.from(state.selectedSessions);
  if (!sessionIds.length) return;
  const ok = window.confirm(
    `确定物理删除选中的 ${sessionIds.length} 个会话？\n\n会删除对应 runs/workbench/sessions/chat-* 目录，无法从 UI 恢复。`
  );
  if (!ok) return;

  await api("/api/chat/sessions/delete", {
    method: "POST",
    body: JSON.stringify({ session_ids: sessionIds }),
  });
  if (state.currentSession && state.selectedSessions.has(state.currentSession)) {
    clearCurrentSession();
  }
  state.selectedSessions.clear();
  const data = await refreshState();
  if (!state.currentSession && (data.sessions || []).length) {
    await loadSession(data.sessions[0].session_id);
  }
}

async function sendMessage(content) {
  if (!state.currentSession) {
    await newSession(titleFromMessage(content));
  }
  const data = await api(`/api/chat/sessions/${state.currentSession}/messages`, {
    method: "POST",
    body: JSON.stringify({ content, wait_seconds: 0 }),
  });
  renderMessages(data.messages || []);
  state.events.push(...(data.events || []));
  renderEvents(state.events);
  renderProgress(data.operator);
  if (data.session) {
    $("sessionTitle").textContent = `${data.session.title || "会话"} · ${providerLabel(data.session.runtime)} · ${data.session.session_id}`;
  }
  await refreshState();
}

async function refreshCurrentSession() {
  if (!state.currentSession) return;
  try {
    const data = await api(`/api/chat/sessions/${state.currentSession}`);
    state.currentSessionData = data;
    renderMessages(data.messages || []);
    state.events = data.events || [];
    renderEvents(state.events);
    renderChatRuntime(data);
    renderProgress(data.operator);
    renderOutputs(state.outputs);
  } catch (err) {
    console.warn("refresh current session failed", err);
  }
}

function normalizeOutputEntry(item) {
  const path = item.path || "";
  const name = item.name || path.split("/").pop() || path;
  return {
    ...item,
    label: item.label || name,
    path,
    type: item.type || outputType(path, name),
    status: item.status || outputStatus(path),
  };
}

function outputGroupFor(item, currentPaths) {
  if (currentPaths.has(item.path)) return "本次会话产出";
  const today = new Date().toISOString().slice(0, 10);
  if (item.path.includes(today)) return "今天产出";
  if (item.mtime) {
    const mtime = new Date(item.mtime * 1000).toISOString().slice(0, 10);
    if (mtime === today) return "今天产出";
  }
  return "历史产出";
}

function renderOutputs(entries) {
  const currentOutputs = (state.operator?.outputs || []).map(normalizeOutputEntry);
  const currentPaths = new Set(currentOutputs.map((item) => item.path));
  const globalOutputs = (entries || [])
    .map(normalizeOutputEntry)
    .filter((item) => !currentPaths.has(item.path));
  const all = [...currentOutputs, ...globalOutputs];
  if (!all.length) {
    $("outputList").innerHTML = `<div class="muted">outputs 暂无文件</div>`;
    return;
  }
  const groups = new Map();
  all.forEach((item) => {
    const group = outputGroupFor(item, currentPaths);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(item);
  });
  const orderedGroups = ["本次会话产出", "今天产出", "历史产出"].filter((name) => groups.has(name));
  $("outputList").innerHTML = orderedGroups.map((group) => `
    <section class="output-group">
      <div class="section-caption">${escapeHtml(group)}</div>
      ${(groups.get(group) || []).map((item, index) => {
        const globalIndex = all.indexOf(item);
        return `
          <div class="item output-card" data-output-index="${globalIndex}">
            <div class="item-title">${escapeHtml(item.label)}</div>
            <div class="item-meta">${escapeHtml(item.type)} · ${escapeHtml(item.status)}</div>
            <div class="action-row">
              <button type="button" data-output-preview="${globalIndex}">预览</button>
              <button type="button" data-output-copy="${globalIndex}">复制内容</button>
              <button type="button" data-output-open="${globalIndex}">打开文件夹</button>
              <button type="button" data-output-edit="${globalIndex}">标记需修改</button>
            </div>
            <details>
              <summary>路径详情</summary>
              <div class="item-meta">${escapeHtml(item.path)}${item.size != null ? ` · ${item.size} bytes` : ""}</div>
            </details>
          </div>
        `;
      }).join("")}
    </section>
  `).join("");
  document.querySelectorAll("[data-output-preview]").forEach((el) => {
    el.addEventListener("click", () => previewFile(all[Number(el.dataset.outputPreview)].path));
  });
  document.querySelectorAll("[data-output-copy]").forEach((el) => {
    el.addEventListener("click", () => copyFileText(all[Number(el.dataset.outputCopy)].path));
  });
  document.querySelectorAll("[data-output-open]").forEach((el) => {
    el.addEventListener("click", () => openFileLocation(all[Number(el.dataset.outputOpen)].path));
  });
  document.querySelectorAll("[data-output-edit]").forEach((el) => {
    el.addEventListener("click", () => {
      const card = el.closest(".output-card");
      const meta = card?.querySelector(".item-meta");
      if (meta) meta.textContent = meta.textContent.replace(/草稿|待审核|可手动发布|已归档/g, "需修改");
    });
  });
}

async function previewFile(path) {
  const relPath = relativeProjectPath(path);
  const data = await api(`/api/files?path=${encodeURIComponent(relPath)}`);
  state.currentPreviewText = "";
  if (data.kind === "image") {
    $("filePreview").innerHTML = `<img src="${data.data_url}" alt="${escapeHtml(data.path)}" />`;
  } else if (data.kind === "text") {
    state.currentPreviewText = data.text || "";
    $("filePreview").innerHTML = `
      <div class="preview-toolbar">
        <div class="item-meta">${escapeHtml(data.path)}${data.truncated ? " · truncated" : ""}</div>
        <button type="button" data-copy-current-preview>复制当前预览</button>
      </div>
      <pre>${escapeHtml(data.text)}</pre>
    `;
    const copyButton = document.querySelector("[data-copy-current-preview]");
    if (copyButton) copyButton.addEventListener("click", () => navigator.clipboard.writeText(state.currentPreviewText));
  } else if (data.entries) {
    $("filePreview").innerHTML = `<pre>${escapeHtml(JSON.stringify(data.entries, null, 2))}</pre>`;
  } else {
    $("filePreview").innerHTML = `<div class="muted">${escapeHtml(data.message || "无法预览")}</div>`;
  }
}

async function copyFileText(path) {
  const relPath = relativeProjectPath(path);
  const data = await api(`/api/files?path=${encodeURIComponent(relPath)}`);
  const text = data.kind === "text" ? data.text : relPath;
  await navigator.clipboard.writeText(text || relPath);
}

async function openFileLocation(path) {
  const relPath = relativeProjectPath(path);
  await api("/api/files/open", {
    method: "POST",
    body: JSON.stringify({ path: relPath }),
  });
}

async function searchKb(query, modality) {
  $("kbResults").innerHTML = `<div class="muted">搜索中...</div>`;
  const data = await api(`/api/kb/search?query=${encodeURIComponent(query)}&modality=${encodeURIComponent(modality)}&topk=10`);
  if (!data.ok) {
    $("kbResults").innerHTML = `<div class="item status-missing">${escapeHtml(data.error)}</div>`;
    return;
  }
  state.kbRows = data.rows || [];
  $("kbResults").innerHTML = state.kbRows.map((r, index) => {
    const relSource = relativeProjectPath(r.source_path || "");
    const canPreview = isPreviewablePath(relSource);
    return `
      <div class="item material-card">
        <div class="item-title">${escapeHtml(r.title || r.id || "素材")}</div>
        <div class="item-meta">${escapeHtml(r.modality || "素材")} · 已入库${r.origin_dir ? ` · ${escapeHtml(r.origin_dir)}` : ""}</div>
        <div class="material-caption">${escapeHtml((r.caption || "").slice(0, 220))}</div>
        <div class="action-row">
          <button type="button" data-material-add="${index}">加入本次任务</button>
          <button type="button" data-material-preview="${index}" ${canPreview ? "" : "disabled"}>查看来源</button>
          <button type="button" data-material-prepare="${index}">准备入库</button>
          <button type="button" data-material-sync="${index}">同步知识库</button>
        </div>
        <details>
          <summary>来源详情</summary>
          <div class="item-meta">${escapeHtml(relSource)}</div>
          ${r.score != null ? `<div class="item-meta">score ${escapeHtml(r.score)}</div>` : ""}
          ${r.id ? `<div class="item-meta">id ${escapeHtml(r.id)}</div>` : ""}
        </details>
      </div>
    `;
  }).join("") || `<div class="muted">无命中</div>`;
  document.querySelectorAll("[data-material-add]").forEach((el) => {
    el.addEventListener("click", () => addMaterialToInput(state.kbRows[Number(el.dataset.materialAdd)]));
  });
  document.querySelectorAll("[data-material-preview]").forEach((el) => {
    el.addEventListener("click", () => previewFile(state.kbRows[Number(el.dataset.materialPreview)]?.source_path || ""));
  });
  document.querySelectorAll("[data-material-prepare]").forEach((el) => {
    el.addEventListener("click", () => addMaterialCommand(state.kbRows[Number(el.dataset.materialPrepare)], "请基于这个素材准备入库清单："));
  });
  document.querySelectorAll("[data-material-sync]").forEach((el) => {
    el.addEventListener("click", () => addMaterialCommand(state.kbRows[Number(el.dataset.materialSync)], "请把这个素材相关资料同步到知识库，并给我同步报告："));
  });
}

function addMaterialToInput(row) {
  if (!row) return;
  const relSource = relativeProjectPath(row.source_path || "");
  const snippet = `参考素材：${row.title || row.id}\n来源：${relSource}\n摘要：${row.caption || ""}`;
  const input = $("chatInput");
  input.value = input.value.trim() ? `${input.value.trim()}\n\n${snippet}` : snippet;
  input.focus();
}

function addMaterialCommand(row, prefix) {
  if (!row) return;
  const relSource = relativeProjectPath(row.source_path || "");
  const input = $("chatInput");
  input.value = `${prefix}\n标题：${row.title || row.id}\n来源：${relSource}\n摘要：${row.caption || ""}`;
  input.focus();
}

function setProviderConfigForm(config) {
  $("chatProvider").value = visibleProvider(config.chat_provider);
  $("runtimeProvider").value = visibleProvider(config.runtime_provider);
  $("codexCommand").value = config.codex_command || "codex";
  $("codexSandbox").value = config.codex_sandbox || "workspace-write";
  $("codexApproval").value = config.codex_approval || "never";
  $("codexExtraArgs").value = config.codex_extra_args || "";
  $("codexNoAltScreen").checked = Boolean(config.codex_no_alt_screen);
  $("codexBypass").checked = Boolean(config.codex_bypass);
  $("claudeCommand").value = config.claude_command || "claude";
  $("claudePermissionMode").value = config.claude_permission_mode || "dontAsk";
  $("claudeExtraArgs").value = config.claude_extra_args || "";
  $("claudeSkipPermissions").checked = Boolean(config.claude_skip_permissions);
}

function readProviderConfigForm() {
  return {
    chat_provider: $("chatProvider").value,
    runtime_provider: $("runtimeProvider").value,
    codex_command: $("codexCommand").value.trim() || "codex",
    codex_sandbox: $("codexSandbox").value.trim() || "workspace-write",
    codex_approval: $("codexApproval").value.trim() || "never",
    codex_extra_args: $("codexExtraArgs").value,
    codex_no_alt_screen: $("codexNoAltScreen").checked,
    codex_bypass: $("codexBypass").checked,
    claude_command: $("claudeCommand").value.trim() || "claude",
    claude_permission_mode: $("claudePermissionMode").value.trim() || "dontAsk",
    claude_extra_args: $("claudeExtraArgs").value,
    claude_skip_permissions: $("claudeSkipPermissions").checked,
  };
}

function renderProviderConfig(runtimeConfig) {
  const config = runtimeConfig.config || {};
  setProviderConfigForm(config);
  $("runtimeType").value = visibleProvider(config.runtime_provider);
  $("providerQuickBtn").textContent = `助手: ${providerLabel(config.chat_provider)}`;
  $("providerQuickBtn").title = `聊天助手: ${providerLabel(config.chat_provider)}；长任务助手: ${providerLabel(config.runtime_provider)}`;
  const settings = state.operator?.settings_summary;
  const checks = settings?.checks || {};
  $("providerSummary").innerHTML = `
    <div class="settings-grid">
      <div class="item">
        <div class="item-title">聊天助手</div>
        <div class="item-meta">${escapeHtml(providerLabel(config.chat_provider))}</div>
      </div>
      <div class="item">
        <div class="item-title">长任务助手</div>
        <div class="item-meta">${escapeHtml(providerLabel(config.runtime_provider))}</div>
      </div>
      <div class="item">
        <div class="item-title">执行模式</div>
        <div class="item-meta">tmux 真实 CLI 会话</div>
      </div>
      <div class="item">
        <div class="item-title">项目目录</div>
        <div class="item-meta">${escapeHtml(state.projectRoot || settings?.project_root || "")}</div>
      </div>
    </div>
    <div class="status-row">
      <span class="pill ${statusClass(checks.codex)}">Codex ${escapeHtml(checks.codex || "unknown")}</span>
      <span class="pill ${statusClass(checks.claude)}">Claude ${escapeHtml(checks.claude || "unknown")}</span>
      <span class="pill ${statusClass(checks.tmux)}">tmux ${escapeHtml(checks.tmux || "unknown")}</span>
    </div>
  `;
}

async function saveProviderConfig() {
  const data = await api("/api/config/runtime", {
    method: "POST",
    body: JSON.stringify(readProviderConfigForm()),
  });
  state.runtimeConfig = data;
  renderProviderConfig(data);
  await refreshState();
}

function renderRuns(runs) {
  const visibleRuns = (runs || []).filter((r) => r.runtime !== "fake");
  $("runtimeRuns").innerHTML = visibleRuns.map((r) => `
    <div class="item">
      <div class="item-title">${escapeHtml(r.run_id)} · <span class="${r.state === "done" ? "status-ok" : r.state === "failed" ? "status-failed" : ""}">${escapeHtml(r.state)}</span></div>
      <div class="item-meta">${escapeHtml(r.runtime)} · ${escapeHtml(r.command)} · ${r.output_bytes || 0} bytes</div>
      <div class="action-row">
        <button type="button" data-log="${escapeHtml(r.run_id)}">日志</button>
        <button type="button" data-stop="${escapeHtml(r.run_id)}">停止</button>
      </div>
    </div>
  `).join("") || `<div class="muted">暂无 runtime run</div>`;
  document.querySelectorAll("[data-log]").forEach((el) => el.addEventListener("click", () => loadLogs(el.dataset.log)));
  document.querySelectorAll("[data-stop]").forEach((el) => el.addEventListener("click", () => stopRun(el.dataset.stop)));
}

async function refreshRuns() {
  const data = await api("/api/runtime/tmux/runs");
  renderRuns(data.runs || []);
}

async function loadLogs(runId) {
  const data = await api(`/api/runtime/tmux/runs/${runId}/logs`);
  $("runtimeLogs").textContent = data.text || "";
}

async function stopRun(runId) {
  await api(`/api/runtime/tmux/runs/${runId}/stop`, { method: "POST", body: "{}" });
  await refreshRuns();
}

async function stopCurrentSessionRuntime() {
  if (!state.currentSession) return;
  const ok = window.confirm("确定停止当前会话里的助手任务？未完成的本轮结果会标记为已停止。");
  if (!ok) return;
  await api(`/api/chat/sessions/${state.currentSession}/runtime/stop`, { method: "POST", body: "{}" });
  await refreshCurrentSession();
}

async function startRuntime(runtime, prompt) {
  await api("/api/runtime/tmux/runs", {
    method: "POST",
    body: JSON.stringify({ runtime, prompt }),
  });
  await refreshRuns();
}

function activateTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
}

async function submitChatInput() {
  const content = $("chatInput").value;
  if (!content.trim()) return;
  $("chatInput").value = "";
  await sendMessage(content);
}

function bindUi() {
  $("newSessionBtn").addEventListener("click", () => newSession());
  $("sessionEditModeBtn").addEventListener("click", toggleSessionEditMode);
  $("refreshBtn").addEventListener("click", refreshState);
  $("providerQuickBtn").addEventListener("click", () => activateTab("settings"));
  $("advancedModeToggle").addEventListener("change", (event) => {
    state.advancedMode = event.target.checked;
    document.body.classList.toggle("advanced-mode", state.advancedMode);
  });
  $("selectAllSessionsBtn").addEventListener("click", () => {
    if (!state.sessionEditMode) return;
    state.sessions.forEach((item) => state.selectedSessions.add(item.session_id));
    renderSessions(state.sessions);
  });
  $("clearSelectedSessionsBtn").addEventListener("click", () => {
    if (!state.sessionEditMode) return;
    state.selectedSessions.clear();
    renderSessions(state.sessions);
  });
  $("deleteSelectedSessionsBtn").addEventListener("click", deleteSelectedSessions);
  $("chatInput").addEventListener("keydown", async (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      await submitChatInput();
    }
  });
  $("chatForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitChatInput();
  });
  $("kbForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await searchKb($("kbQuery").value.trim(), $("kbModality").value);
  });
  $("providerConfigForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveProviderConfig();
  });
  $("runtimeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await startRuntime($("runtimeType").value, $("runtimePrompt").value.trim());
  });
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab)));
}

async function init() {
  bindUi();
  renderProgress(null);
  const data = await refreshState();
  if (!state.currentSession && (data.sessions || []).length) {
    await loadSession(data.sessions[0].session_id);
  }
  state.runtimePoll = setInterval(refreshRuns, 5000);
  state.chatPoll = setInterval(refreshCurrentSession, 3000);
}

init().catch((err) => {
  console.error(err);
  alert(err.message || err);
});
