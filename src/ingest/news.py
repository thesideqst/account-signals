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

OPTIONAL FULL-BODY EXTRACTION (news_extract.py) - CURRENTLY A NO-OP
Each kept item is passed to `news_extract.maybe_extract_body()`, which fetches
and extracts the article body ONLY for a publisher named in that module's
`ALLOWED_PUBLISHERS` set. That set is empty, so this is a no-op today: `body`
is NULL on every row and behaviour is unchanged from before this existed.
Populating the allowlist is a per-publisher terms-of-service decision left for
a human to make later - see news_extract.py's module docstring for why.
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
# `body` is nullable and additive - NULL means "no full text", never "no
# item". It never overwrites `summary`, which is what falls back to when body
# is absent (i.e. every row today, since ALLOWED_PUBLISHERS is empty).
SCHEMA = ("symbol string, source string, title string, url string, "
          "published_at string, summary string, publisher string, "
          "body string")


def strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def fetch(symbol: str, source: str, url: str, terms):
    import re

    import feedparser
    import news_extract

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
        link = e.get("link", "")
        # Google News nests the outlet; Yahoo puts it flat or not at all.
        publisher = (e.get("source", {}).get("title", "")
                     if isinstance(e.get("source"), dict)
                     else str(e.get("source", "")))
        # No-op unless `publisher` is in news_extract.ALLOWED_PUBLISHERS
        # (empty today). Any failure - timeout, non-200, paywall, parse error
        # - is caught inside maybe_extract_body and returns None there, same
        # catch-log-continue convention as the per-feed try/except in main():
        # one bad article fetch must not fail the whole news ingest task.
        body = news_extract.maybe_extract_body(link, publisher)
        yield {
            "symbol": symbol,
            "source": source,
            "title": title,
            "url": link,
            "published_at": e.get("published", e.get("updated", "")),
            "summary": summary,
            "publisher": publisher,
            "body": body,
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
