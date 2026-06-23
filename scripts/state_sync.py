#!/usr/bin/env python3
"""Export, import, and verify ignored local state for multi-device use."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "state-sync.example.json"
DEFAULT_OUT_DIR = ROOT / "runs" / "state-sync"
MANIFEST_NAME = "manifest.json"
PACKAGE_PREFIX = "agent-state"

BUILTIN_INCLUDE = [
    {"path": "workspace/daily", "required": False, "note": "session 摘要和复盘事实"},
    {"path": "workspace/resume", "required": False, "note": "未完成任务恢复点"},
    {"path": "workspace/media-inbox", "required": False, "note": "待整理素材"},
    {"path": "workspace/media-store", "required": False, "note": "已整理素材"},
    {"path": "workspace/kb/lance", "required": False, "note": "当前本地知识库事实源"},
    {"path": "outputs", "required": False, "note": "草稿、成品包和临时交付物"},
    {"path": "runs/workbench/config.json", "required": False, "note": "工作台 UI 本地偏好"},
]

BUILTIN_EXCLUDE = [
    ".env",
    ".venv",
    "venv",
    "__pycache__",
    "*.pyc",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.cookie",
    ".DS_Store",
    "runs/tmux",
    "runs/shared-runtime",
    "runs/workbench/sessions",
    "runs/workbench/server.pid",
    "runs/workbench/server.log",
    "runs/workbench-smoke",
    "runs/model-backend-smoke",
    "runs/tmp-lancedb-inspect",
    "runs/tmux-detector-smoke",
]


@dataclass(frozen=True)
class IncludeEntry:
    path: str
    required: bool = False
    note: str = ""


@dataclass(frozen=True)
class FileEntry:
    rel_path: str
    size: int
    sha256: str


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load_config(path: Path) -> tuple[list[IncludeEntry], list[str], dict]:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif path == DEFAULT_CONFIG:
        raw = {"version": 1, "include": BUILTIN_INCLUDE, "exclude": BUILTIN_EXCLUDE}
    else:
        raise FileNotFoundError(f"配置不存在：{path}")

    include: list[IncludeEntry] = []
    for item in raw.get("include", []):
        if isinstance(item, str):
            include.append(IncludeEntry(path=item))
        elif isinstance(item, dict) and item.get("path"):
            include.append(
                IncludeEntry(
                    path=str(item["path"]),
                    required=bool(item.get("required", False)),
                    note=str(item.get("note", "")),
                )
            )
        else:
            raise ValueError(f"非法 include 项：{item!r}")
    exclude = [str(item) for item in raw.get("exclude", BUILTIN_EXCLUDE)]
    return include, exclude, raw


def normalize_rel(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"路径必须是项目内相对路径：{value}")
    normalized = path.as_posix().strip("/")
    if not normalized:
        raise ValueError("路径不能为空")
    return normalized


def is_excluded(rel_path: str, excludes: Iterable[str]) -> bool:
    path = PurePosixPath(rel_path)
    parts = set(path.parts)
    for raw_pattern in excludes:
        pattern = raw_pattern.strip().strip("/")
        if not pattern:
            continue
        if pattern in parts or rel_path == pattern or rel_path.startswith(pattern + "/"):
            return True
        if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files(include: list[IncludeEntry], excludes: list[str]) -> tuple[list[FileEntry], list[str]]:
    files: list[FileEntry] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for entry in include:
        rel_entry = normalize_rel(entry.path)
        root = ROOT / rel_entry
        if not root.exists():
            message = f"缺失：{rel_entry}"
            if entry.required:
                raise FileNotFoundError(message)
            warnings.append(message)
            continue

        candidates = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in candidates:
            rel_path = rel(path)
            if is_excluded(rel_path, excludes):
                continue
            if rel_path in seen:
                continue
            seen.add(rel_path)
            files.append(FileEntry(rel_path=rel_path, size=path.stat().st_size, sha256=sha256_file(path)))
    return files, warnings


def human_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{size}B"


def git_commit() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        import subprocess

        proc = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def build_manifest(config_path: Path, files: list[FileEntry], warnings: list[str]) -> dict:
    total_size = sum(item.size for item in files)
    return {
        "version": 1,
        "kind": "agent-state-package",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(ROOT),
        "git_commit": git_commit(),
        "config": rel(config_path) if config_path.exists() else "",
        "file_count": len(files),
        "total_size": total_size,
        "warnings": warnings,
        "files": [item.__dict__ for item in files],
    }


def print_plan(files: list[FileEntry], warnings: list[str], *, limit: int) -> None:
    total_size = sum(item.size for item in files)
    print(f"待同步文件：{len(files)} 个，总大小：{human_size(total_size)}")
    for warning in warnings:
        print(f"  ! {warning}")
    for item in files[:limit]:
        print(f"  + {item.rel_path}  {human_size(item.size)}")
    if len(files) > limit:
        print(f"  ... 还有 {len(files) - limit} 个文件，使用 --limit 调整展示数量")


def cmd_plan(args: argparse.Namespace) -> int:
    include, excludes, _raw = load_config(Path(args.config))
    files, warnings = iter_files(include, excludes)
    print_plan(files, warnings, limit=args.limit)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    include, excludes, _raw = load_config(config_path)
    files, warnings = iter_files(include, excludes)
    if args.dry_run:
        print_plan(files, warnings, limit=args.limit)
        print("dry-run：未写出归档")
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = Path(args.archive) if args.archive else out_dir / f"{stamp}-{PACKAGE_PREFIX}.tar.gz"
    manifest = build_manifest(config_path, files, warnings)

    with tempfile.TemporaryDirectory(prefix="agent-state-manifest-") as tmp:
        manifest_path = Path(tmp) / MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(manifest_path, arcname=MANIFEST_NAME)
            for item in files:
                tar.add(ROOT / item.rel_path, arcname=item.rel_path, recursive=False)

    print(f"已导出：{archive}")
    print(f"文件数：{len(files)}，总大小：{human_size(sum(item.size for item in files))}")
    if warnings:
        print("提示：")
        for warning in warnings:
            print(f"  ! {warning}")
    return 0


def safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        name = member.name.strip("/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not name:
            raise ValueError(f"归档包含非法路径：{member.name}")
        if member.isdir() or member.isfile():
            members.append(member)
        else:
            raise ValueError(f"归档包含不支持的文件类型：{member.name}")
    return members


def read_manifest(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(MANIFEST_NAME)
        except KeyError as exc:
            raise ValueError(f"归档缺少 {MANIFEST_NAME}") from exc
        extracted = tar.extractfile(member)
        if extracted is None:
            raise ValueError(f"无法读取 {MANIFEST_NAME}")
        return json.loads(extracted.read().decode("utf-8"))


def cmd_import(args: argparse.Namespace) -> int:
    archive = Path(args.archive)
    if not archive.exists():
        raise FileNotFoundError(f"归档不存在：{archive}")

    manifest = read_manifest(archive)
    if manifest.get("kind") != "agent-state-package":
        raise ValueError("归档类型不是 agent-state-package")

    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in safe_members(tar) if m.name != MANIFEST_NAME and m.isfile()]
        conflicts: list[str] = []
        writes: list[str] = []
        skips: list[str] = []
        for member in members:
            dest = ROOT / member.name
            if dest.exists() and not args.overwrite:
                conflicts.append(member.name)
                skips.append(member.name)
            else:
                writes.append(member.name)

        print(f"归档文件：{archive}")
        print(f"源目录：{manifest.get('source_root', '')}")
        print(f"源 commit：{manifest.get('git_commit', '')}")
        print(f"待写入：{len(writes)}，跳过：{len(skips)}")
        if conflicts:
            print("已有文件冲突，默认跳过；如需覆盖请加 --overwrite：")
            for item in conflicts[: args.limit]:
                print(f"  ! {item}")
            if len(conflicts) > args.limit:
                print(f"  ... 还有 {len(conflicts) - args.limit} 个冲突")

        if args.dry_run:
            print("dry-run：未写入文件")
            return 0

        for member in members:
            if member.name in skips:
                continue
            dest = ROOT / member.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with dest.open("wb") as f:
                shutil.copyfileobj(extracted, f)

    print("导入完成")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    include, excludes, _raw = load_config(Path(args.config))
    files, warnings = iter_files(include, excludes)
    errors: list[str] = []

    for item in [".env", ".venv", "venv"]:
        if (ROOT / item).exists():
            print(f"本机存在 {item}：正常，本脚本不会导出它")

    if not (ROOT / "requirements.txt").exists():
        errors.append("缺少 requirements.txt")
    if not (ROOT / ".env.example").exists():
        errors.append("缺少 .env.example")
    if not (ROOT / "workspace" / "kb").exists():
        errors.append("缺少 workspace/kb")

    print_plan(files, warnings, limit=args.limit)
    if errors:
        print("校验失败：")
        for error in errors:
            print(f"  ✗ {error}")
        return 1
    print("校验通过：同步清单可用")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="多电脑本地状态同步工具")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="同步清单 JSON，默认 config/state-sync.example.json",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="预览将导出的文件")
    plan.add_argument("--limit", type=int, default=80, help="最多展示多少个文件")
    plan.set_defaults(func=cmd_plan)

    export = sub.add_parser("export", help="导出本地状态归档")
    export.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="默认输出目录")
    export.add_argument("--archive", help="指定输出 tar.gz 路径")
    export.add_argument("--dry-run", action="store_true", help="只预览，不写归档")
    export.add_argument("--limit", type=int, default=80, help="dry-run 展示文件数量")
    export.set_defaults(func=cmd_export)

    import_cmd = sub.add_parser("import", help="导入本地状态归档")
    import_cmd.add_argument("--archive", required=True, help="state package tar.gz 路径")
    import_cmd.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    import_cmd.add_argument("--overwrite", action="store_true", help="覆盖目标电脑已有文件")
    import_cmd.add_argument("--limit", type=int, default=80, help="最多展示多少个冲突")
    import_cmd.set_defaults(func=cmd_import)

    verify = sub.add_parser("verify", help="校验当前项目同步状态")
    verify.add_argument("--limit", type=int, default=80, help="最多展示多少个文件")
    verify.set_defaults(func=cmd_verify)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
