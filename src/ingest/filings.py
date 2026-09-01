"""SEC 8-K filings and their exhibits -> bronze_filing_documents.

WHY THIS EXISTS
The news feeds carry headlines, not articles. Measured across silver_doc_chunks:
Google News and Yahoo Finance average 186 characters and top out at 620, while
the industry feeds average 2,649 and transcripts 1,081. A 186-character stub is
a headline plus a truncated teaser, and every fabricated figure the 2026-09-01
audit found sat on one - a 325-character Yahoo teaser became an invented 1999
IPO date and an invented $200,000 figure, both credited to the article by name.

The grounding rule cannot hold when the grounding material is a headline. The
model is being handed a title and asked to narrate its significance, which is a
request to speculate.

WHY FILINGS RATHER THAN SCRAPING ARTICLE BODIES
Fetching publisher article text is a different act from reading the summary
they publish in a feed, and this project already ruled Seeking Alpha out on
terms-of-service grounds when it went looking for transcripts (SCOPE.md,
2026-08-30). That reasoning has to extend here rather than be quietly dropped
because the data would be useful.

SEC filings have no such problem. They are public domain, they are the
company's own words filed under legal liability, and the fair-access policy
asks only for identification and a rate limit - both of which edgar.py already
honours. An 8-K is literally the form a company uses to announce news.

Measured on NVDA's recent 8-Ks:
    earnings press release (EX-99.1)   22,347 characters
    CFO commentary (EX-99.2)           19,722
    partnership announcement (EX-99.1)  9,859
against 186 for a news chunk.

WHAT IS COLLECTED
8-K only - the news form. The cover page carries the item codes and a short
description; the substance is in the EX-99 exhibits, which are the press
releases themselves. Both are kept.

R*.htm files are skipped: they are XBRL viewer renderings of the cover page,
not content, and they would land near-duplicate boilerplate on every filing.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}"

# Same three accounts as edgar.py. Kept here rather than imported so this task
# can run alone, matching how every other ingest module works.
ACCOUNTS = {
    "NVDA": "0001045810",
    "GOOG": "0001652044",
    "MU":   "0000723125",
}

# What an 8-K item code means, so the chunk can say why the filing exists
# instead of making the model infer it from a number.
ITEM_MEANINGS = {
    "1.01": "entry into a material agreement",
    "1.02": "termination of a material agreement",
    "2.02": "results of operations and financial condition",
    "2.03": "creation of a direct financial obligation",
    "3.02": "unregistered sale of equity securities",
    "5.02": "a change of directors or principal officers",
    "5.07": "results of a shareholder vote",
    "7.01": "Regulation FD disclosure",
    "8.01": "other events the company chose to report",
    "9.01": "financial statements and exhibits",
}

LOOKBACK_DAYS = 120      # matches the retrieval window for industry material
MAX_FILINGS = 8          # per account, newest first
MIN_CHARS = 400          # below this it is a cover page stub, not content
SEC_CONTACT = os.environ.get("SEC_CONTACT", "")

SCHEMA = ("symbol string, cik string, form string, filed_date string, "
          "accession string, document string, exhibit_type string, "
          "items string, item_description string, title string, text string")


def _get(url: str) -> bytes:
    if not SEC_CONTACT:
        raise RuntimeError(
            "SEC contact is unset. SEC fair-access policy requires a User-Agent "
            "carrying a contact address; requests without one are throttled or "
            "blocked. Pass it as the third task parameter, or set SEC_CONTACT.")
    req = urllib.request.Request(
        url, headers={"User-Agent": f"account_signals/0.1 ({SEC_CONTACT})"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()
    # SEC allows 10 requests/second. This module makes roughly a dozen per
    # account, so the ceiling is not a real constraint, but the pause below
    # keeps it comfortably inside anyway.


def strip_html(raw: str) -> str:
    """Filing HTML -> readable text.

    EDGAR documents are tables and inline styles wrapped around prose. Scripts
    and styles go first so their contents do not survive as text, then tags,
    then entities. Whitespace is collapsed last because the tag removal leaves
    a great deal of it.
    """
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def clean(text: str) -> str:
    """Drop the EDGAR wrapper that precedes the actual document.

    Exhibits begin with a machine header naming the exhibit, its sequence
    number and its filename, sometimes several times over:

        EX-99.1 5 d83560dex991.htm EX-99.1 EX-99.1 Exhibit 99.1 Alphabet ...
        EX-99.1 2 a2026q3ex991-pressrelease.htm EX-99.1 - PRESS RELEASE ...

    Left in place it is the first thing the model reads, and it looks like
    content. The tokens are stripped one at a time from the front rather than
    with a single pattern, because their number and order vary by filer.
    """
    junk = re.compile(
        r"^(?:EX-[\d.]+|EXHIBIT\s+[\d.]+|Exhibit\s+[\d.]+|Document|"
        r"\d{1,3}|\S+\.html?|-|PRESS\s+RELEASE)\s+", re.I)
    prev = None
    while prev != text:
        prev = text
        text = junk.sub("", text, count=1)
    # The cover page repeats the SEC's own address block on every filing.
    text = re.sub(r"UNITED STATES SECURITIES AND EXCHANGE COMMISSION\s+"
                  r"WASHINGTON,?\s+D\.?C\.?\s+20549\s*_*", "", text, flags=re.I)
    return text.strip()


def describe(items: str) -> str:
    """Item codes -> what the filing is actually about, in words."""
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    known = [ITEM_MEANINGS[c] for c in codes if c in ITEM_MEANINGS]
    return "; ".join(known)


def recent_8ks(symbol: str, cik: str):
    """Newest 8-K filings for one company, inside the lookback window."""
    from datetime import date, timedelta

    payload = json.loads(_get(SUBMISSIONS.format(cik=cik)))
    r = payload.get("filings", {}).get("recent", {})
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    out = []
    for form, filed, acc, doc, items in zip(
            r.get("form", []), r.get("filingDate", []),
            r.get("accessionNumber", []), r.get("primaryDocument", []),
            r.get("items", [])):
        if form != "8-K" or filed < cutoff:
            continue
        out.append({"form": form, "filed": filed, "accession": acc,
                    "primary": doc, "items": items})
        if len(out) >= MAX_FILINGS:
            break
    return out


def exhibit_types(base: str, accession: str) -> dict:
    """document name -> exhibit type (EX-99.1, EX-4.2 ...) from the index page.

    index.json carries a `type` field, but it is the name of the icon EDGAR
    draws next to the row - "text.gif" for everything - so it cannot be used.
    The human-facing index page has the real table.
    """
    raw = _get(f"{base}/{accession}-index.htm").decode("utf-8", "replace")
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S | re.I):
        cells = [re.sub(r"\s+", " ",
                        html.unescape(re.sub(r"(?s)<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) >= 4 and cells[2].lower().endswith((".htm", ".html")):
            out[cells[2]] = cells[3]
    return out


def documents(symbol: str, cik: str, filing: dict):
    """The readable documents in one filing: the cover page and EX-99 exhibits.

    EXHIBIT TYPE IS THE FILTER, and it has to be. Alphabet's 2026-08-10 bond
    offering attached ELEVEN "GLOBAL SECURITY" exhibits - EX-4.2 through
    EX-4.11 - at 26,000 to 38,000 characters each. That is roughly 300,000
    characters of indenture boilerplate from a single filing, which would
    swamp retrieval for that account and teach a rep nothing.

    EX-99 is the press-release class: "NVIDIA Announces Financial Results",
    "Alphabet Announces Second Quarter 2026 Results", "Micron Announces
    Leadership Appointments". EX-4 is securities, EX-5 legal opinions, EX-10
    material contracts - all real documents, none of them news.
    """
    base = ARCHIVE.format(cik_int=str(int(cik)),
                          acc_nodash=filing["accession"].replace("-", ""))
    try:
        types = exhibit_types(base, filing["accession"])
    except Exception as e:
        print(f"    index unreadable ({type(e).__name__}); cover page only")
        types = {}

    index = json.loads(_get(base + "/index.json"))
    names = [i["name"] for i in index["directory"]["item"]
             if i["name"].lower().endswith((".htm", ".html"))]
    for name in names:
        # EDGAR ships its own machinery alongside the filing, and all of it is
        # HTML so none of it is excluded by extension:
        #   R1.htm, R2.htm ...      XBRL viewer renderings of the cover page
        #   <accession>-index.htm   the "EDGAR Filing Documents for ..." page
        #   <accession>.txt         the raw submission header
        # Landing any of them repeats boilerplate on every single filing and,
        # worse, reads like content to a model that was handed it.
        if re.match(r"^R\d+\.htm", name, re.I):
            continue
        if name.lower().startswith(filing["accession"].lower()):
            continue

        is_primary = name == filing["primary"]
        etype = types.get(name, "")
        if not is_primary and not etype.upper().startswith("EX-99"):
            continue

        try:
            text = clean(strip_html(_get(f"{base}/{name}").decode("utf-8", "replace")))
        except Exception as e:
            print(f"    {name}: {type(e).__name__} - skipped")
            continue
        if len(text) < MIN_CHARS:
            continue
        # Belt and braces: catch EDGAR wrappers by what they SAY, not only by
        # what they are called, since the naming is not guaranteed.
        if text[:60].startswith(("SEC EDGAR Submission",
                                 "EDGAR Filing Documents for")):
            continue
        yield {
            "symbol": symbol,
            "cik": cik,
            "form": filing["form"],
            "filed_date": filing["filed"],
            "accession": filing["accession"],
            "document": name,
            "exhibit_type": "cover" if is_primary else (etype or "exhibit"),
            "items": filing["items"] or "",
            "item_description": describe(filing["items"]),
            # The opening line of a press release IS its headline.
            "title": text[:180],
            "text": text,
        }
        time.sleep(0.2)


def main() -> None:
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    global SEC_CONTACT
    if len(sys.argv) > 3 and sys.argv[3]:
        SEC_CONTACT = sys.argv[3]

    rows, failed = [], []
    for symbol, cik in ACCOUNTS.items():
        try:
            filings = recent_8ks(symbol, cik)
            n_before = len(rows)
            for f in filings:
                rows.extend(documents(symbol, cik, f))
            print(f"{symbol}: {len(filings)} 8-K(s), "
                  f"{len(rows) - n_before} document(s)")
        except Exception as e:
            code = getattr(e, "code", "")
            print(f"{symbol}: FAILED {type(e).__name__} {code} - continuing")
            failed.append(f"{symbol} ({type(e).__name__} {code})")

    # A partial pull is the failure that looks like success, so say it loudly
    # and fail outright only when the whole source is down.
    if failed:
        print(f"FILINGS WARNING - no documents for {len(failed)} of "
              f"{len(ACCOUNTS)} account(s): {'; '.join(failed)}")
    if len(failed) == len(ACCOUNTS):
        raise RuntimeError(f"filings ingest got nothing: {'; '.join(failed)}")

    if rows:
        chars = sum(len(r["text"]) for r in rows)
        print(f"{len(rows)} document(s), {chars:,} characters "
              f"(news chunks average 186)")
        bronze_write(spark().createDataFrame(rows, schema=SCHEMA),
                     catalog, schema, "filing_documents")
        print(f"wrote {len(rows)} document(s) to "
              f"{catalog}.{schema}.bronze_filing_documents")


if __name__ == "__main__":
    main()
