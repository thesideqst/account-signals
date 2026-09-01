"""The opening must not claim recency the filing date does not support.

prompts.build is imported directly - prompts.py has no Databricks imports.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "briefing"))
import prompts  # noqa: E402

BASE = dict(mode="A", account="GOOG", deltas="- revenue: $1", framing="")


def test_a_stale_quarter_is_told_not_to_claim_recency():
    """Alphabet's quarter was 40 days old and the script still said
    'just posted'."""
    p = prompts.build(reported_on="2026-07-23", reported_days_ago=40, **BASE)
    assert "IT IS NOT NEWS" in p
    assert "2026-07-23" in p and "40 day(s) ago" in p
    assert 'Do not say "just posted"' in p


def test_a_fresh_quarter_may_be_treated_as_news():
    p = prompts.build(reported_on="2026-08-26", reported_days_ago=6, **BASE)
    assert "genuinely recent" in p
    assert "IT IS NOT NEWS" not in p


def test_the_boundary_is_seven_days():
    assert "IT IS NOT NEWS" not in prompts.build(
        reported_on="2026-08-25", reported_days_ago=7, **BASE)
    assert "IT IS NOT NEWS" in prompts.build(
        reported_on="2026-08-24", reported_days_ago=8, **BASE)


def test_an_unknown_filing_date_adds_no_instruction_at_all():
    """Silence beats a claim in either direction when the date is missing."""
    p = prompts.build(**BASE)
    assert "IT IS NOT NEWS" not in p and "genuinely recent" not in p


def test_the_show_is_named_in_the_opening():
    p = prompts.build(**BASE)
    assert prompts.SHOW_NAME in p
    assert f"This is {prompts.SHOW_NAME}." in p
