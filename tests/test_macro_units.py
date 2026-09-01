"""Macro values must carry the unit that makes them a fact.

Lifted out of src/briefing/synthesize.py with ast rather than imported,
because that module needs the Databricks SDK. The cases are real FRED values
taken from silver_macro_context on 2026-09-01.
"""
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "briefing" / "synthesize.py"


def _load(*names):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    want = set(names) | {"MACRO_UNITS"}
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in want)
            or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in want)]
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SRC), "exec"), ns)
    return [ns[n] for n in names]


macro_value, macro_change = _load("macro_value", "macro_change")


def test_the_bug_that_prompted_this():
    """PNFI 4623.356 reached the prompt bare and the script said "climbing to
    4 623" - which a listener hears as "four, six twenty three", and which the
    grounding guard flagged because the space split the number."""
    out = macro_value("PNFI", 4623.356)
    assert out == "$4.62 trillion at an annual rate"
    assert "4623" not in out


@pytest.mark.parametrize("sid,value,expected", [
    ("DGS10", 4.73, "4.73 percent"),
    ("FEDFUNDS", 3.63, "3.63 percent"),
    ("T10Y2Y", 0.41, "0.41 percentage points"),
    ("CPIAUCSL", 332.813, "332.81 index points, where 1982 to 1984 equals 100"),
])
def test_each_series_carries_its_unit(sid, value, expected):
    assert macro_value(sid, value) == expected


def test_a_rate_moves_in_percentage_points_not_percent():
    """4.5 to 4.7 is 0.2 percentage points, not 0.2 percent. Getting this
    wrong is the kind of error a briefing states confidently."""
    assert macro_change("DGS10", 0.22) == "0.22 percentage points"
    assert "percentage points" in macro_change("FEDFUNDS", -0.01)


def test_dollar_changes_stay_in_billions():
    """The level reads better in trillions; a 132.84 move does not."""
    assert macro_change("PNFI", 132.84) == "$132.8 billion"


def test_change_is_unsigned_because_direction_is_said_in_words():
    assert macro_change("DGS10", -0.22) == macro_change("DGS10", 0.22)


def test_small_dollar_values_stay_in_billions():
    assert macro_value("PNFI", 950.0) == "$950.0 billion at an annual rate"


def test_unknown_series_and_nulls_do_not_crash():
    assert macro_value("NEWSERIES", 12.345) == "12.35"
    assert macro_change("NEWSERIES", 1.0) == "1.00"
    assert macro_value("DGS10", None) == "unavailable"
    assert macro_change("PNFI", None) == "unavailable"
