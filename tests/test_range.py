"""HTTP range parsing for the audio endpoint.

`_parse_range` is lifted out of src/app/app.py with the ast module rather than
imported, because importing that module pulls in psycopg and databricks-sdk and
opens a Postgres connection path. The function is pure and self-contained, so
extracting just its definition keeps this runnable with no Databricks
dependencies at all - which is what lets it run in CI.
"""
import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "src" / "app" / "app.py"


def _load(name):
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(APP), "exec"), ns)
    return ns[name]


parse_range = _load("_parse_range")
TOTAL = 1000


@pytest.mark.parametrize("header,expected", [
    # No range at all -> serve the whole body.
    (None, None),
    ("", None),
    # The ordinary forms.
    ("bytes=0-", (0, 999)),        # what Chrome opens a media file with
    ("bytes=0-1", (0, 1)),         # both ends INCLUSIVE, so two bytes
    ("bytes=500-999", (500, 999)),
    ("bytes=999-", (999, 999)),
    # An end past EOF clamps rather than failing.
    ("bytes=500-4000", (500, 999)),
    # Suffix form is the LAST n bytes, not the first n.
    ("bytes=-100", (900, 999)),
    ("bytes=-5000", (0, 999)),     # longer than the file -> whole file
])
def test_satisfiable_ranges(header, expected):
    assert parse_range(header, TOTAL) == expected


@pytest.mark.parametrize("header", [
    "bytes=1000-",       # starts exactly at EOF
    "bytes=2000-3000",   # wholly past EOF
    "bytes=-0",          # zero-length suffix
    "bytes=5-3",         # reversed
])
def test_unsatisfiable_ranges_are_416(header):
    assert parse_range(header, TOTAL) == "unsatisfiable"


@pytest.mark.parametrize("header", [
    "items=0-10",     # unit we do not speak
    "bytes=abc",      # not numbers
    "bytes=",         # nothing after the unit
    "0-10",           # no unit
    "bytes=0-1,5-6",  # multi-range: legal to answer with the whole file
])
def test_malformed_headers_are_ignored_not_rejected(header):
    """RFC 7233: a Range header you cannot parse MUST be ignored.

    Returning 416 here would break clients that send something odd, when
    serving the whole file is always a valid response to a Range request.
    """
    assert parse_range(header, TOTAL) is None


def test_empty_file_is_unsatisfiable():
    assert parse_range("bytes=0-", 0) == "unsatisfiable"
