"""The grounding guard: figures spoken must be figures supplied.

`figures` is lifted out of src/briefing/synthesize.py with ast rather than
imported, because that module imports the Databricks SDK and builds a Spark
session at import time. The function is pure, so extracting the definition
keeps this runnable with no Databricks dependencies - which is what lets it
run in CI.

The cases are real. Every fabricated figure here was actually published by
this pipeline and found in the 2026-09-01 end-to-end audit.
"""
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "briefing" / "synthesize.py"


def _load(name):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"re": __import__("re")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SRC), "exec"), ns)
    return ns[name]


figures = _load("figures")


def ungrounded(script, supplied):
    """The guard's rule, as synthesize.py applies it."""
    given = figures(supplied)
    return sorted(v for v in figures(script)
                  if not any(abs(v - t) <= max(0.05, abs(v) * 0.01) for t in given))


def test_catches_figures_invented_on_top_of_a_headline():
    """The IPO fabrication. The source is a 325-character teaser; the script
    invented both the year and the amount and credited them to the article."""
    source = ("If You Invested $1,000 In Nvidia Stock at IPO, Here's How Much "
              "You'd Have Now. NVIDIA Corp CEO Jensen Huang may not check the "
              "price of the stock for the company he runs and co-founded.")
    script = ("The article points out that a $1,000 stake at the 1999 IPO "
              "would be worth well over $200,000 today.")
    assert ungrounded(script, source) == [1999.0, 200000.0]


def test_does_not_flag_a_figure_the_source_really_contains():
    source = "If You Invested $1,000 In Nvidia Stock at IPO"
    assert ungrounded("a $1,000 stake", source) == []


def test_catches_a_fabricated_derivation():
    """The '81 cents' error: 100 - 19.0, where 19.0 was a growth-rate gap in
    percentage points, not a cost ratio. The real figure was 25 cents."""
    supplied = "Costs grew SLOWER than revenue by 19.0 percentage points year-over-year."
    script = "for every extra dollar of sales the company spent only about 81 cents"
    assert 81.0 in ungrounded(script, supplied)


@pytest.mark.parametrize("spoken,given", [
    ("75 percent", "gross margin 74.98 percent"),      # one-decimal rounding
    ("96.2 billion", "revenue 96.221 billion"),         # the prompt asks for this
    ("34.0 percent", "operating margin 34.04 percent"),
    ("209 basis points", "compressed 208.6 basis points"),
    ("96,221", "96221"),                                # comma formatting
])
def test_rounding_and_formatting_are_not_flagged(spoken, given):
    """The prompt asks for one decimal place, so the script legitimately
    rounds. A guard that flags rounding would cry wolf on every episode."""
    assert ungrounded(spoken, given) == []


def test_empty_and_missing_text_are_safe():
    assert figures("") == set()
    assert figures(None) == set()
    assert ungrounded("", "anything") == []
