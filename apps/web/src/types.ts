export type RuntimeProvider = "codex_cli" | "claude_cli" | "fake";

export interface SessionSummary {
  session_id: string;
  title?: string;
  runtime?: RuntimeProvider | string;
  updated_at?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | string;
  content: string;
  ts?: string;
  pending?: boolean;
  turn_id?: string;
  result?: RuntimeResult;
}

export interface RuntimeResult {
  status?: string;
  assistant_message?: string;
  summary?: string;
  outputs?: OutputRef[];
  questions?: string[];
  errors?: string[];
}

export interface OutputRef {
  label?: string;
  name?: string;
  path: string;
  type?: string;
  status?: string;
  size?: number | null;
  mtime?: number;
  kind?: string;
}

export interface RuntimeConfig {
  chat_provider?: string;
  runtime_provider?: string;
  codex_command?: string;
  claude_command?: string;
  codex_no_alt_screen?: boolean;
  codex_sandbox?: string;
  codex_approval?: string;
  codex_bypass?: boolean;
  codex_extra_args?: string;
  claude_permission_mode?: string;
  claude_skip_permissions?: boolean;
  claude_extra_args?: string;
}

export interface RuntimeConfigPayload {
  config?: RuntimeConfig;
  effective?: Record<string, unknown>;
  allowed_providers?: string[];
  model_backends?: ModelBackend[];
}

export interface ModelBackend {
  id: string;
  label: string;
  provider: string;
  status: string;
  detail: string;
  key_env?: string;
  key_set?: boolean;
  model?: string;
}

export interface HealthPayload {
  checks?: Array<{ id: string; label: string; status: string; detail: string }>;
  summary?: Record<string, number>;
  model_backends?: ModelBackend[];
}

export interface RuntimeRun {
  run_id: string;
  state?: string;
  runtime?: string;
  command?: string;
  output_bytes?: number;
}

export interface OperatorProgress {
  title?: string;
  current_step?: string;
  provider?: string;
  provider_label?: string;
  status?: string;
  status_label?: string;
  activity?: string;
  elapsed_seconds?: number | null;
  friendly_error?: string;
  raw_error?: string;
  outputs?: OutputRef[];
  actions?: Array<{ label: string; action: string; style?: string }>;
}

export interface OperatorView {
  progress?: OperatorProgress;
  outputs?: OutputRef[];
  settings_summary?: {
    checks?: Record<string, string>;
    project_root?: string;
  };
  diagnostics_ref?: {
    event_count?: number;
    pending_turn_count?: number;
    runtime_status?: Record<string, unknown>;
  };
}

export interface SessionDetail extends SessionSummary {
  messages?: ChatMessage[];
  events?: Array<Record<string, unknown>>;
  linked_outputs?: OutputRef[];
  pending_turns?: Array<Record<string, unknown>>;
  runtime_status?: Record<string, unknown>;
  runtime_log_tail?: { text?: string };
  operator?: OperatorView;
}

export interface AppStatePayload {
  project_root: string;
  sessions: SessionSummary[];
  outputs: OutputRef[];
  runtime_runs: RuntimeRun[];
  runtime_config: RuntimeConfigPayload;
  skills: Array<Record<string, unknown>>;
}

export interface KbRow {
  id?: string;
  title?: string;
  modality?: string;
  source_path?: string;
  origin_dir?: string;
  caption?: string;
  score?: number;
}

export interface FilePreviewPayload {
  path?: string;
  kind?: "image" | "text" | "binary";
  data_url?: string;
  text?: string;
  truncated?: boolean;
  entries?: unknown[];
  message?: string;
}
