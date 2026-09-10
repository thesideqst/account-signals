"""Full-article extraction for news.py -> optional `body` on bronze_news.

BUILT, BUT DISABLED. See ALLOWED_PUBLISHERS immediately below.

news.py's two RSS feeds carry a headline and a one-sentence summary, not the
article body: SCOPE.md's 2026-09-01 audit found 410 of 430 news chunks at 300
characters or fewer, and traced every fabricated figure it found back to one
of those stubs. This module is the fetch-and-extract half of the fix
described in SCOPE.md's backlog under "Full-article news, not headlines and
teasers": given a kept RSS item, follow its `url`, fetch the page, and pull
out the readable body text with a readability-style extractor (trafilatura).

WHY IT DOES NOTHING TODAY
Fetching an article's full text from a publisher's own site is a materially
different act from reading the summary that publisher chooses to put in its
RSS feed. This project already ruled Seeking Alpha out on exactly this
ground when it went looking for transcripts (see news.py's module docstring
and SCOPE.md, 2026-08-30) - and SCOPE.md's backlog entry for this feature is
explicit that taking a publisher's headlines while scraping their article
bodies without a per-publisher decision would be picking whichever reading
suits us. (Separately, and after this backlog item was written, the project
also added src/ingest/filings.py, which solves the same "stub" problem for a
company's OWN press releases by reading its SEC 8-K exhibits instead of
scraping anyone's website - see that file's "WHY FILINGS RATHER THAN
SCRAPING ARTICLE BODIES" docstring section. That covers company-issued news.
It does not cover third-party journalism and analysis, which is what this
module is for and which still arrives as an RSS stub today.)

So every actual HTTP fetch of an article body is gated behind
ALLOWED_PUBLISHERS, a per-publisher allowlist that starts, and stays, EMPTY
in this change. Deciding to populate it - for any single publisher - is a
ToS/business call for a human to make, publisher by publisher, not an
engineering default to flip on because the plumbing exists. With an empty
allowlist, `maybe_extract_body()` always returns None before making any
network call, and the pipeline behaves exactly as it does today: every item
stays headline/summary-only, and synthesize.py's `KIND: HEADLINE ONLY`
labelling is unaffected. See ALLOWED_PUBLISHERS itself, just below the
imports.

PAYWALL / CONSENT-WALL DETECTION
A page that returns 200 is not the same as a page that handed over the
article. Many outlets serve a short preview, a cookie-consent interstitial,
or a "subscribe to continue" wall at the article's own URL, and a readability
extractor will happily return that boilerplate as if it were the body. A
false "success" here is worse than no extraction at all: it would silently
reintroduce the stub-masquerading-as-substance problem this feature exists to
remove, except now labelled ARTICLE instead of HEADLINE ONLY. See
`looks_like_paywall_or_stub()` for the (deliberately conservative) checks.
"""
import re

# SEC and most feeds want a contact address in the User-Agent; match news.py's
# own UA string rather than inventing a second one.
import os

UA = f"account_signals/0.1 ({os.environ.get('SEC_CONTACT', 'contact@example.com')})"

# THE GATE. Every real fetch in this module happens only for a publisher
# named here, and this set is deliberately empty - see the module docstring
# above for why. Populating it is a per-publisher ToS decision for a human to
# make later, not something this change decides.
#
# ALLOWED_PUBLISHERS = {"Reuters", "AP"}  # example only, not enabled -
# adding a publisher here is a per-publisher ToS decision, not something
# this change makes on its own.
ALLOWED_PUBLISHERS = set()

# Below this many characters, extracted text is treated as a failed
# extraction regardless of content - too short to be a real article body, and
# not meaningfully better than the summary already on the row. Set well above
# synthesize.py's STUB_CHARS (300): the point of a body is to clear that bar
# by a wide margin, not scrape by it.
MIN_BODY_CHARS = 500

# Below this many characters, a paywall/consent phrase match is treated as
# conclusive (the page is mostly boilerplate). At or above it, a single
# incidental phrase match (e.g. a long real article that happens to mention
# "subscribe" once) is not enough on its own to discard genuine text - the
# length is doing most of the work by that point anyway.
PAYWALL_PHRASE_SAFE_CHARS = 2000

# Common paywall / consent-wall / bot-check boilerplate. Deliberately broad -
# a false "this is a real article" is the failure mode this feature exists to
# avoid, so err toward treating a match as a failed extraction rather than
# toward keeping content that might be a wall.
_PAYWALL_PHRASES = [
    r"enable javascript",
    r"disable (?:your )?ad[\s-]?blocker",
    r"subscribe (?:now )?to (?:continue|read|access)",
    r"sign in to (?:continue|read)",
    r"log in to (?:continue|read)",
    r"create a free account to continue",
    r"you(?:'|’)ve reached your (?:free )?article limit",
    r"you have reached your (?:free )?(?:article|story) limit",
    r"this (?:content|article|page) is not available in your (?:region|country)",
    r"we use cookies",
    r"accept (?:all )?cookies",
    r"manage (?:your )?(?:cookie|privacy) (?:preferences|settings)",
    r"consent to the (?:use of )?cookies",
    r"already a subscriber",
    r"start your (?:free )?trial",
    r"verify you are human",
    r"checking your browser",
    r"unusual traffic (?:from|detected)",
    r"access (?:to this page has been )?denied",
    r"this site uses cookies",
]
_PAYWALL_RE = [re.compile(p, re.I) for p in _PAYWALL_PHRASES]


def looks_like_paywall_or_stub(text) -> bool:
    """True if extracted text should NOT be trusted as a real article body.

    Conservative on purpose: this is the guard that stops a paywall's own
    boilerplate from being filed as ARTICLE-grade text and elaborated on as
    if it were substance - exactly the failure this whole feature exists to
    fix, just moved one layer down. Two checks, either one is disqualifying:

    1. Too short outright (under MIN_BODY_CHARS) - most paywalls give away a
       teaser paragraph or two before the wall, which reads as plausible
       prose but is not the article.
    2. Boilerplate phrase present, AND the text is still under
       PAYWALL_PHRASE_SAFE_CHARS - long enough that a single incidental
       phrase match stops being conclusive, short enough that it still could
       be one.
    """
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) < MIN_BODY_CHARS:
        return True
    if len(stripped) < PAYWALL_PHRASE_SAFE_CHARS:
        for rx in _PAYWALL_RE:
            if rx.search(stripped):
                return True
    return False


def _fetch_and_extract(url: str, timeout: float = 10.0):
    """Fetch `url` and pull the readable body text out of it, or None.

    Imports its dependencies locally (not at module level) so the rest of
    this module - the allowlist gate and the paywall check - stays importable
    and testable without `requests`/`trafilatura` installed, matching how
    every other src/ingest/*.py module keeps third-party imports inside
    functions rather than at the top of the file (see src/ingest/CLAUDE.md).
    """
    import requests
    import trafilatura

    resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    if resp.status_code != 200:
        return None
    return trafilatura.extract(
        resp.text, url=url, favor_precision=True, include_comments=False,
        include_tables=False,
    )


def maybe_extract_body(url: str, publisher: str):
    """The entry point news.py calls per kept item. Returns text or None.

    Gated on ALLOWED_PUBLISHERS FIRST, before any network call: with the
    allowlist empty (as it is in this change) this returns None immediately
    for every item, and nothing downstream of this function's early return
    ever runs a fetch. Any failure past the gate - timeout, non-200, a parse
    error, an unexpected exception from the extraction library - is caught
    here and logged, the same per-item catch-log-continue convention
    news.py's own `fetch()` already uses for a bad feed: one bad article
    fetch must not fail the whole news ingest task.
    """
    if not url or not publisher or publisher not in ALLOWED_PUBLISHERS:
        return None
    try:
        text = _fetch_and_extract(url)
    except Exception as e:
        print(f"  extract FAILED for {url[:80]}: {type(e).__name__}: {str(e)[:120]}")
        return None
    if looks_like_paywall_or_stub(text):
        print(f"  extract skipped (too short or paywall-like) for {url[:80]}")
        return None
    return text.strip()
