"""Prompt/context assembly with an explicit, testable trim order (5.7).

Order is fixed and must never be "summarise first because it is cheaper":

1. mask large tool observations (``> 2 KiB`` -> first 200 chars + path + sha256)
2. summarise older rounds to at most 200 chars each
3. drop older rounds, keeping the last ``window`` rounds
4. if still over budget -> fail with ``F-CONTEXT-OVERFLOW``

We never silently truncate the current input: overflow is an error, because a
model that silently loses the question is worse than a run that stops.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_json
from .errors import AwfError

MASK_THRESHOLD = 2048
MASK_PREVIEW = 200
SUMMARY_CHARS = 200
CHARS_PER_TOKEN = 4


class ContextOverflow(AwfError):
    def __init__(self, used: int, limit: int, *, node: str = "") -> None:
        super().__init__(
            "F-CONTEXT-OVERFLOW",
            f"context for node {node!r} is {used} tokens after trimming, limit is {limit}",
            "raise context.max_tokens, narrow context.sources, or reduce the node's input",
            exit_code=3,
        )
        self.used = used
        self.limit = limit


class ContextPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    sources: tuple[str, ...] = ("input", "history", "repo_map", "artifacts")
    window: int = 2
    compaction: Literal["none", "summarize", "drop", "mask"] = "summarize"
    max_tokens: int = 12_000
    isolation: Literal["none", "fresh"] = "none"
    mask_observations: bool = True

    @model_validator(mode="after")
    def _check(self) -> "ContextPolicy":
        if self.max_tokens < 256:
            raise ValueError("context.max_tokens must be >= 256")
        if self.window < 1:
            raise ValueError("context.window must be >= 1")
        unknown = set(self.sources) - {"input", "history", "repo_map", "artifacts", "messages"}
        if unknown:
            raise ValueError(f"unknown context sources: {', '.join(sorted(unknown))}")
        return self


@dataclass
class ContextBundle:
    messages: list[dict[str, Any]]
    tokens: int
    trimmed: list[str] = field(default_factory=list)
    prefix_sha: str = ""
    overflow: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "trimmed": self.trimmed,
            "prefix_sha": self.prefix_sha,
            "messages": len(self.messages),
        }


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def messages_tokens(messages: Iterable[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(str(message.get("content") or ""))
        total += 8
    return total


def mask_text(text: str, *, source: str = "observation") -> str:
    if len(text.encode("utf-8")) <= MASK_THRESHOLD:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{text[:MASK_PREVIEW]}\n...[masked {len(text)} chars sha256:{digest} source:{source}]"


def summarize_entry(entry: dict[str, Any], max_chars: int = SUMMARY_CHARS) -> str:
    node = entry.get("node") or "?"
    status = entry.get("status") or "?"
    summary = entry.get("summary") or entry.get("output") or ""
    if not isinstance(summary, str):
        summary = canonical_json(summary)
    summary = " ".join(summary.split())
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."
    return f"- round {entry.get('round', '?')} {node} [{status}] {summary}".strip()


def _format_history(entries: list[dict[str, Any]], *, window: int, summarise: bool) -> str:
    if not entries:
        return ""
    recent = entries[-window:]
    older = entries[:-window]
    lines: list[str] = []
    if older:
        if summarise:
            lines.append("Older rounds (summarised):")
            lines.extend(summarize_entry(entry) for entry in older)
        else:
            dropped = len(older)
            lines.append(f"[{dropped} older round(s) omitted]")
    if recent:
        lines.append("Recent rounds:")
        for entry in recent:
            lines.append(summarize_entry(entry, max_chars=1200))
    return "\n".join(lines)


def assemble(
    *,
    system: str,
    task: str,
    policy: ContextPolicy,
    node: str = "",
    repo_map: str = "",
    history: Iterable[dict[str, Any]] = (),
    observations: Iterable[dict[str, Any]] = (),
    artifacts: dict[str, Any] | None = None,
) -> ContextBundle:
    """Build the chat messages for one LLM node call."""
    history_list = list(history)
    observation_list = list(observations)
    trimmed: list[str] = []

    if policy.isolation == "fresh" or policy.compaction == "none":
        history_list = []
        observation_list = []

    if policy.mask_observations:
        masked: list[dict[str, Any]] = []
        for item in observation_list:
            text = str(item.get("content") or "")
            new_text = mask_text(text, source=str(item.get("source") or "tool"))
            if new_text != text:
                trimmed.append(f"masked observation {item.get('source', '?')}")
            masked.append({**item, "content": new_text})
        observation_list = masked

    history_text = _format_history(history_list, window=policy.window, summarise=False)
    if policy.compaction in ("summarize", "mask"):
        history_text = _format_history(history_list, window=policy.window, summarise=True)
        if len(history_list) > policy.window:
            trimmed.append(f"summarised {len(history_list) - policy.window} older round(s)")
    elif policy.compaction == "drop":
        history_text = _format_history(history_list, window=policy.window, summarise=False)
        if len(history_list) > policy.window:
            trimmed.append(f"dropped {len(history_list) - policy.window} older round(s)")

    sections: list[str] = []
    if "repo_map" in policy.sources and repo_map:
        sections.append(f"## Repository map\n{repo_map}")
    if "history" in policy.sources and history_text:
        sections.append(f"## History\n{history_text}")
    if "artifacts" in policy.sources and artifacts:
        rendered = []
        for key in sorted(artifacts):
            value = artifacts[key]
            if isinstance(value, dict):
                rendered.append(f"- {key}: {canonical_json(value)[:1200]}")
            else:
                rendered.append(f"- {key}: {str(value)[:400]}")
        sections.append("## Available artifacts\n" + "\n".join(rendered))
    if "input" in policy.sources:
        sections.append(f"## Current task\n{task}")
    if observations:
        rendered_obs = []
        for item in observation_list:
            rendered_obs.append(f"[{item.get('source', 'tool')}]\n{item.get('content', '')}")
        sections.append("## Tool observations\n" + "\n\n".join(rendered_obs))

    user_content = "\n\n".join(sections).strip() or task
    messages = [
        {"role": "system", "content": system.strip()},
        {"role": "user", "content": user_content},
    ]
    tokens = messages_tokens(messages)

    if tokens > policy.max_tokens:
        # Last resort per the fixed order: drop history entirely (never the
        # current input), then re-check.  Still over -> hard failure.
        if history_text and policy.compaction in ("drop", "summarize"):
            messages = [
                {"role": "system", "content": system.strip()},
                {"role": "user", "content": "\n\n".join(sections[:-1] or sections[-1:])},
            ]
            tokens = messages_tokens(messages)
            trimmed.append("dropped all history to fit budget")
    if tokens > policy.max_tokens:
        raise ContextOverflow(tokens, policy.max_tokens, node=node)

    prefix = hashlib.sha256(canonical_json([system, repo_map]).encode("utf-8")).hexdigest()
    return ContextBundle(
        messages=messages,
        tokens=tokens,
        trimmed=trimmed,
        prefix_sha=prefix,
    )
