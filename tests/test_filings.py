"""SEC filing text extraction.

The pure functions are lifted out of src/ingest/filings.py with ast rather
than imported, because importing runs module-level urllib setup. Every case
below is real text taken from live EDGAR documents on 2026-09-01.
"""
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "ingest" / "filings.py"


def _load(*names):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    want = set(names) | {"ITEM_MEANINGS"}
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in want)
            or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in want)]
    ns = {"re": __import__("re"), "html": __import__("html")}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SRC), "exec"), ns)
    return [ns[n] for n in names]


clean, describe, strip_html = _load("clean", "describe", "strip_html")


@pytest.mark.parametrize("raw,expected_start", [
    # Real exhibit headers. Their length and order vary by filer, which is why
    # they are stripped token by token rather than with one pattern.
    ("EX-99.1 5 d83560dex991.htm EX-99.1 EX-99.1 Exhibit 99.1 Alphabet Announces",
     "Alphabet Announces"),
    ("EX-99.1 2 a2026q3ex991-pressrelease.htm EX-99.1 - PRESS RELEASE Document "
     "Exhibit 99.1 FOR IMMEDIATE RELEASE", "FOR IMMEDIATE RELEASE"),
    ("EX-99.1 2 tm2624017d1_ex99-1.htm EXHIBIT 99.1 Exhibit 99.1 Micron Announces",
     "Micron Announces"),
    ("Exhibit 99.1 Alphabet Announces Second Quarter 2026 Results",
     "Alphabet Announces"),
])
def test_edgar_exhibit_headers_are_stripped(raw, expected_start):
    assert clean(raw).startswith(expected_start)


@pytest.mark.parametrize("raw", [
    "NVIDIA Announces Financial Results for Second Quarter Fiscal 2027",
    "CFO Commentary on Second Quarter Fiscal 2027 Results",
])
def test_a_real_headline_is_left_alone(raw):
    """The stripper must not eat content that merely starts with a word it
    recognises."""
    assert clean(raw) == raw


def test_item_codes_become_words():
    """A code that matters is never handed to the model as a number - same
    rule as stating a direction in words rather than a signed value."""
    assert describe("2.02") == "results of operations and financial condition"
    assert describe("5.02") == "a change of directors or principal officers"
    out = describe("2.02,9.01")
    assert "results of operations" in out and "financial statements" in out


def test_unknown_item_codes_are_dropped_not_guessed():
    assert describe("99.99") == ""
    assert describe("") == ""
    assert describe(None) == ""


def test_html_becomes_readable_text():
    html_in = ('<div><style>p{color:red}</style><script>x=1</script>'
               '<p>Revenue of&nbsp;$96.2&nbsp;billion,</p><td>up 106%</td></div>')
    out = strip_html(html_in)
    assert "Revenue of $96.2 billion, up 106%" in out
    # Script and style contents must not survive as text.
    assert "color:red" not in out and "x=1" not in out
