"""Deterministic LLM doubles and the cassette format.

A cassette is a JSON file named after ``sha256(model + messages + tools +
params)``::

    {"request": {...}, "response": {"text": ..., "tool_calls": [...],
                                    "usage": {...}, "model": ...}}

``FakeLLM`` only reads cassettes.  ``RecordingLLM`` wraps a live provider and
writes one cassette per request, which is how the checked-in cassettes for the
demo workflows were produced.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..contracts import Usage, canonical_json, digest_of
from ..errors import CassetteMiss
from .client import LLMProvider, LLMResponse, ToolCall

CASSETTE_VERSION = 1


def cassette_key(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    max_tokens: int,
) -> str:
    return digest_of(
        {
            "v": CASSETTE_VERSION,
            "model": model,
            "messages": messages,
            "tools": tools or [],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    )


def default_cassette_dir() -> Path:
    env = os.environ.get("AW_CASSETTES")
    if env:
        return Path(env)
    candidates = [
        Path.cwd() / "cassettes",
        Path.cwd() / "tests" / "cassettes",
        Path(__file__).resolve().parent.parent.parent / "tests" / "cassettes",
        Path(__file__).resolve().parent.parent.parent / "cassettes",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


@dataclass
class CassetteStore:
    root: Path
    read_only: bool = True
    _cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if key in self._cache:
            return self._cache[key]
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        self._cache[key] = data
        return data

    def put(self, key: str, request: dict[str, Any], response: LLMResponse) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(key)
        payload = {
            "version": CASSETTE_VERSION,
            "key": key,
            "request": request,
            "response": response.to_dict(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        self._cache[key] = payload
        return path

    def count(self) -> int:
        return len(list(self.root.glob("*.json"))) if self.root.is_dir() else 0


class FakeLLM:
    """Read-only provider backed by cassettes."""

    def __init__(self, store: CassetteStore, *, model: str = "fake") -> None:
        self.store = store
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 120,
    ) -> LLMResponse:
        key = cassette_key(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.calls.append({"model": model, "key": key, "messages": len(messages)})
        data = self.store.get(key)
        if data is None:
            raise CassetteMiss(key)
        response = LLMResponse.from_dict(data.get("response") or {})
        response.model = response.model or model
        return response


class RecordingLLM:
    """Wrap a live provider and persist every request/response pair."""

    def __init__(self, inner: LLMProvider, store: CassetteStore) -> None:
        self.inner = inner
        self.store = store
        self.model = getattr(inner, "model", "")

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 120,
    ) -> LLMResponse:
        response = self.inner.complete(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        key = cassette_key(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.store.put(
            key,
            {
                "model": model,
                "messages": messages,
                "tools": tools or [],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            response,
        )
        return response


class ScriptedLLM:
    """Return pre-built responses in order; optionally callable by request.

    ``responder`` receives ``(model, messages, tools, turn)`` and returns an
    :class:`LLMResponse`.  This is what the test-suite uses for agent loops.
    """

    def __init__(
        self,
        responses: Iterable[LLMResponse] | None = None,
        responder: Callable[[str, list[dict[str, Any]], list[dict[str, Any]] | None, int], LLMResponse] | None = None,
        *,
        model: str = "fake",
    ) -> None:
        self._responses = list(responses or [])
        self._responder = responder
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 120,
    ) -> LLMResponse:
        turn = len(self.calls)
        self.calls.append({"model": model, "messages": messages, "tools": tools, "turn": turn})
        if self._responder is not None:
            return self._responder(model, messages, tools, turn)
        if turn < len(self._responses):
            return self._responses[turn]
        raise CassetteMiss(f"scripted-response-{turn}")


def response(
    text: str = "",
    *,
    tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    model: str = "fake",
) -> LLMResponse:
    calls = [
        ToolCall(id=f"call_{index}", name=name, arguments=arguments)
        for index, (name, arguments) in enumerate(tool_calls or [])
    ]
    return LLMResponse(
        text=text,
        tool_calls=calls,
        usage=Usage(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            calls=1,
            cost=0,
        ),
        model=model,
    )
