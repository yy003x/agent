"""SkillManager:发现/注册/校验/路由技能;加载隔离(见 design/05 §1)。

内核只提供机制;技能实现是调用方内容(目录由 Config 注入)。坏技能不拖垮内核。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentrun.core.yaml_lite import load_yaml


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class SkillManager:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._errors: list[dict[str, str]] = []

    def register_dir(self, directory: str | Path) -> None:
        """扫描 *.skill.yaml;单个技能加载失败被隔离(记 errors,不抛)。"""
        d = Path(directory)
        if not d.is_dir():
            self._errors.append({"path": str(d), "error": "技能目录不存在"})
            return
        for path in sorted(d.glob("*.skill.yaml")):
            try:
                self._skills[self._load(path).name] = self._load(path)
            except Exception as exc:  # noqa: BLE001 加载隔离
                self._errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def list(self) -> list[Skill]:
        return list(self._skills.values())

    def doctor(self) -> dict[str, Any]:
        return {"ok": True, "loaded": len(self._skills), "errors": self._errors}

    def route(self, query: str) -> Skill | None:
        """默认关键词路由(策略可换)。"""
        q = query.lower()
        for skill in self._skills.values():
            if any(kw.lower() in q for kw in skill.keywords):
                return skill
        return None

    def _load(self, path: Path) -> Skill:
        raw = load_yaml(path) or {}
        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", "")).strip()
        if not name or not description:
            raise ValueError("技能必须含 name 与 description")
        keywords = [str(k) for k in (raw.get("keywords") or [])]
        return Skill(name=name, description=description, keywords=keywords, path=str(path), raw=raw)
