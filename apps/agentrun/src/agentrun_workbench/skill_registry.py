"""Filesystem-backed registry for project skills."""
from __future__ import annotations

import re
from pathlib import Path


class SkillRegistry:
    """Read project skills from ``skills/*/SKILL.md``."""

    def __init__(self, skills_root: str | Path | None = None, project_root: str | Path | None = None) -> None:
        self.skills_root = Path(skills_root).resolve() if skills_root else None
        self.project_root = Path(project_root).resolve() if project_root else (
            self.skills_root.parent if self.skills_root else None
        )
        self._skills: dict[str, dict] = {}

    def register(self, name: str, metadata: dict | None = None) -> None:
        self._skills[name] = metadata or {}

    def list(self) -> list[dict]:
        discovered = self.discover()
        manual = [{"name": name, **metadata} for name, metadata in sorted(self._skills.items())]
        seen = {item["name"] for item in discovered}
        return discovered + [item for item in manual if item["name"] not in seen]

    def discover(self) -> list[dict]:
        if not self.skills_root or not self.skills_root.exists():
            return []
        skills = []
        for path in sorted(self.skills_root.iterdir(), key=lambda item: item.name):
            skill_file = path / "SKILL.md"
            if path.is_dir() and skill_file.exists():
                skills.append(self._read_skill(path.name, skill_file))
        return skills

    def _read_skill(self, name: str, skill_file: Path) -> dict:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        scripts = []
        scripts_dir = skill_file.parent / "scripts"
        if scripts_dir.exists():
            scripts = [
                self._relative(item)
                for item in sorted(scripts_dir.iterdir(), key=lambda value: value.name)
                if item.is_file()
            ]
        return {
            "name": name,
            "title": _first_heading(text) or name,
            "category": _category(text),
            "trigger": _section_summary(text, ["触发条件", "类别与触发"]),
            "capabilities": _step_titles(text),
            "commands": _python_commands(text),
            "skill_file": self._relative(skill_file),
            "scripts": scripts,
            "status": "available",
        }

    def _relative(self, path: Path) -> str:
        if not self.project_root:
            return str(path)
        try:
            return str(path.resolve().relative_to(self.project_root))
        except ValueError:
            return str(path)


def _first_heading(text: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _category(text: str) -> str:
    if "收尾类 skill" in text or "收尾类" in text:
        return "收尾类"
    if "处理类 skill" in text or "处理类" in text:
        return "处理类"
    if "## 触发条件" in text and "## 执行流程" in text:
        return "处理类"
    return "未分类"


def _section_summary(text: str, headings: list[str], max_lines: int = 4) -> str:
    for heading in headings:
        pattern = rf"^##\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s+|\Z)"
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            continue
        lines = []
        in_code = False
        for raw_line in match.group(1).splitlines():
            line = raw_line.strip()
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code or not line or line == "---":
                continue
            lines.append(line.lstrip("- ").strip())
            if len(lines) >= max_lines:
                break
        return " ".join(lines)
    return ""


def _step_titles(text: str) -> list[str]:
    titles = []
    for match in re.finditer(r"^###\s+(?:步骤\s*\d+[^：:]*[：:]\s*)?(.+?)\s*$", text, re.MULTILINE):
        title = match.group(1).strip()
        if title and title not in titles:
            titles.append(title)
    return titles


def _python_commands(text: str) -> list[str]:
    commands = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("python ", "python3 ")):
            commands.append(stripped.rstrip(" \\"))
    return commands
