"""LLM package: one protocol, three implementations."""

from __future__ import annotations

from .client import (
    LLMProvider,
    LLMResponse,
    OpenAICompatibleLLM,
    ToolCall,
    parse_json_response,
    resolve_api_key,
)
from .fake import CassetteStore, FakeLLM, RecordingLLM, ScriptedLLM, default_cassette_dir, response
from .usage import approx_tokens, usage_from_dict

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatibleLLM",
    "ToolCall",
    "parse_json_response",
    "resolve_api_key",
    "CassetteStore",
    "FakeLLM",
    "RecordingLLM",
    "ScriptedLLM",
    "default_cassette_dir",
    "response",
    "approx_tokens",
    "usage_from_dict",
]
