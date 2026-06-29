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
    choices = _runtime_choices()
    tmux_default = _first_profile(choices, "tmux", "tmux-codex")
    runtime_default = _first_profile(choices, MAIN_RUNTIME.default_runtime(), effective["provider_profiles"].get(MAIN_RUNTIME.default_runtime(), {}).get("profile", ""))
    return {
        "chat_provider": valid_user_runtime(CHAT_RUNTIME, "tmux"),
        "runtime_provider": valid_user_runtime(MAIN_RUNTIME.default_runtime(), "tmux"),
        "chat_profile": tmux_default,
        "runtime_profile": runtime_default,
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
    choices = _runtime_choices()
    config["chat_provider"] = _valid_runtime_for_choices(
        choices,
        str(config.get("chat_provider", defaults["chat_provider"])),
        defaults["chat_provider"],
    )
    config["runtime_provider"] = _valid_runtime_for_choices(
        choices,
        str(config.get("runtime_provider", defaults["runtime_provider"])),
        defaults["runtime_provider"],
    )
    config["chat_profile"] = _valid_profile_for_runtime(
        choices,
        config["chat_provider"],
        str(config.get("chat_profile") or defaults["chat_profile"]),
        defaults["chat_profile"],
    )
    config["runtime_profile"] = _valid_profile_for_runtime(
        choices,
        config["runtime_provider"],
        str(config.get("runtime_profile") or defaults["runtime_profile"]),
        defaults["runtime_profile"],
    )
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
    validation = _validate_selected_profiles(config)
    return runtime_config_payload(config, validation=validation)


def _runtime_options_from_config(config: dict) -> dict:
    return {
        "chat_profile": config["chat_profile"],
        "runtime_profile": config["runtime_profile"],
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
    if runtime == "cli" and config.get("runtime_profile") == "codex-cli":
        return config["codex_command"]
    if runtime == "cli" and config.get("runtime_profile") == "claude-cli":
        return config["claude_command"]
    return None


def _runtime_choices() -> list[dict]:
    payload = MAIN_RUNTIME.runtime_choices(only_valid=True)
    choices = payload.get("choices") if isinstance(payload, dict) else []
    return choices if isinstance(choices, list) else []


def _runtime_choices_all() -> list[dict]:
    payload = MAIN_RUNTIME.runtime_choices(only_valid=False)
    choices = payload.get("choices") if isinstance(payload, dict) else []
    return choices if isinstance(choices, list) else []


def _first_profile(choices: list[dict], runtime: str, default_value: str) -> str:
    for choice in choices:
        if choice.get("provider_type") == runtime or choice.get("transport") == runtime:
            return str(choice.get("profile") or choice.get("id") or default_value)
    return default_value


def _valid_runtime_for_choices(choices: list[dict], value: str, default_value: str) -> str:
    runtime = valid_user_runtime(value, default_value)
    if not choices:
        return runtime
    if any(choice.get("provider_type") == runtime or choice.get("transport") == runtime for choice in choices):
        return runtime
    first = choices[0]
    return str(first.get("provider_type") or first.get("transport") or runtime)


def _valid_profile_for_runtime(choices: list[dict], runtime: str, value: str, default_value: str) -> str:
    allowed = {
        str(choice.get("profile") or choice.get("id"))
        for choice in choices
        if choice.get("provider_type") == runtime or choice.get("transport") == runtime
    }
    if value in allowed:
        return value
    return _first_profile(choices, runtime, default_value)


def _validate_selected_profiles(config: dict) -> dict:
    results = []
    seen: set[str] = set()
    for profile in (config.get("chat_profile"), config.get("runtime_profile")):
        value = str(profile or "")
        if not value or value in seen:
            continue
        seen.add(value)
        results.append(MAIN_RUNTIME.validate_config(profile_id=value))
    return {"ok": all(item.get("ok") for item in results) if results else True, "results": results}


def validate_runtime_config(data: dict | None = None) -> dict:
    provider_type = str((data or {}).get("provider_type") or "") or None
    name = str((data or {}).get("name") or "") or None
    profile_id = str((data or {}).get("profile") or (data or {}).get("profile_id") or "") or None
    validation = MAIN_RUNTIME.validate_config(provider_type=provider_type, name=name, profile_id=profile_id)
    return runtime_config_payload(validation=validation)


def runtime_config_payload(config: dict | None = None, *, validation: dict | None = None) -> dict:
    config = config or workbench_config()
    options = _runtime_options_from_config(config)
    return {
        "config": config,
        "effective": MAIN_RUNTIME.effective_runtime_config(options),
        "config_path": str(CONFIG_PATH),
        "allowed_runtimes": sorted(USER_RUNTIMES),
        "runtime_choices": _runtime_choices(),
        "runtime_choices_all": _runtime_choices_all(),
        "validation": validation or {},
        "model_backends": model_backends.collect_model_backends(),
    }
