#!/usr/bin/env python3
"""Scaffold a project-local skill for the personal workbench."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = ROOT / "skills"


def _valid_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise argparse.ArgumentTypeError("name must be lower-case hyphen format")
    return value


def _render(name: str, description: str, short_description: str) -> str:
    return f"""---
name: {name}
description: "{description}"
metadata:
  short-description: "{short_description}"
---

# {name} Skill

## 触发条件

- TODO: 写清这个 skill 应该在什么用户请求下触发。
- TODO: 写清哪些相近请求不应该触发。

## 执行流程

### 步骤 1：确认输入

TODO

### 步骤 2：执行

TODO

### 步骤 3：验证

TODO

## 输出契约

- 输出：
- 成功标准：
- 部分完成：

## 安全边界

不得写入 secret、token、cookie、private key、完整 JWT、账号密码或用户隐私。

## 验证

```bash
bash scripts/validate.sh --quick
```

## 完成标准

TODO
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, type=_valid_name)
    parser.add_argument("--description", required=True)
    parser.add_argument("--short-description", required=True)
    parser.add_argument("--allow-write", action="store_true")
    args = parser.parse_args()

    skill_dir = SKILLS_DIR / args.name
    skill_file = skill_dir / "SKILL.md"
    content = _render(args.name, args.description, args.short_description)

    if skill_file.exists():
        raise SystemExit(f"skill already exists: {skill_file.relative_to(ROOT)}")

    if not args.allow_write:
        print(f"[dry-run] would create: {skill_file.relative_to(ROOT)}")
        print(content)
        return 0

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content, encoding="utf-8")
    print(f"[skill] created: {skill_file.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
