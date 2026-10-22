"""Usage accounting helpers."""

from __future__ import annotations

from typing import Any

from ..contracts import Usage


def usage_from_dict(data: dict[str, Any] | None) -> Usage:
    if not data:
        return Usage()
    return Usage(
        model=str(data.get("model") or ""),
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
        cached_tokens=int(data.get("cached_tokens") or 0),
        calls=int(data.get("calls") or 1),
        latency_ms=int(data.get("latency_ms") or 0),
        cost=data.get("cost") or 0,
    )


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
