"""Runtime configuration helpers for the workbench API."""
from __future__ import annotations

from apps.runtime import model_backends

from apps.api.services.workbench_support import (
    CHAT_RUNTIME,
    CONFIG_PATH,
    MAIN_RUNTIME,
    USER_RUNTIMES,
    read_json,
    valid_user_runtime,
    write_json,
)


def _default_config() -> dict:
    effective = MAIN_RUNTIME.effective_runtime_config()
    return {
        "chat_provider": valid_user_runtime(CHAT_RUNTIME, "tmux"),
        "runtime_provider": valid_user_runtime(MAIN_RUNTIME.default_runtime(), "tmux"),
        "code_cli_profile": effective["provider_profiles"]["code_cli"]["profile"],
        "codex_command": "codex",
        "claude_command": "claude",
        "codex_no_alt_screen": effective["codex"]["no_alt_screen"],
        "codex_sandbox": effective["codex"]["sandbox"],
        "codex_approval": effective["codex"]["approval"],
        "codex_bypass": effective["codex"]["approval"] == "bypass",
        "codex_extra_args": effective["codex"].get("extra_args", ""),
        "claude_permission_mode": effective["claude"]["permission_mode"],
        "claude_skip_permissions": effective["claude"]["skip_permissions"],
        "claude_extra_args": effective["claude"].get("extra_args", ""),
    }


def _sanitize_config(data: dict) -> dict:
    defaults = _default_config()
    config = {**defaults, **(data or {})}
    config["chat_provider"] = valid_user_runtime(str(config.get("chat_provider", defaults["chat_provider"])), defaults["chat_provider"])
    config["runtime_provider"] = valid_user_runtime(str(config.get("runtime_provider", defaults["runtime_provider"])), defaults["runtime_provider"])
    config["code_cli_profile"] = _valid_code_cli_profile(str(config.get("code_cli_profile") or defaults["code_cli_profile"]))
    config["codex_command"] = str(config.get("codex_command") or "codex").strip() or "codex"
    config["claude_command"] = str(config.get("claude_command") or "claude").strip() or "claude"
    config["codex_no_alt_screen"] = bool(config.get("codex_no_alt_screen"))
    config["codex_bypass"] = bool(config.get("codex_bypass"))
    config["codex_sandbox"] = str(config.get("codex_sandbox") or defaults["codex_sandbox"]).strip()
    config["codex_approval"] = str(config.get("codex_approval") or defaults["codex_approval"]).strip()
    config["codex_extra_args"] = str(config.get("codex_extra_args") or "")
    config["claude_permission_mode"] = str(config.get("claude_permission_mode") or defaults["claude_permission_mode"]).strip()
    config["claude_skip_permissions"] = bool(config.get("claude_skip_permissions"))
    config["claude_extra_args"] = str(config.get("claude_extra_args") or "")
    return config


def workbench_config() -> dict:
    return _sanitize_config(read_json(CONFIG_PATH, {}))


def save_workbench_config(data: dict) -> dict:
    config = _sanitize_config(data)
    write_json(CONFIG_PATH, config)
    return runtime_config_payload(config)


def _runtime_options_from_config(config: dict) -> dict:
    return {
        "code_cli_profile": config["code_cli_profile"],
        "codex_no_alt_screen": config["codex_no_alt_screen"],
        "codex_bypass": config["codex_bypass"],
        "codex_sandbox": config["codex_sandbox"],
        "codex_approval": config["codex_approval"],
        "codex_extra_args": config["codex_extra_args"],
        "claude_permission_mode": config["claude_permission_mode"],
        "claude_skip_permissions": config["claude_skip_permissions"],
        "claude_extra_args": config["claude_extra_args"],
    }


def _command_for_runtime(config: dict, runtime: str) -> str | None:
    if runtime == "code_cli" and config.get("code_cli_profile") == "codex-cli":
        return config["codex_command"]
    if runtime == "code_cli" and config.get("code_cli_profile") == "claude-cli":
        return config["claude_command"]
    return None


def _valid_code_cli_profile(value: str) -> str:
    return value if value in {"codex-cli", "claude-cli"} else "codex-cli"


def _runtime_choices() -> list[dict]:
    return [
        {"id": "tmux", "label": "Tmux", "transport": "tmux", "profile": "tmux-codex"},
        {"id": "code_cli", "label": "Code CLI", "transport": "code_cli", "profile": "configurable"},
        {"id": "llm_api", "label": "LLM API", "transport": "llm_api", "profile": "llm-api"},
    ]


def runtime_config_payload(config: dict | None = None) -> dict:
    config = config or workbench_config()
    options = _runtime_options_from_config(config)
    return {
        "config": config,
        "effective": MAIN_RUNTIME.effective_runtime_config(options),
        "config_path": str(CONFIG_PATH),
        "allowed_runtimes": sorted(USER_RUNTIMES),
        "runtime_choices": _runtime_choices(),
        "model_backends": model_backends.collect_model_backends(),
    }
