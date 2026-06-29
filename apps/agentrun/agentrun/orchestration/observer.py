"""Observer:解析执行输出,区分 进度 / 最终结果 / 阻断(见 design/04 §3)。

不从 tmux 屏幕判断 run 完成 —— run 完成只认 02 的 result.json。
"""
from __future__ import annotations

from typing import Any


class Observer:
    def observe(self, action: dict[str, Any], exec_result: dict[str, Any]) -> dict[str, Any]:
        status = exec_result.get("status")
        output = exec_result.get("output")
        if status == "ok":
            return {"kind": "progress", "content": output}
        if status == "blocked":
            return {"kind": "blocked", "content": output}
        # error 默认停下(不自动重试),进 blocked 交上层处理
        return {"kind": "blocked", "content": output}
