from __future__ import annotations

from calc import add, mul


def test_add_holdout_negative():
    assert add(-2, 3) == 1


def test_add_holdout_large():
    assert add(10**6, 1) == 10**6 + 1


def test_mul_holdout():
    assert mul(-2, 5) == -10
