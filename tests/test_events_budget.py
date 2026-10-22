"""Event bus redaction/persistence and pricing/budget arithmetic."""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from awengine.budget import Budget, ModelPrice, Pricing, format_cny, load_pricing
from awengine.contracts import Usage
from awengine.errors import PricingMissing
from awengine.events import EventBus, read_events


def test_event_bus_persists_monotonic_events(tmp_path: Path) -> None:
    stream = io.StringIO()
    bus = EventBus("run-1", run_dir=tmp_path, json_stream=stream)
    bus.emit("run_start", data={"workflow": "demo"})
    bus.emit("node_end", node="plan", round=1, data={"status": "ok"})
    bus.close()

    events = read_events(tmp_path / "events.jsonl")
    assert [event["seq"] for event in events] == [1, 2]
    assert events[0]["type"] == "run_start"
    assert events[1]["node"] == "plan"
    # JSON stream mirrors the file, one object per line.
    mirrored = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert mirrored == events


def test_event_bus_redacts_secrets_but_keeps_token_counts(tmp_path: Path) -> None:
    bus = EventBus("run-1", run_dir=tmp_path, secrets=("sk-supersecret",))
    bus.emit(
        "llm_call",
        data={
            "api_key": "sk-supersecret",
            "authorization": "Bearer sk-supersecret",
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "note": "using sk-supersecret now",
        },
    )
    bus.close()
    event = read_events(tmp_path / "events.jsonl")[0]
    assert event["data"]["api_key"] == "***"
    assert "sk-supersecret" not in json.dumps(event)
    assert event["data"]["prompt_tokens"] == 12
    assert event["data"]["completion_tokens"] == 7


def test_event_bus_truncates_oversized_payload(tmp_path: Path) -> None:
    bus = EventBus("run-1", run_dir=tmp_path)
    bus.emit("tool_call", data={"blob": "x" * 100_000, "tool": "read_file"})
    bus.close()
    event = read_events(tmp_path / "events.jsonl")[0]
    assert event["data"]["truncated"] is True
    assert event["data"]["bytes"] > 64 * 1024


def test_pricing_arithmetic_and_cache_discount() -> None:
    pricing = Pricing(
        {
            "m": ModelPrice(
                input_per_mtok=Decimal("2"),
                output_per_mtok=Decimal("8"),
                cached_input_per_mtok=Decimal("0.5"),
            )
        },
        version="test",
    )
    usage = Usage(model="m", prompt_tokens=1_000_000, completion_tokens=500_000, cached_tokens=400_000)
    # 600k fresh input * 2 + 400k cached * 0.5 + 500k output * 8, per Mtok.
    assert pricing.cost("m", usage) == Decimal("5.400000")
    assert pricing.cost("vendor/m", usage) == Decimal("5.400000")
    with pytest.raises(PricingMissing):
        pricing.cost("unknown", usage)


def test_budget_limit_and_formatting(tmp_path: Path) -> None:
    pricing = load_pricing(Path(__file__).resolve().parent.parent / "pricing.toml")
    assert pricing.has("deepseek-chat")
    budget = Budget(pricing, Decimal("1"))
    usage = Usage(model="deepseek-chat", prompt_tokens=1000, completion_tokens=1000)
    cost = budget.charge("deepseek-chat", usage)
    assert cost > 0
    assert budget.exceeded(cost) is False
    assert budget.exceeded(Decimal("2")) is True
    assert budget.remaining(Decimal("2")) == Decimal("0")
    assert format_cny(Decimal("0")) == "¥0"
    assert format_cny(Decimal("0.00332")).startswith("¥0.0033")
    assert format_cny(Decimal("1.5")) == "¥1.50"
