from __future__ import annotations

from calc import add, mul


def test_add_positive():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, 1) == 0


def test_mul():
    assert mul(3, 4) == 12
