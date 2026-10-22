"""Event stream: one schema, several sinks.

Every event is a JSON object with the fixed envelope
``{v, seq, ts, run, type, node, round, data}``.  Events are appended to
``runs/<id>/events.jsonl`` as they happen; the terminal, the JSON reporter, the
HTML replay and the Mermaid sequence diagram are all derived from this file.

``EventBus.emit`` is the *single exit* for secrets: it redacts the payload
before any sink sees it.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO

from .contracts import iso, utc_now

EVENT_VERSION = 1
MAX_EVENT_BYTES = 64 * 1024

_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|secret|password|(?:^|[_-])token(?:$|[_-])|authorization|bearer)")
_SECRET_VALUE = re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def _default_secrets() -> tuple[str, ...]:
    values: list[str] = []
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "AW_API_KEY", "AW_SECRET"):
        value = os.environ.get(key)
        if value and len(value) >= 6:
            values.append(value)
    return tuple(values)


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Recursively redact secret-looking keys and values.

    This runs on the event payload only; file contents are never redacted.
    """
    secret_values = [item for item in secrets if item]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                result[str(key)] = "***"
            else:
                result[str(key)] = redact(item, secret_values)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, str):
        text = value
        for secret in secret_values:
            if secret in text:
                text = text.replace(secret, "***")
        text = _SECRET_VALUE.sub("***", text)
        return text
    return value


@dataclass(frozen=True)
class Event:
    seq: int
    ts: str
    run: str
    type: str
    node: str | None = None
    round: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    v: int = EVENT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "seq": self.seq,
            "ts": self.ts,
            "run": self.run,
            "type": self.type,
            "node": self.node,
            "round": self.round,
            "data": self.data,
        }


class EventBus:
    """Thread-safe event fan-out with redaction at the boundary."""

    def __init__(
        self,
        run_id: str,
        *,
        run_dir: Path | None = None,
        secrets: Iterable[str] = (),
        human_sink: Callable[[Event], None] | None = None,
        json_stream: TextIO | None = None,
        retain: bool = True,
    ) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self._secrets = tuple(secrets) + _default_secrets()
        self._human = human_sink
        self._json_stream = json_stream
        self._retain = retain
        self._events: list[Event] = []
        self._seq = 0
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            self._file = (run_dir / "events.jsonl").open("a", encoding="utf-8", newline="\n")

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def emit(
        self,
        event_type: str,
        *,
        node: str | None = None,
        round: int = 0,
        data: dict[str, Any] | None = None,
    ) -> Event:
        payload = redact(data or {}, self._secrets)
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            payload = {
                "truncated": True,
                "bytes": len(encoded.encode("utf-8")),
                "keys": sorted(payload)[:50],
            }
        with self._lock:
            self._seq += 1
            event = Event(
                seq=self._seq,
                ts=iso(utc_now()),
                run=self.run_id,
                type=event_type,
                node=node,
                round=round,
                data=payload,
            )
            if self._file is not None:
                self._file.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
                self._file.flush()
            if self._json_stream is not None:
                self._json_stream.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n")
                self._json_stream.flush()
            if self._human is not None:
                self._human(event)
            if self._retain:
                self._events.append(event)
        return event

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()
                self._file.close()
                self._file = None


def read_events(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None and len(events) > limit:
        return events[-limit:]
    return events


def last_events(events: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    return events[-count:] if count > 0 else []
