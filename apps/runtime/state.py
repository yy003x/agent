"""Runtime gateway errors."""
from __future__ import annotations


class RuntimeErrorState(RuntimeError):
    """Raised when a runtime operation cannot be completed."""
