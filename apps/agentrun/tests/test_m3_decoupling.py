"""M3 解耦自检:内核 grep 不得出现业务名 / 写死仓库路径(见 design/05 §5 / design/07 §6)。

这是"业务无关"红线的可执行守卫,等价于 CI 静态检查。
"""
from __future__ import annotations

import unittest
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "src" / "agentrun"

# 业务名(不区分大小写)+ 写死的用户/仓库绝对路径前缀
# 注:不含 "agentruntime" —— 会误伤合法门面类名 AgentRuntime(见 design 命名表)
_BANNED_NAMES = ("lark", "workbench", "stock", "选股", "选题", "mozi")
_BANNED_PATHS = ("/Users/", "/home/", "/opt/homebrew")


class DecouplingTest(unittest.TestCase):
    def test_kernel_has_no_business_names_or_hardcoded_paths(self) -> None:
        offenders: list[str] = []
        for path in sorted(_PKG.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            low = text.lower()
            for name in _BANNED_NAMES:
                if name.lower() in low:
                    offenders.append(f"{path}: 业务名 {name!r}")
            for p in _BANNED_PATHS:
                if p in text:
                    offenders.append(f"{path}: 写死路径 {p!r}")
        self.assertEqual(offenders, [], f"内核出现业务名/写死路径:\n" + "\n".join(offenders))

    def test_conf_has_no_hardcoded_user_paths(self) -> None:
        offenders: list[str] = []
        for path in sorted((_PKG / "conf").rglob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            for p in _BANNED_PATHS:
                if p in text:
                    offenders.append(f"{path}: {p!r}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
