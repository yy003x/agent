"""ConfigManager:项目配置 + 调用方覆盖 + fail-fast(见 design/06 A)。

合并顺序:config/agentrun ← AGENTRUN_CONF_DIR/当前目录 conf ← project overlay ← run 参数。
provider 配置优先使用配置根目录下 api.yaml/cli.yaml/tmux.yaml,同时读取 providers/ 子目录以覆盖既有调用方。
本模块负责静态配置;env 注入在 provider 层执行。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentrun.core.yaml_lite import load_yaml

VALID_TRANSPORTS = ("api", "cli", "tmux")
VALID_RESULT_CONTRACTS = ("none", "optional", "required")
FIXED_PROVIDER_FILES = ("api.yaml", "cli.yaml", "tmux.yaml")


class ConfigError(ValueError):
    """配置错(启动即 fail-fast)。"""


@dataclass(frozen=True)
class RuntimeConfig:
    runs_dir: Path
    default_project: str
    default_profile: str
    max_concurrency: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class Profile:
    id: str
    transport: str
    label: str
    binary: str
    default_args: list[str]
    timeout_seconds: int
    result_contract: str
    raw: dict[str, Any] = field(default_factory=dict)


class ConfigManager:
    def __init__(self, conf_dir: str | Path | None = None, runs_dir: str | Path | None = None) -> None:
        self._config_dirs = _resolve_config_dirs(conf_dir)
        self._runs_dir_override = Path(runs_dir).expanduser() if runs_dir else None

    def _merge_yaml(self, name: str, project_id: str | None = None) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for directory in self._config_dirs:
            path = directory / name
            if path.exists():
                _deep_merge(merged, load_yaml(path) or {})
        if project_id:
            for overlay in self._project_overlay_paths(project_id):
                if overlay.exists():
                    _deep_merge(merged, load_yaml(overlay) or {})
        return merged

    def runtime_config(self, project_id: str | None = None) -> RuntimeConfig:
        raw = self._merge_yaml("runtime.yaml", project_id=project_id)
        runs_dir = self._runs_dir_override or Path(str(raw.get("runs_dir", "runs"))).expanduser()
        return RuntimeConfig(
            runs_dir=runs_dir.resolve(),
            default_project=str(raw.get("default_project", "_default")),
            default_profile=str(raw.get("default_profile", "codex-cli")),
            max_concurrency=int(raw.get("max_concurrency", 1) or 1),
            raw=raw,
        )

    def profiles(self, project_id: str | None = None) -> dict[str, Profile]:
        out: dict[str, Profile] = {}
        for name in FIXED_PROVIDER_FILES:
            raw = self._merge_provider_yaml(name)
            self._apply_fixed_provider_file(name, raw, out)
        if project_id:
            for overlay in self._project_overlay_paths(project_id):
                if not overlay.exists():
                    continue
                raw = self._load_profiles_file(overlay, out, optional_profiles=True)
                self._apply_simplified_profiles(raw, out)
        if not out:
            raise ConfigError("未加载到任何 profile(检查 config/agentrun/api.yaml|cli.yaml|tmux.yaml)")
        return out

    def _merge_provider_yaml(self, name: str) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for path in self._provider_file_candidates(name):
            if path.exists():
                _deep_merge(merged, load_yaml(path) or {})
        return merged

    def _provider_file_candidates(self, name: str) -> list[Path]:
        candidates: list[Path] = []
        for directory in self._config_dirs:
            candidates.append(directory / "providers" / name)
            candidates.append(directory / name)
        return candidates

    def _project_overlay_paths(self, project_id: str) -> list[Path]:
        return [directory / "projects" / f"{project_id}.runtime.yaml" for directory in self._config_dirs]

    def _load_profiles_file(self, path: Path, out: dict[str, Profile], *, optional_profiles: bool = False) -> dict[str, Any]:
        raw = load_yaml(path) or {}
        items = raw.get("profiles", [])
        if items in (None, []) and optional_profiles:
            return raw
        if not isinstance(items, list):
            raise ConfigError(f"{path}: profiles 必须是列表")
        for item in items:
            if not isinstance(item, dict):
                raise ConfigError(f"{path}: profile 项必须是 mapping")
            profile = self._build_profile(item, path)
            out[profile.id] = profile
        return raw

    def _build_profile(self, item: dict[str, Any], path: Path) -> Profile:
        pid = str(item.get("id", "")).strip()
        transport = str(item.get("transport", "")).strip()
        if not pid or not transport:
            raise ConfigError(f"{path}: profile 必须含 id 与 transport")
        if transport not in VALID_TRANSPORTS:
            raise ConfigError(f"{path}: 未知 transport {transport!r}(profile {pid})")
        result_contract = str(item.get("result_contract", "required"))
        if result_contract not in VALID_RESULT_CONTRACTS:
            raise ConfigError(f"{path}: 非法 result_contract {result_contract!r}(profile {pid})")
        return Profile(
            id=pid,
            transport=transport,
            label=str(item.get("label", pid)),
            binary=str(item.get("binary", "")),
            default_args=[str(a) for a in (item.get("default_args") or [])],
            timeout_seconds=int(item.get("timeout_seconds", 0) or 0),
            result_contract=result_contract,
            raw=item,
        )

    def _apply_fixed_provider_file(self, name: str, raw: dict[str, Any], out: dict[str, Profile]) -> None:
        if not isinstance(raw, dict):
            return
        stem = Path(name).stem
        if stem == "api":
            self._apply_api(raw.get("api", raw), out)
        elif stem == "cli":
            self._apply_cli(raw.get("cli", raw), out)
        elif stem == "tmux":
            self._apply_tmux(raw.get("tmux", raw), out)

    def _apply_simplified_profiles(self, raw: dict[str, Any], out: dict[str, Profile]) -> None:
        self._apply_api(raw.get("api"), out)
        self._apply_cli(raw.get("cli"), out)
        self._apply_tmux(raw.get("tmux"), out)

    def _apply_api(self, section: Any, out: dict[str, Profile]) -> None:
        direct_keys = {
            "profile",
            "id",
            "protocol",
            "base_url",
            "host",
            "model",
            "models",
            "api_key_env",
            "key_env",
            "headers",
        }
        for provider_name, provider_item in _section_items(section, direct_keys=direct_keys):
            for profile_name, item in _api_profile_items(provider_item):
                suffix = provider_name if profile_name == "default" else f"{provider_name}-{profile_name}"
                pid = _profile_id(item, default=f"api-{_slug(suffix)}")
                base = out.get(pid)
                label = str(item.get("label") or item.get("model") or suffix)
                raw = _profile_raw(base, pid=pid, transport="api", label=label)
                raw["provider_type"] = "api"
                raw["provider_name"] = str(provider_name)
                raw["profile_name"] = str(profile_name)
                for src, dst in (
                    ("protocol", "protocol"),
                    ("base_url", "base_url"),
                    ("host", "base_url"),
                    ("model", "model"),
                    ("api_key_env", "api_key_env"),
                    ("key_env", "api_key_env"),
                    ("headers", "headers"),
                    ("timeout_seconds", "timeout_seconds"),
                    ("result_contract", "result_contract"),
                    ("mock", "mock"),
                ):
                    if src in item:
                        raw[dst] = item[src]
                out[pid] = _profile_from_raw(raw, base=base)

    def _apply_cli(self, section: Any, out: dict[str, Profile]) -> None:
        for name, item in _section_items(section):
            pid = _profile_id(item, default=f"{name}-cli")
            base = out.get(pid)
            raw = _profile_raw(base, pid=pid, transport="cli", label=f"{name} CLI")
            raw["provider_type"] = "cli"
            raw["provider_name"] = str(name)
            raw["profile_name"] = str(item.get("profile_name") or "default")
            _apply_command_fields(raw, item, default_binary=name)
            out[pid] = _profile_from_raw(raw, base=base)

    def _apply_tmux(self, section: Any, out: dict[str, Profile]) -> None:
        if not isinstance(section, dict):
            return
        defaults = dict(section.get("defaults") or {})
        for key in ("session_name", "tmux_session_name"):
            if key in section:
                defaults[key] = section[key]
        entries = {
            key: value
            for key, value in section.items()
            if isinstance(value, dict) and key not in {"defaults"}
        }
        if defaults and not entries:
            entries = {
                pid.removeprefix("tmux-"): {}
                for pid, profile in out.items()
                if profile.transport == "tmux"
            }
        for name, item in entries.items():
            merged_item = {**defaults, **item}
            pid = _profile_id(item, default=f"tmux-{name}")
            base = out.get(pid)
            raw = _profile_raw(base, pid=pid, transport="tmux", label=f"Tmux {name}")
            raw["provider_type"] = "tmux"
            raw["provider_name"] = str(name)
            raw["profile_name"] = str(item.get("profile_name") or "default")
            _apply_command_fields(raw, merged_item, default_binary=name)
            out[pid] = _profile_from_raw(raw, base=base)


def _resolve_config_dirs(conf_dir: str | Path | None) -> list[Path]:
    dirs: list[Path] = []
    for directory in _project_config_candidates():
        _append_dir(dirs, directory)
    if conf_dir:
        _append_dir(dirs, Path(conf_dir).expanduser())
        return dirs
    env_dir = os.environ.get("AGENTRUN_CONF_DIR")
    if env_dir:
        _append_dir(dirs, Path(env_dir).expanduser())
        return dirs
    cwd_conf = Path.cwd() / "conf"
    if cwd_conf.is_dir():
        _append_dir(dirs, cwd_conf)
    return dirs


def _project_config_candidates() -> list[Path]:
    starts = [Path.cwd(), Path(__file__).resolve()]
    candidates: list[Path] = []
    for start in starts:
        path = start if start.is_dir() else start.parent
        for parent in (path, *path.parents):
            candidate = parent / "config" / "agentrun"
            if candidate.is_dir():
                candidates.append(candidate)
                break
    return candidates


def _append_dir(dirs: list[Path], directory: Path) -> None:
    resolved = directory.expanduser().resolve()
    if resolved not in dirs:
        dirs.append(resolved)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def _section_items(section: Any, *, direct_keys: set[str] | None = None) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(section, dict):
        return []
    if direct_keys and any(key in section for key in direct_keys):
        return [("default", section)]
    return [(str(k), v) for k, v in section.items() if isinstance(v, dict)]


def _profile_id(item: dict[str, Any], *, default: str) -> str:
    return str(item.get("profile") or item.get("id") or default)


def _profile_raw(base: Profile | None, *, pid: str, transport: str, label: str) -> dict[str, Any]:
    raw = dict(base.raw) if base else {}
    raw.setdefault("id", pid)
    raw.setdefault("transport", transport)
    raw.setdefault("label", base.label if base else label)
    return raw


def _profile_from_raw(raw: dict[str, Any], *, base: Profile | None) -> Profile:
    result_contract = str(raw.get("result_contract", base.result_contract if base else "required"))
    if result_contract not in VALID_RESULT_CONTRACTS:
        raise ConfigError(f"非法 result_contract {result_contract!r}(profile {raw.get('id')})")
    transport = str(raw.get("transport", base.transport if base else ""))
    if transport not in VALID_TRANSPORTS:
        raise ConfigError(f"未知 transport {transport!r}(profile {raw.get('id')})")
    args = raw.get("default_args")
    if args is None and base is not None:
        args = base.default_args
    return Profile(
        id=str(raw.get("id", base.id if base else "")),
        transport=transport,
        label=str(raw.get("label", base.label if base else raw.get("id", ""))),
        binary=str(raw.get("binary", base.binary if base else "")),
        default_args=[str(a) for a in (args or [])],
        timeout_seconds=int(raw.get("timeout_seconds", base.timeout_seconds if base else 0) or 0),
        result_contract=result_contract,
        raw=raw,
    )


def _apply_command_fields(raw: dict[str, Any], item: dict[str, Any], *, default_binary: str) -> None:
    if "label" in item:
        raw["label"] = str(item["label"])
    raw["binary"] = str(item.get("command") or item.get("binary") or raw.get("binary") or default_binary)
    if "args" in item:
        raw["default_args"] = [str(a) for a in (item.get("args") or [])]
    elif "default_args" in item:
        raw["default_args"] = [str(a) for a in (item.get("default_args") or [])]
    if "env" in item:
        raw["env"] = _string_map(item.get("env"))
    for key in ("env_passthrough", "env_allowlist"):
        if key in item:
            raw[key] = [str(a) for a in (item.get(key) or [])]
    if "timeout_seconds" in item:
        raw["timeout_seconds"] = int(item.get("timeout_seconds") or 0)
    if "result_contract" in item:
        raw["result_contract"] = str(item.get("result_contract") or "required")
    if "session_name" in item:
        raw["tmux_session_name"] = str(item["session_name"])
    if "tmux_session_name" in item:
        raw["tmux_session_name"] = str(item["tmux_session_name"])
    for key, value in item.items():
        if key not in raw and key not in {"command", "binary", "args", "default_args"}:
            raw[key] = value


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _api_profile_items(provider_item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    common = {key: value for key, value in provider_item.items() if key != "models"}
    models = provider_item.get("models")
    if not isinstance(models, dict) or not models:
        name = str(common.get("profile_name") or common.get("model") or "default")
        return [(name, common)]
    out: list[tuple[str, dict[str, Any]]] = []
    for name, value in models.items():
        item = dict(common)
        if isinstance(value, dict):
            item.update(value)
        else:
            item["model"] = value
        item.setdefault("model", str(name))
        out.append((str(name), item))
    return out


def _slug(value: str) -> str:
    return "-".join(part for part in str(value).replace("_", "-").split("/") if part).strip("-")
