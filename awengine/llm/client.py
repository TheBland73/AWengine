"""LLM providers: live OpenAI-compatible, recorded, and fake.

Only :class:`OpenAICompatibleLLM` touches the network.  Tests instantiate a
:class:`~awengine.llm.fake.FakeLLM` with cassettes, so the whole suite runs
offline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..contracts import Usage, utc_now
from ..errors import LLMError, LLMNoKey

DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] = field(default_factory=dict)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments} for call in self.tool_calls
            ],
            "usage": self.usage.model_dump(mode="json"),
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMResponse":
        return cls(
            text=data.get("text", ""),
            tool_calls=[
                ToolCall(id=item.get("id", ""), name=item.get("name", ""), arguments=item.get("arguments") or {})
                for item in data.get("tool_calls") or []
            ],
            usage=Usage(**(data.get("usage") or {})),
            model=data.get("model", ""),
        )


class LLMProvider(Protocol):
    model: str

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 120,
    ) -> LLMResponse: ...


def resolve_api_key(model: str) -> tuple[str, str]:
    """Return ``(api_key, base_url)`` for a model name."""
    lower = model.lower()
    if lower.startswith("deepseek"):
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("AW_API_KEY") or ""
        base = os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URLS["deepseek"]
        return key, base
    if lower.startswith(("gpt", "openai", "o1", "o3", "o4")):
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AW_API_KEY") or ""
        base = os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URLS["openai"]
        return key, base
    return (
        os.environ.get("AW_API_KEY") or os.environ.get("OPENAI_API_KEY") or "",
        os.environ.get("AW_BASE_URL") or DEFAULT_BASE_URLS["openai"],
    )


class OpenAICompatibleLLM:
    """Thin wrapper over the ``openai`` SDK (imported lazily)."""

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str = "") -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any = None

    def _ensure(self, model: str) -> Any:
        key = self._api_key
        base = self._base_url
        if not key:
            key, base = resolve_api_key(model)
        if not key:
            raise LLMNoKey(model)
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LLMError(
                "openai package is not installed; install it with `pip install awengine[llm]`",
                "or run with --fake to use cassettes",
            ) from exc
        self._client = OpenAI(api_key=key, base_url=base, timeout=120.0)
        return self._client

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
        client = self._ensure(model)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalise third-party errors
            raise LLMError(f"LLM call failed: {exc}", "retry, or check API key / base URL") from exc
        choice = response.choices[0].message if response.choices else None
        text = getattr(choice, "content", "") if choice else ""
        calls: list[ToolCall] = []
        for call in getattr(choice, "tool_calls", None) or []:
            import json

            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"__raw__": call.function.arguments}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        usage = Usage(model=model)
        if getattr(response, "usage", None) is not None:
            raw_usage = response.usage
            cached = 0
            details = getattr(raw_usage, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            usage = Usage(
                model=model,
                prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
                cached_tokens=cached,
                calls=1,
            )
        return LLMResponse(text=text or "", tool_calls=calls, usage=usage, model=model)


def parse_json_response(text: str) -> dict[str, Any]:
    """Extract the JSON object a node returned, tolerating ``` fences."""
    import json

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(
                f"model response is not JSON: {cleaned[:200]!r}",
                "llm_agent nodes must return a JSON object; check the prompt",
            )
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"model response is not valid JSON: {exc}",
                "llm_agent nodes must return a JSON object; check the prompt",
            ) from exc
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return data
