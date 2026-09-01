"""The news relevance filter, and the two copies of it staying in step.

The same term lists exist twice: in src/ingest/news.py, which stops off-topic
articles landing in Bronze, and in src/pipelines/chunk_and_embed.py, which
stops the ones that landed BEFORE the ingest filter existed from being
retrieved. Bronze is append-only, so fixing ingest does not retire old rows -
both filters are needed. They cannot share an import (the DLT pipeline and the
ingest tasks have different import paths), so this test is what keeps them
honest.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NEWS = ROOT / "src" / "ingest" / "news.py"
CHUNK = ROOT / "src" / "pipelines" / "chunk_and_embed.py"


def ingest_terms():
    """ACCOUNTS[sym]["terms"] from news.py, without importing it."""
    tree = ast.parse(NEWS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACCOUNTS":
            accounts = ast.literal_eval(node.value)
            return {k: v["terms"] for k, v in accounts.items()}
    raise AssertionError("ACCOUNTS not found in news.py")


def pipeline_patterns():
    """ACCOUNT_TERMS from chunk_and_embed.py, without importing dlt."""
    tree = ast.parse(CHUNK.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACCOUNT_TERMS":
            return ast.literal_eval(node.value)
    raise AssertionError("ACCOUNT_TERMS not found in chunk_and_embed.py")


def test_the_two_filters_have_not_drifted():
    ingest = {k: "|".join(v) for k, v in ingest_terms().items()}
    assert ingest == pipeline_patterns()


def test_mu_uses_a_real_word_boundary():
    """Written as a plain string, "\\bmu\\b" is a backspace character, not a
    word boundary, so the branch never matched and MU's filter was
    effectively "micron" only."""
    terms = ingest_terms()["MU"]
    assert "\x08" not in "".join(terms), "\\b was written without a raw string"
    pattern = re.compile("|".join(terms), re.I)
    assert pattern.search("MU stock rises")
    assert pattern.search("Micron beats estimates")
    # The boundary has to actually exclude these, or the filter is noise.
    assert not pattern.search("Much ado about nothing")
    assert not pattern.search("the community responded")
    assert not pattern.search("AMU index climbs")


@pytest.mark.parametrize("symbol,headline,keep", [
    # Real off-topic rows found in bronze_news, filed under the wrong account.
    ("GOOG", "SK Hynix CEO Warns Memory Shortage Will Persist Through 2030", False),
    ("GOOG", "Billionaire Stanley Druckenmiller Sold Broadcom", False),
    ("NVDA", "Is It Too Late to Buy Moderna Stock After Its 127% Surge?", False),
    ("NVDA", "Dow Jones Futures Fall, Oil Prices Pop As U.S. Strikes Iran", False),
    ("MU", "Prediction: Micron Stock Will Go Parabolic After Sept. 30", True),
    ("NVDA", "Nvidia's $96 billion quarter revealed a surprising constraint", True),
    ("GOOG", "Congressman Sold Alphabet Stock Three Months After Buying It", True),
])
def test_real_rows_are_kept_or_dropped_correctly(symbol, headline, keep):
    pattern = re.compile("|".join(ingest_terms()[symbol]), re.I)
    assert bool(pattern.search(headline)) is keep
