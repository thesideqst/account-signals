"""Company news -> bronze_news.

Two feeds, both verified live 2026-08-30, both account-specific - unlike the
industry trends, which describe the sector rather than the company:

  Google News search RSS   query per account, ~100 items over 7 days
  Yahoo Finance headlines  per ticker, ~19 items

Seeking Alpha publishes a working per-ticker feed and is deliberately NOT used.
It was ruled out on terms-of-service grounds when we looked for transcripts,
and taking their headlines while declining their transcripts would be picking
whichever reading suited us.

WHAT THIS SOURCE CAN AND CANNOT DO
These feeds carry headlines and a sentence, not article bodies. Good for "what
happened and when", useless for depth. The briefing should use news to
establish that an event occurred and let the filings and the call supply the
substance. It is also what makes Mode B reachable: a news day with no filing
and no earnings call is a single-event episode.
"""
import html
import re
import sys
import urllib.parse

GOOGLE = ("https://news.google.com/rss/search?q={query}+when:7d"
          "&hl=en-US&gl=US&ceid=US:en")
YAHOO = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
         "?s={ticker}&region=US&lang=en-US")

# Query terms matter: bare "NVDA" returns little, the company name returns the
# world. Both are pinned per account rather than derived from the symbol.
ACCOUNTS = {
    "NVDA": {"ticker": "NVDA", "query": "NVIDIA"},
}

import os

# SEC and most feeds want a contact address in the User-Agent.
UA = f"account_signals/0.1 ({os.environ.get('SEC_CONTACT', 'contact@example.com')})"
SCHEMA = ("symbol string, source string, title string, url string, "
          "published_at string, summary string, publisher string")


def strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def fetch(symbol: str, source: str, url: str):
    import feedparser

    parsed = feedparser.parse(url, request_headers={"User-Agent": UA})
    for e in parsed.entries:
        yield {
            "symbol": symbol,
            "source": source,
            "title": strip_html(e.get("title", "")),
            "url": e.get("link", ""),
            "published_at": e.get("published", e.get("updated", "")),
            "summary": strip_html(e.get("summary", e.get("description", ""))),
            # Google News nests the outlet; Yahoo puts it flat or not at all.
            "publisher": (e.get("source", {}).get("title", "")
                          if isinstance(e.get("source"), dict)
                          else str(e.get("source", ""))),
        }


def main() -> None:
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    rows = []
    for symbol, cfg in ACCOUNTS.items():
        for source, url in (
            ("google_news", GOOGLE.format(query=urllib.parse.quote(cfg["query"]))),
            ("yahoo_finance", YAHOO.format(ticker=cfg["ticker"])),
        ):
            try:
                items = list(fetch(symbol, source, url))
                rows.extend(items)
                print(f"{symbol} {source}: {len(items)} items")
            except Exception as e:
                print(f"{symbol} {source}: FAILED - {type(e).__name__}: {str(e)[:120]}")

    if not rows:
        raise RuntimeError("every news feed failed; not writing an empty batch")

    df = spark().createDataFrame(rows, schema=SCHEMA)
    bronze_write(df, catalog, schema, "news")
    print(f"wrote {len(rows)} items to {catalog}.{schema}.bronze_news")


if __name__ == "__main__":
    main()
