#!/usr/bin/env python3
"""Safe file listing and preview helpers for the local workbench."""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

ALLOW_ROOTS = {
    "design",
    "rules",
    "skills",
    "memory",
    "workspace",
    "outputs",
    "runs",
    "apps",
    "scripts",
}

DENY_PARTS = {
    ".env",
    ".netrc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

DENY_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".cookie",
}

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".toml",
    ".yaml",
    ".yml",
    ".py",
    ".sh",
    ".html",
    ".css",
    ".js",
    ".log",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class FileAccessError(ValueError):
    pass


def _is_denied(path: Path) -> bool:
    lowered = [p.lower() for p in path.parts]
    name = path.name.lower()
    if name in DENY_PARTS or path.suffix.lower() in DENY_SUFFIXES:
        return True
    return any(part in {"token", "tokens", "secret", "secrets", "cookie", "cookies"} for part in lowered)


def safe_path(rel_path: str | None) -> Path:
    rel = (rel_path or "").strip().lstrip("/")
    if not rel:
        raise FileAccessError("path is required")
    top = rel.split("/", 1)[0]
    if top not in ALLOW_ROOTS:
        raise FileAccessError(f"path root is not allowed: {top}")
    path = (ROOT / rel).resolve()
    if ROOT not in path.parents and path != ROOT:
        raise FileAccessError("path escapes project root")
    if _is_denied(path):
        raise FileAccessError("sensitive path is denied")
    return path


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _entry(path: Path) -> dict:
    st = path.stat()
    return {
        "name": path.name,
        "path": rel(path),
        "kind": "dir" if path.is_dir() else "file",
        "size": st.st_size if path.is_file() else None,
        "mtime": st.st_mtime,
    }


def list_dir(rel_path: str = "outputs", limit: int = 300) -> dict:
    path = safe_path(rel_path)
    if not path.exists():
        return {"path": rel_path, "exists": False, "entries": []}
    if not path.is_dir():
        return {"path": rel(path), "exists": True, "entries": [_entry(path)]}
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if _is_denied(child):
            continue
        entries.append(_entry(child))
        if len(entries) >= limit:
            break
    return {"path": rel(path), "exists": True, "entries": entries}


def list_outputs(limit: int = 400) -> dict:
    outputs = ROOT / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    entries = []
    for dirpath, dirnames, filenames in os.walk(outputs):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _is_denied(current / d)]
        for filename in sorted(filenames):
            path = current / filename
            if _is_denied(path):
                continue
            entries.append(_entry(path))
            if len(entries) >= limit:
                return {"root": "outputs", "entries": entries}
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return {"root": "outputs", "entries": entries[:limit]}


def read_file(rel_path: str, max_bytes: int = 240_000) -> dict:
    path = safe_path(rel_path)
    if not path.exists():
        raise FileAccessError("path does not exist")
    if path.is_dir():
        return list_dir(rel(path))
    suffix = path.suffix.lower()
    mime, _ = mimetypes.guess_type(path.name)
    size = path.stat().st_size
    if suffix in IMAGE_SUFFIXES:
        data = path.read_bytes()
        return {
            "path": rel(path),
            "kind": "image",
            "mime": mime or "application/octet-stream",
            "size": size,
            "data_url": f"data:{mime or 'application/octet-stream'};base64,{base64.b64encode(data).decode()}",
        }
    if suffix in TEXT_SUFFIXES or size <= max_bytes:
        data = path.read_bytes()[:max_bytes]
        return {
            "path": rel(path),
            "kind": "text",
            "mime": mime or "text/plain",
            "size": size,
            "truncated": size > max_bytes,
            "text": data.decode("utf-8", errors="replace"),
        }
    return {
        "path": rel(path),
        "kind": "binary",
        "mime": mime or "application/octet-stream",
        "size": size,
        "message": "binary preview is not supported",
    }
