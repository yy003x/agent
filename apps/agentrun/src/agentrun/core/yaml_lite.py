"""零依赖的最小 YAML 加载器,仅覆盖配置所需子集。

支持:嵌套 mapping(缩进)、`- ` 列表、标量(str/int/float/bool/null)、
行内空 `[]` / `{}`、`#` 注释、引号字符串。不支持锚点 / 多文档 / 复杂流式语法。
配置若超出该子集,fail-fast 报错优于悄悄解析错。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def load_yaml(path: str | Path) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    return loads(text)


def loads(text: str) -> Any:
    lines = _significant_lines(text)
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, lines[0][0])
    if idx != len(lines):
        raise ValueError(f"yaml_lite: 解析未消费全部内容,停在第 {idx} 行")
    return value


def _significant_lines(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if stripped.strip() == "":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        out.append((indent, stripped.strip()))
    return out


def _strip_comment(line: str) -> str:
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] == " ":
                return line[:i]
    return line


def _parse_block(lines: list[tuple[int, str]], idx: int, indent: int) -> tuple[Any, int]:
    if lines[idx][1].startswith("- "):
        return _parse_list(lines, idx, indent)
    return _parse_map(lines, idx, indent)


def _parse_list(lines: list[tuple[int, str]], idx: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while idx < len(lines):
        cur_indent, content = lines[idx]
        if cur_indent != indent or not content.startswith("- "):
            break
        rest = content[2:].strip()
        if ":" in rest and not _is_scalar_only(rest):
            # 列表元素是行内 mapping 起始:把该 key 当作子 map 的第一行
            synthetic = [(indent + 2, rest)] + lines[idx + 1 :]
            value, consumed = _parse_map(synthetic, 0, indent + 2)
            items.append(value)
            idx += consumed
        elif rest == "":
            value, idx2 = _parse_block(lines, idx + 1, lines[idx + 1][0])
            items.append(value)
            idx = idx2
        else:
            items.append(_scalar(rest))
            idx += 1
    return items, idx


def _parse_map(lines: list[tuple[int, str]], idx: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while idx < len(lines):
        cur_indent, content = lines[idx]
        if cur_indent != indent:
            break
        if ":" not in content:
            raise ValueError(f"yaml_lite: 期望 key: value,得到 {content!r}")
        key, _, raw_val = content.partition(":")
        key = key.strip()
        raw_val = raw_val.strip()
        if raw_val == "":
            if idx + 1 < len(lines) and lines[idx + 1][0] > indent:
                value, idx = _parse_block(lines, idx + 1, lines[idx + 1][0])
            else:
                value = None
                idx += 1
            result[key] = value
        else:
            result[key] = _scalar(raw_val)
            idx += 1
    return result, idx


def _is_scalar_only(text: str) -> bool:
    return False


def _scalar(token: str) -> Any:
    if token == "[]":
        return []
    if token == "{}":
        return {}
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p.strip()) for p in inner.split(",")]
    low = token.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token
