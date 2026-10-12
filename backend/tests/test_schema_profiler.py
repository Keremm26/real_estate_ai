"""
Offline tests for the schema-alignment profiler's number parsing
(app/services/schema_alignment/profiler.py).

Run:
    PYTHONPATH=. python tests/test_schema_profiler.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.schema_alignment.profiler import parse_numeric  # noqa: E402


def _parse(values):
    return parse_numeric(pd.Series(values, dtype=str))


def test_dot_decimals_with_a_few_commas_stay_dot():
    # Madrid norenov_global: dot decimals, a handful of rows with a comma
    is_num, style, nums = _parse(["135.44", "29.02", "282.36", "0.00", "65.24"] * 200 + ["12,5"])
    assert is_num and style == "dot"
    assert abs(nums.iloc[0] - 135.44) < 1e-9


def test_comma_decimals():
    is_num, style, nums = _parse(["135,44", "29,02", "1.234,56", "0,00"] * 50)
    assert is_num and style == "comma"
    assert sorted(nums.unique().tolist()) == [0.0, 29.02, 135.44, 1234.56]


def test_anglo_thousands_with_decimals():
    is_num, style, nums = _parse(["1,234.56", "12.50", "3,000.00"] * 50)
    assert is_num and style == "dot"
    assert 1234.56 in nums.tolist()


def test_integers_default_to_dot():
    is_num, style, _ = _parse(["12", "300", "45"] * 50)
    assert is_num and style == "dot"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")
