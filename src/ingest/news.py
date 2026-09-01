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
# `terms` is the relevance test: an item is kept only if one of these appears in
# its title or summary. Yahoo's per-ticker feed turned out to return general
# market news rather than news about the ticker - an NVIDIA request came back
# with Hershey and Warren Buffett headlines - so the feed cannot be trusted to
# have filtered anything.
ACCOUNTS = {
    "NVDA": {"ticker": "NVDA", "query": "NVIDIA",
             "terms": ["nvidia", "nvda", "jensen huang"]},
    "GOOG": {"ticker": "GOOG", "query": "Alphabet+Google",
             "terms": ["alphabet", "google", "goog", "sundar pichai", "deepmind"]},
    "MU":   {"ticker": "MU",   "query": "Micron",
             # RAW string. Written as "\bmu\b" this is a backspace character,
             # not a word boundary, so the branch never matched and MU's filter
             # was effectively "micron" only - any headline saying just "MU"
             # was dropped at ingest and is unrecoverable from Bronze.
             "terms": ["micron", r"\bmu\b"]},
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


def fetch(symbol: str, source: str, url: str, terms):
    import re

    import feedparser

    pattern = re.compile("|".join(terms), re.I) if terms else None
    parsed = feedparser.parse(url, request_headers={"User-Agent": UA})
    kept = dropped = 0
    for e in parsed.entries:
        title = strip_html(e.get("title", ""))
        summary = strip_html(e.get("summary", e.get("description", "")))
        # The company has to actually be mentioned. Without this the episode
        # cites articles about other companies entirely.
        if pattern and not pattern.search(f"{title} {summary}"):
            dropped += 1
            continue
        kept += 1
        yield {
            "symbol": symbol,
            "source": source,
            "title": title,
            "url": e.get("link", ""),
            "published_at": e.get("published", e.get("updated", "")),
            "summary": summary,
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
                items = list(fetch(symbol, source, url, cfg.get("terms")))
                rows.extend(items)
                print(f"{symbol} {source}: {len(items)} items kept "
                      f"(items not mentioning the company are dropped)")
            except Exception as e:
                print(f"{symbol} {source}: FAILED - {type(e).__name__}: {str(e)[:120]}")

    if not rows:
        raise RuntimeError("every news feed failed; not writing an empty batch")

    df = spark().createDataFrame(rows, schema=SCHEMA)
    bronze_write(df, catalog, schema, "news")
    print(f"wrote {len(rows)} items to {catalog}.{schema}.bronze_news")


if __name__ == "__main__":
    main()
