"""Industry trend RSS -> bronze_industry_trends.

Feeds verified live on 2026-08-30 rather than taken from documentation:

  McKinsey Insights     50 items, current      business and strategy
  IEEE Spectrum semis   25 items, current      semiconductors, the closest
                                               thing to NVDA's own industry
  MIT Tech Review AI    10 items, current      AI research and industry
  SemiAnalysis           4 items, STALE        latest post September 2025

DROPPED: a16z. SCOPE.md planned to pull it and to check whether the State of AI
report appeared in the feed. There is no feed - /feed/, /rss/, /rss.xml,
/blog/feed/, /index.xml and /posts/feed/ all return 404.

SemiAnalysis is kept because its subject is exactly right for this account, but
it has not published to RSS in a year. Everything downstream filters on
published date, so a stale feed contributes nothing rather than contributing
something old and wrong.

These sources are NOT account-specific. They describe the industry an account
sits in, which is what phase 3 of the briefing needs. Relevance to a given
account is decided later, at retrieval.
"""
import html
import re
import sys

FEEDS = {
    "mckinsey":     "https://www.mckinsey.com/insights/rss",
    "ieee_semis":   "https://spectrum.ieee.org/feeds/topic/semiconductors.rss",
    "mit_tech_ai":  "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "semianalysis": "https://semianalysis.com/feed/",
}

import os

# SEC and most feeds want a contact address in the User-Agent.
UA = f"account_signals/0.1 ({os.environ.get('SEC_CONTACT', 'contact@example.com')})"

SCHEMA = """
    source string, title string, url string, published_at string,
    summary string, author string
"""


def strip_html(text: str) -> str:
    """Feed summaries arrive as HTML. Chunking and embedding want plain text."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch(source: str, url: str):
    import feedparser

    parsed = feedparser.parse(url, request_headers={"User-Agent": UA})
    for e in parsed.entries:
        yield {
            "source": source,
            "title": strip_html(e.get("title", "")),
            "url": e.get("link", ""),
            # Keep the feed's own string. Formats vary between feeds and
            # normalising is a Silver concern.
            "published_at": e.get("published", e.get("updated", "")),
            "summary": strip_html(e.get("summary", e.get("description", ""))),
            "author": e.get("author", ""),
        }


def main() -> None:
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    rows = []
    for source, url in FEEDS.items():
        try:
            items = list(fetch(source, url))
            rows.extend(items)
            print(f"{source}: {len(items)} items")
        except Exception as e:
            # One dead feed should not fail the task. A source that stops
            # publishing shows up as zero rows, which is visible downstream.
            print(f"{source}: FAILED - {type(e).__name__}: {str(e)[:120]}")

    if not rows:
        raise RuntimeError("every trend feed failed; not writing an empty batch")

    df = spark().createDataFrame(rows, schema=SCHEMA)
    bronze_write(df, catalog, schema, "industry_trends")
    print(f"wrote {len(rows)} items to {catalog}.{schema}.bronze_industry_trends")


if __name__ == "__main__":
    main()
