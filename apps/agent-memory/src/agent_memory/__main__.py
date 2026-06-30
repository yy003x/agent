"""Module entrypoint for agent memory tools."""

from __future__ import annotations

import sys

from agent_memory import finalize, learning


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: python -m agent_memory {finalize|learning} ...")
        return 0
    command, rest = args[0], args[1:]
    if command == "finalize":
        return finalize.main(rest)
    if command == "learning":
        return learning.main(rest)
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
