"""轻量契约 schema 校验(见 design/06 C)。

只覆盖当前公共契约需要的 required / enum / type,避免引入运行时依赖。
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from agentrun.core.yaml_lite import load_yaml, loads


class SchemaError(ValueError):
    """schema 不匹配或 schema 文件不可用。"""


def validate_contract(data: dict[str, Any], schema_ref: str | Path | None = None) -> None:
    schema = _load_schema(schema_ref or "result")
    for key in schema.get("required") or []:
        if key not in data:
            raise SchemaError(f"缺少必填字段: {key}")
    properties = schema.get("properties") or {}
    for key, rules in properties.items():
        if key not in data or not isinstance(rules, dict):
            continue
        if "enum" in rules and data[key] not in rules["enum"]:
            raise SchemaError(f"{key} 不在允许枚举内: {data[key]!r}")
        if "type" in rules and not _matches_type(data[key], str(rules["type"])):
            raise SchemaError(f"{key} 类型不匹配: 期望 {rules['type']}")


def _load_schema(schema_ref: str | Path) -> dict[str, Any]:
    ref = str(schema_ref)
    candidate = Path(ref).expanduser()
    if candidate.is_file():
        schema = load_yaml(candidate)
    else:
        name = _builtin_name(ref)
        path = resources.files("agentrun") / "schemas" / name
        schema = loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise SchemaError(f"schema 必须是 mapping: {schema_ref}")
    return schema


def _builtin_name(ref: str) -> str:
    if ref in ("", "result", "builtin:result"):
        return "result.schema.yaml"
    if ref.endswith(".schema.yaml"):
        return Path(ref).name
    return f"{ref}.schema.yaml"


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True
