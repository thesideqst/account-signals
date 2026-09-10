"""news_extract.py: the allowlist gate, and the paywall/stub guard.

Imported directly rather than lifted out with ast (contrast test_grounding.py,
test_filings.py): news_extract.py keeps `requests` and `trafilatura` inside
the one function that needs them (see its own module docstring), so the
module itself has no network or Spark dependency at import time and is safe
to import as-is, the same way tests/test_reported_date.py imports prompts.py
directly.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "ingest"))
import news_extract  # noqa: E402


def test_allowlist_is_empty():
    """The one assertion that matters most in this file. Populating
    ALLOWED_PUBLISHERS is a per-publisher ToS decision for a human, not
    something this codebase decides for itself - see news_extract.py's
    module docstring. If this test ever fails, a publisher was added
    somewhere it should not have been added silently."""
    assert news_extract.ALLOWED_PUBLISHERS == set()


def test_maybe_extract_body_is_a_noop_for_every_publisher_today():
    """With the allowlist empty, every publisher - including one that looks
    legitimate - must short-circuit before any network call. If this made a
    real HTTP request it would hang or fail in a network-sandboxed test
    environment; it must not even try."""
    for publisher in ["Reuters", "AP", "Bloomberg", "", "Some Random Blog"]:
        assert news_extract.maybe_extract_body(
            "https://example.com/article", publisher) is None


def test_maybe_extract_body_handles_missing_url_or_publisher():
    assert news_extract.maybe_extract_body("", "Reuters") is None
    assert news_extract.maybe_extract_body("https://example.com/a", "") is None
    assert news_extract.maybe_extract_body("https://example.com/a", None) is None


def test_empty_or_none_text_is_treated_as_failed_extraction():
    assert news_extract.looks_like_paywall_or_stub(None) is True
    assert news_extract.looks_like_paywall_or_stub("") is True
    assert news_extract.looks_like_paywall_or_stub("   ") is True


def test_short_text_is_treated_as_failed_even_with_no_paywall_language():
    """A short extraction is not automatically a paywall, but it is not
    trustworthy as an article body either - real prose, just not enough of
    it to be better than the summary already on the row."""
    short = "The company announced a new product today. Shares rose slightly."
    assert len(short) < news_extract.MIN_BODY_CHARS
    assert news_extract.looks_like_paywall_or_stub(short) is True


def test_long_clean_text_is_accepted():
    body = ("The quarter beat expectations on every major line. " * 40).strip()
    assert len(body) >= news_extract.MIN_BODY_CHARS
    assert news_extract.looks_like_paywall_or_stub(body) is False


def test_short_paywall_boilerplate_is_rejected_on_length_alone():
    """The most common real case: a two-paragraph teaser and a subscribe
    wall, well under MIN_BODY_CHARS. Caught by the length check before the
    phrase check is even reached."""
    teaser = (
        "Shares of the company moved after a report on production plans. "
        "Analysts had mixed reactions to the announcement, with some citing "
        "supply concerns. To keep reading this article, subscribe now to "
        "continue with unlimited access to our coverage and analysis."
    )
    assert len(teaser) < news_extract.MIN_BODY_CHARS
    assert news_extract.looks_like_paywall_or_stub(teaser) is True


def test_paywall_phrase_rejects_text_that_clears_the_length_bar_alone():
    """The check the length bar alone would miss: enough padding to clear
    MIN_BODY_CHARS, but still mostly-boilerplate and under
    PAYWALL_PHRASE_SAFE_CHARS - the phrase match has to do the real work
    here, not just the length."""
    teaser = (
        "Shares of the company moved after a report on production plans, "
        "with trading volume well above the recent daily average as investors "
        "weighed the announcement against the broader sector's performance "
        "this week. Analysts had mixed reactions, with some citing supply "
        "concerns and others pointing to steady long-term demand across the "
        "industry as a whole, according to people familiar with the matter. "
        "To keep reading this article, subscribe now to continue with "
        "unlimited access to our coverage and analysis."
    )
    assert news_extract.MIN_BODY_CHARS <= len(teaser) < news_extract.PAYWALL_PHRASE_SAFE_CHARS
    assert news_extract.looks_like_paywall_or_stub(teaser) is True


def test_incidental_phrase_in_a_long_real_article_is_not_conclusive():
    """A single coincidental phrase match in an article that is clearly long
    enough to be real should not discard genuine text - length does most of
    the work once a piece is unambiguously substantial."""
    long_article = (
        "The company's quarterly results showed strong momentum across "
        "every segment. " * 60
        + "One executive noted that customers increasingly expect firms to "
        "accept cookies and manage their own privacy settings when visiting "
        "the company's site, a minor aside in an otherwise unrelated interview. "
        + "The call continued with detailed remarks on capital allocation. " * 20
    )
    assert len(long_article) >= news_extract.PAYWALL_PHRASE_SAFE_CHARS
    assert news_extract.looks_like_paywall_or_stub(long_article) is False


def test_consent_wall_language_is_recognised():
    consent = (
        "This site uses cookies to improve your experience. "
        "We use cookies to personalize content and ads. "
        "Please accept all cookies to continue to the article you requested."
    )
    assert news_extract.looks_like_paywall_or_stub(consent) is True
