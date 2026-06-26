"""Observers for runtime completion signals."""
from __future__ import annotations

import json
from pathlib import Path


class FileResultObserver:
    """Observe result-file based runtime completion."""

    def exists(self, result_path: str | Path) -> bool:
        return Path(result_path).exists()

    def read_json(self, result_path: str | Path) -> dict | None:
        path = Path(result_path)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
