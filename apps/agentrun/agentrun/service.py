"""RuntimeService:lib 与 CLI 共用的唯一核心服务对象(无 web 假设,见 design/08)。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from agentrun import __version__
from agentrun.core.config import ConfigManager, Profile
from agentrun.core.contract import read_status, write_request, write_status
from agentrun.core.jsonio import read_json, read_jsonl, write_json_atomic
from agentrun.core.registry import Registry
from agentrun.core.rundir import run_paths
from agentrun.core.run import CANCELLED, PENDING, RUNNING, SESSION, TASK, RunRequest, check_contract_version, new_run_id, utc_now
from agentrun.providers.code_cli import CodeCliProvider
from agentrun.providers.llm_api import LlmApiProvider
from agentrun.providers.llm_api.protocols import get_protocol
from agentrun.providers.llm_api.provider import _expanded_headers
from agentrun.providers.tmux import TmuxProvider

_PROVIDERS = {
    "cli": CodeCliProvider,
    "api": LlmApiProvider,
    "tmux": TmuxProvider,
}


class RuntimeService:
    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.runtime_config = config.runtime_config()
        self.registry = Registry(self.runtime_config.runs_dir)

    # ---- 只读 ----
    def profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "id": p.id,
                "transport": p.transport,
                "label": p.label,
                "binary": p.binary,
                "result_contract": p.result_contract,
            }
            for p in self.config.profiles().values()
        ]

    def config_choices(self, *, project_id: str | None = None, only_valid: bool = True) -> dict[str, Any]:
        profiles = self.config.profiles(project_id=project_id)
        validation = self.validation_status().get("items", {})
        choices = []
        for profile in profiles.values():
            item = _choice_from_profile(profile, validation.get(_validation_key(profile), {}))
            if only_valid and not item["validated"]:
                continue
            choices.append(item)
        return {
            "ok": True,
            "only_valid": only_valid,
            "validation_status_file": str(self._validation_status_path()),
            "choices": sorted(choices, key=lambda item: (item["provider_type"], item["provider_name"], item["profile_name"])),
        }

    def validation_status(self) -> dict[str, Any]:
        path = self._validation_status_path()
        data = read_json(path, {}) or {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", 1)
        data.setdefault("items", {})
        return data

    def validate_config(
        self,
        *,
        provider_type: str | None = None,
        name: str | None = None,
        profile_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        profiles = [
            profile
            for profile in self.config.profiles(project_id=project_id).values()
            if _matches_profile(profile, provider_type=provider_type, name=name, profile_id=profile_id)
        ]
        status = self.validation_status()
        items = status.setdefault("items", {})
        results = []
        for profile in profiles:
            result = self._validate_profile(profile)
            items[_validation_key(profile)] = result
            results.append(result)
        status["updated_at"] = utc_now()
        write_json_atomic(self._validation_status_path(), status)
        return {
            "ok": bool(results) and all(item.get("ok") for item in results),
            "validated": len(results),
            "status_file": str(self._validation_status_path()),
            "results": results,
        }

    def doctor(self) -> dict[str, Any]:
        profiles = self.config.profiles()
        providers: dict[str, Any] = {}
        for p in profiles.values():
            providers[p.id] = {
                "transport": p.transport,
                "implemented": p.transport in _PROVIDERS,
                "binary_found": (shutil.which(p.binary) is not None) if p.binary else None,
            }
        return {
            "ok": True,
            "version": __version__,
            "runs_dir": str(self.runtime_config.runs_dir),
            "default_profile": self.runtime_config.default_profile,
            "profiles": len(profiles),
            "providers": providers,
        }

    def _validation_status_path(self) -> Path:
        return self.runtime_config.runs_dir / "runtime-validation" / "status.json"

    def _validate_profile(self, profile: Profile) -> dict[str, Any]:
        if profile.transport == "api":
            return _validate_api_profile(profile)
        if profile.transport == "cli":
            return _validate_cli_profile(profile)
        if profile.transport == "tmux":
            return _validate_tmux_profile(profile)
        return _validation_result(profile, False, f"未知 transport: {profile.transport}")

    # ---- 执行 ----
    def run_task(
        self,
        *,
        prompt_file: str | Path,
        provider_profile: str | None = None,
        project_id: str | None = None,
        result_schema: str = "",
        run_id: str | None = None,
        run_type: str = TASK,
        cwd: str | Path | None = None,
        deadline_seconds: int | None = None,
        allowed_actions: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        project = project_id or self.runtime_config.default_project
        profile = self._resolve_profile(provider_profile, project_id=project)
        prompt_path = Path(prompt_file).expanduser().resolve()
        if not prompt_path.is_file():
            raise ValueError(f"prompt_file 不存在: {prompt_path}")

        actual_run_id = run_id or new_run_id(run_type)
        if run_id and not force:
            existing = self._existing_summary(project, run_type, actual_run_id)
            if existing is not None:
                existing["idempotent"] = True
                return existing
        paths = run_paths(self.runtime_config.runs_dir, project, run_type, actual_run_id).ensure()
        request = RunRequest(
            run_type=run_type,
            run_id=actual_run_id,
            provider_profile=profile.id,
            provider=profile.transport,
            project_id=project,
            cwd=Path(cwd).expanduser().resolve() if cwd else Path.cwd(),
            prompt_file=prompt_path,
            result_file=paths.result_file,
            result_schema=result_schema,
            deadline_seconds=profile.timeout_seconds if deadline_seconds is None else deadline_seconds,
            allowed_actions=allowed_actions or [],
            forbidden_actions=forbidden_actions or [],
            runtime_version=__version__,
        )
        write_request(paths, request)
        write_status(paths, request, PENDING, message="queued")
        self.registry.register(
            actual_run_id,
            {"run_type": run_type, "project_id": project, "run_dir": str(paths.run_dir), "state": PENDING},
        )

        write_status(paths, request, RUNNING, message="running")
        self.registry.update(actual_run_id, state=RUNNING)
        provider = self._provider_for(profile)
        provider.run(request, paths)

        final = read_status(paths) or {}
        self.registry.update(actual_run_id, state=final.get("state"))
        return {
            "run_id": actual_run_id,
            "project_id": project,
            "state": final.get("state"),
            "failure_reason": final.get("failure_reason"),
            "result_file": str(paths.result_file),
        }

    def start_session(
        self,
        *,
        provider_profile: str | None = None,
        project_id: str | None = None,
        run_id: str | None = None,
        cwd: str | Path | None = None,
        allowed_actions: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        project = project_id or self.runtime_config.default_project
        profile = self._resolve_profile(provider_profile, project_id=project)
        if profile.transport != "tmux":
            raise ValueError(f"session 目前只支持 tmux profile,得到 {profile.transport}")
        actual_run_id = run_id or new_run_id(SESSION)
        if run_id and not force:
            existing = self._existing_summary(project, SESSION, actual_run_id)
            if existing is not None:
                existing["idempotent"] = True
                return existing
        paths = run_paths(self.runtime_config.runs_dir, project, SESSION, actual_run_id).ensure()
        request = RunRequest(
            run_type=SESSION,
            run_id=actual_run_id,
            provider_profile=profile.id,
            provider=profile.transport,
            project_id=project,
            cwd=Path(cwd).expanduser().resolve() if cwd else Path.cwd(),
            result_file=paths.result_file,
            allowed_actions=allowed_actions or [],
            forbidden_actions=forbidden_actions or [],
            runtime_version=__version__,
        )
        write_request(paths, request)
        write_status(paths, request, PENDING, message="queued")
        self.registry.register(
            actual_run_id,
            {"run_type": SESSION, "project_id": project, "run_dir": str(paths.run_dir), "state": PENDING},
        )
        provider = self._provider_for(profile)
        start = provider.start_session(request, paths)
        self.registry.update(actual_run_id, state=RUNNING)
        return {
            "run_id": actual_run_id,
            "project_id": project,
            "state": RUNNING,
            **start,
        }

    def task_status(self, run_id: str, project_id: str | None = None, run_type: str = TASK) -> dict[str, Any]:
        return self.status(run_id, project_id=project_id, run_type=run_type)

    def status(self, run_id: str, project_id: str | None = None, run_type: str = TASK) -> dict[str, Any]:
        project = project_id or self.runtime_config.default_project
        paths = run_paths(self.runtime_config.runs_dir, project, run_type, run_id)
        request = read_json(paths.request_file, None)
        if request is not None:
            check_contract_version(request)  # 契约版本不匹配时立即失败
        status = read_status(paths)
        if status is None:
            raise ValueError(f"run 不存在: {run_id}")
        if request and request.get("provider") == "tmux" and run_type == SESSION:
            try:
                provider = self._provider_for(self._resolve_profile(str(request.get("provider_profile") or ""), project_id=project))
                provider_status = provider.session_status(paths)
                status["provider_live_status"] = provider_status
                status["classification"] = "running" if provider_status.get("alive") else "orphaned"
            except Exception as exc:  # noqa: BLE001 status 不应因实时探测失败而不可读
                status["classification"] = self.registry.classify(run_id)
                status["provider_live_error"] = str(exc)
        else:
            status["classification"] = self.registry.classify(run_id)
        return status

    def logs(
        self,
        run_id: str,
        *,
        project_id: str | None = None,
        run_type: str = TASK,
        tail: int = 120,
    ) -> dict[str, Any]:
        project = project_id or self.runtime_config.default_project
        paths = run_paths(self.runtime_config.runs_dir, project, run_type, run_id)
        request = self._read_request(paths)
        if request and request.provider == "tmux":
            try:
                provider = self._provider_for(self._resolve_profile(request.provider_profile, project_id=request.project_id))
                live = provider.logs(paths, tail=tail)
                content = live.get("content", "")
            except Exception:
                content = _tail_text(paths.output_log, tail)
        else:
            content = _tail_text(paths.output_log, tail)
        events = read_jsonl(paths.events_file)[-tail:]
        return {"run_id": run_id, "content": content, "events": events}

    def send(
        self,
        run_id: str,
        text: str,
        *,
        project_id: str | None = None,
        run_type: str = SESSION,
        submit: bool = True,
    ) -> dict[str, Any]:
        provider, paths = self._tmux_provider_for(run_id, project_id=project_id, run_type=run_type)
        return provider.send(paths, text, submit=submit)

    def interrupt(self, run_id: str, *, project_id: str | None = None, run_type: str = SESSION) -> dict[str, Any]:
        provider, paths = self._tmux_provider_for(run_id, project_id=project_id, run_type=run_type)
        return provider.interrupt(paths)

    def stop(self, run_id: str, *, project_id: str | None = None, run_type: str = SESSION) -> dict[str, Any]:
        provider, paths = self._tmux_provider_for(run_id, project_id=project_id, run_type=run_type)
        out = provider.stop(paths)
        status = read_status(paths)
        if status and status.get("state") not in (CANCELLED, "done", "failed"):
            request = self._read_request(paths)
            if request:
                write_status(paths, request, CANCELLED, message="session stopped")
        self.registry.update(run_id, state=CANCELLED)
        return out

    def cancel(self, run_id: str, *, project_id: str | None = None, run_type: str = TASK) -> dict[str, Any]:
        project = project_id or self.runtime_config.default_project
        paths = run_paths(self.runtime_config.runs_dir, project, run_type, run_id)
        request = self._read_request(paths)
        if request is None:
            raise ValueError(f"run 不存在: {run_id}")
        if request.provider == "tmux":
            provider = self._provider_for(self._resolve_profile(request.provider_profile, project_id=request.project_id))
            if hasattr(provider, "cancel"):
                provider.cancel(paths)
        write_status(paths, request, CANCELLED, message="cancelled")
        self.registry.update(run_id, state=CANCELLED)
        return {"run_id": run_id, "state": CANCELLED}

    def prune(self, dry_run: bool = True) -> dict[str, Any]:
        return self.registry.prune(dry_run=dry_run)

    # ---- 内部 ----
    def _resolve_profile(self, profile_id: str | None, project_id: str | None = None) -> Profile:
        profiles = self.config.profiles(project_id=project_id)
        runtime_config = self.config.runtime_config(project_id=project_id)
        pid = profile_id or runtime_config.default_profile
        if pid not in profiles:
            raise ValueError(f"未知 provider profile: {pid}")
        return profiles[pid]

    def _provider_for(self, profile: Profile):
        cls = _PROVIDERS.get(profile.transport)
        if cls is None:
            raise NotImplementedError(f"transport {profile.transport} 将在 M1 接入")
        return cls(profile)

    def _existing_summary(self, project_id: str, run_type: str, run_id: str) -> dict[str, Any] | None:
        paths = run_paths(self.runtime_config.runs_dir, project_id, run_type, run_id)
        if not paths.status_file.exists():
            return None
        status = read_status(paths) or {}
        return {
            "run_id": run_id,
            "project_id": project_id,
            "state": status.get("state"),
            "failure_reason": status.get("failure_reason"),
            "result_file": str(paths.result_file),
        }

    def _read_request(self, paths):
        data = read_json(paths.request_file, None)
        if data is None:
            return None
        check_contract_version(data)
        return _request_from_dict(data)

    def _tmux_provider_for(self, run_id: str, *, project_id: str | None, run_type: str):
        project = project_id or self.runtime_config.default_project
        paths = run_paths(self.runtime_config.runs_dir, project, run_type, run_id)
        request = self._read_request(paths)
        if request is None:
            raise ValueError(f"run 不存在: {run_id}")
        if request.provider != "tmux":
            raise ValueError(f"{run_id} 不是 tmux run(provider={request.provider})")
        provider = self._provider_for(self._resolve_profile(request.provider_profile, project_id=request.project_id))
        return provider, paths


def _request_from_dict(data: dict[str, Any]) -> RunRequest:
    return RunRequest(
        run_type=str(data["run_type"]),
        run_id=str(data["run_id"]),
        provider_profile=str(data["provider_profile"]),
        provider=str(data.get("provider") or ""),
        project_id=str(data.get("project_id") or "_default"),
        caller=str(data.get("caller") or ""),
        cwd=Path(data["cwd"]) if data.get("cwd") else None,
        prompt_file=Path(data["prompt_file"]) if data.get("prompt_file") else None,
        deadline_seconds=int(data.get("deadline_seconds") or 0),
        result_file=Path(data["result_file"]) if data.get("result_file") else None,
        result_schema=str(data.get("result_schema") or ""),
        allowed_actions=[str(x) for x in data.get("allowed_actions") or []],
        forbidden_actions=[str(x) for x in data.get("forbidden_actions") or []],
        contract_version=int(data.get("contract_version") or 1),
        runtime_version=str(data.get("runtime_version") or ""),
        created_at=str(data.get("created_at") or ""),
    )


def _tail_text(path: Path, tail: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:]) + ("\n" if lines else "")


def _matches_profile(
    profile: Profile,
    *,
    provider_type: str | None,
    name: str | None,
    profile_id: str | None,
) -> bool:
    raw = profile.raw
    actual_provider_type = _normalized_provider_type(str(raw.get("provider_type") or profile.transport))
    if provider_type and actual_provider_type != _normalized_provider_type(provider_type):
        return False
    if name and raw.get("provider_name") != name:
        return False
    if profile_id and profile.id != profile_id:
        return False
    return True


def _validation_key(profile: Profile) -> str:
    return str(profile.raw.get("validation_key") or f"{profile.transport}/{profile.id}")


def _config_hash(profile: Profile) -> str:
    public_raw = {
        key: value
        for key, value in profile.raw.items()
        if key not in {"api_key", "secret", "password", "token"}
    }
    blob = json.dumps(
        {
            "id": profile.id,
            "transport": profile.transport,
            "binary": profile.binary,
            "default_args": profile.default_args,
            "timeout_seconds": profile.timeout_seconds,
            "result_contract": profile.result_contract,
            "raw": public_raw,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _choice_from_profile(profile: Profile, validation: dict[str, Any]) -> dict[str, Any]:
    config_hash = _config_hash(profile)
    validated = bool(validation.get("ok") and validation.get("config_hash") == config_hash)
    raw = profile.raw
    return {
        "id": profile.id,
        "profile": profile.id,
        "label": profile.label,
        "transport": profile.transport,
        "provider_type": _public_provider_type(profile),
        "provider_name": raw.get("provider_name") or profile.id,
        "profile_name": raw.get("profile_name") or "default",
        "validation_key": _validation_key(profile),
        "validated": validated,
        "validation": {
            "ok": bool(validation.get("ok")),
            "validated_at": validation.get("validated_at", ""),
            "message": validation.get("message", ""),
        },
        "detail": _public_profile_detail(profile),
    }


def _public_profile_detail(profile: Profile) -> dict[str, Any]:
    raw = profile.raw
    if profile.transport == "api":
        return {
            "protocol": raw.get("protocol"),
            "base_url": raw.get("base_url") or raw.get("host"),
            "model": raw.get("model"),
            "api_key_env": raw.get("api_key_env"),
            "headers": sorted((raw.get("headers") or {}).keys()) if isinstance(raw.get("headers"), dict) else [],
        }
    if profile.transport in {"cli", "tmux"}:
        return {
            "command": profile.binary,
            "args": list(profile.default_args),
        }
    return {}


def _validation_result(profile: Profile, ok: bool, message: str) -> dict[str, Any]:
    return {
        "key": _validation_key(profile),
        "profile": profile.id,
        "provider_type": _public_provider_type(profile),
        "provider_name": profile.raw.get("provider_name") or profile.id,
        "profile_name": profile.raw.get("profile_name") or "default",
        "ok": ok,
        "message": message,
        "config_hash": _config_hash(profile),
        "validated_at": utc_now(),
    }


def _validate_cli_profile(profile: Profile) -> dict[str, Any]:
    if not profile.binary:
        return _validation_result(profile, False, "缺少 command")
    if shutil.which(profile.binary) is None and not Path(profile.binary).expanduser().exists():
        return _validation_result(profile, False, f"命令不可用: {profile.binary}")
    return _validation_result(profile, True, "命令可用")


def _validate_tmux_profile(profile: Profile) -> dict[str, Any]:
    if shutil.which("tmux") is None:
        return _validation_result(profile, False, "tmux 不可用")
    cli = _validate_cli_profile(profile)
    if not cli["ok"]:
        return cli
    return _validation_result(profile, True, "tmux 和命令可用")


def _validate_api_profile(profile: Profile) -> dict[str, Any]:
    raw = profile.raw
    api_key_env = str(raw.get("api_key_env") or "")
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""
    if not api_key_env:
        return _validation_result(profile, False, "缺少 api_key_env")
    if not api_key:
        return _validation_result(profile, False, f"环境变量未设置: {api_key_env}")
    base_url = str(raw.get("base_url") or raw.get("host") or "").rstrip("/")
    if not base_url:
        return _validation_result(profile, False, "缺少 base_url")
    try:
        model = str(raw.get("model") or "")
        if not model:
            return _validation_result(profile, False, "缺少 model")
        protocol = get_protocol(str(raw.get("protocol") or "openai"))
        payload = protocol.build_payload(model, "请只回复 ok")
        headers = {**protocol.build_headers(api_key), **_expanded_headers(dict(raw.get("headers") or {}))}
        response = LlmApiProvider(profile)._post(base_url + protocol.ENDPOINT, payload, headers, profile.timeout_seconds or 120)
        text = protocol.extract_text(response)
    except Exception as exc:  # noqa: BLE001 验证边界要把失败原因写入状态,不抛密钥
        return _validation_result(profile, False, f"调用失败: {type(exc).__name__}")
    return _validation_result(profile, bool(str(text).strip()), "真实 API 调用成功")


def _normalized_provider_type(value: str) -> str:
    return value


def _public_provider_type(profile: Profile) -> str:
    return profile.transport
