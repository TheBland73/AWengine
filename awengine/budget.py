"""Token pricing and cost budgets, in CNY.

Prices live in ``pricing.toml`` next to the project (or next to the installed
package) and carry a ``version`` so a run manifest can record which table was
used.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .contracts import Usage
from .errors import PricingMissing

MICRO = Decimal("1000000")
_CENT = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None = None

    def cost(self, usage: Usage) -> Decimal:
        cached = usage.cached_tokens or 0
        fresh = max(0, usage.prompt_tokens - cached)
        total = fresh * self.input_per_mtok / MICRO
        total += usage.completion_tokens * self.output_per_mtok / MICRO
        if self.cached_input_per_mtok is not None and cached:
            total += cached * self.cached_input_per_mtok / MICRO
        return total.quantize(_CENT, rounding=ROUND_HALF_UP)


class Pricing:
    def __init__(self, models: dict[str, ModelPrice], *, version: str = "unknown", source: str = "") -> None:
        self.models = models
        self.version = version
        self.source = source

    def has(self, model: str) -> bool:
        return model in self.models

    def price(self, model: str) -> ModelPrice | None:
        if model in self.models:
            return self.models[model]
        # Allow provider prefixes such as "deepseek/deepseek-chat".
        short = model.rsplit("/", 1)[-1]
        return self.models.get(short)

    def cost(self, model: str, usage: Usage, *, required: bool = True) -> Decimal:
        price = self.price(model)
        if price is None:
            if required:
                raise PricingMissing(model)
            return Decimal("0")
        return price.cost(usage)


def _search_paths() -> list[Path]:
    paths = [Path.cwd() / "pricing.toml"]
    env = os.environ.get("AW_PRICING")
    if env:
        paths.insert(0, Path(env))
    package_root = Path(__file__).resolve().parent.parent
    paths.append(package_root / "pricing.toml")
    return paths


def load_pricing(path: str | os.PathLike[str] | None = None) -> Pricing:
    candidates = [Path(path)] if path else _search_paths()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PricingMissing("<file>", ) from exc
        models: dict[str, ModelPrice] = {}
        table: dict[str, Any] = data.get("models", data)
        for name, entry in table.items():
            if not isinstance(entry, dict):
                continue
            try:
                models[name] = ModelPrice(
                    input_per_mtok=Decimal(str(entry.get("input_per_mtok", entry.get("input", "0")))),
                    output_per_mtok=Decimal(str(entry.get("output_per_mtok", entry.get("output", "0")))),
                    cached_input_per_mtok=(
                        Decimal(str(entry["cached_input_per_mtok"]))
                        if "cached_input_per_mtok" in entry
                        else None
                    ),
                )
            except (ArithmeticError, ValueError):
                continue
        version = str(data.get("version", "unknown"))
        return Pricing(models, version=version, source=str(candidate))
    return Pricing({}, version="none", source="")


class Budget:
    def __init__(self, pricing: Pricing, limit_cny: Decimal | None) -> None:
        self.pricing = pricing
        self.limit = Decimal(str(limit_cny)) if limit_cny is not None else None

    def charge(self, model: str, usage: Usage, *, required: bool = True) -> Decimal:
        return self.pricing.cost(model, usage, required=required and self.limit is not None)

    def exceeded(self, used: Decimal) -> bool:
        if self.limit is None:
            return False
        return Decimal(str(used)) > self.limit

    def remaining(self, used: Decimal) -> Decimal | None:
        if self.limit is None:
            return None
        return max(Decimal("0"), self.limit - Decimal(str(used)))


CURRENCY = "¥"


def format_cny(value: Decimal | float | str) -> str:
    amount = Decimal(str(value))
    if amount == 0:
        return f"{CURRENCY}0"
    if abs(amount) < Decimal("0.01"):
        return f"{CURRENCY}{amount:.4f}"
    return f"{CURRENCY}{amount:.2f}"
