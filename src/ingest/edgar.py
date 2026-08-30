"""SEC EDGAR XBRL company facts -> bronze_xbrl_facts.

This is the load-bearing source. EDGAR tags every reported value with a
machine-readable concept, so a quarter-over-quarter delta is arithmetic rather
than interpretation. No language model reads a filing here, and none should:
summarizing filing prose would rebuild the management-framing problem this
project exists to strip out.

ENDPOINT
    GET https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json

One call returns every tagged fact the company has ever filed, across every
taxonomy. That is a lot of JSON (tens of thousands of facts), but it is a
single request per company, and Bronze wants breadth rather than a guess about
which concepts matter later.

CIK must be zero-padded to ten digits. "1045810" 404s; "0001045810" works.

SEC FAIR ACCESS
SEC requires a User-Agent identifying the requester with a contact address, and
throttles or blocks requests without one. Limit is 10 requests/second; this
module makes one per account, so the ceiling is irrelevant — but the header is
not optional.

BRONZE SHAPE
The response nests taxonomy -> concept -> units -> [facts]. This flattens it to
one row per fact and lands everything: no concept filter, no form filter, no
unit filter. Choosing which concepts matter is a Silver decision, and filtering
here would mean re-downloading when that choice changes.
"""
import gzip
import json
import os
import sys
import urllib.request

BASE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Vertical slice: one account, end to end, before breadth (SCOPE.md build order).
ACCOUNTS = {
    "NVDA": "0001045810",
}

# SEC requires a real contact address. Supplied as the third task parameter on
# Databricks; falls back to SEC_CONTACT for local runs.
SEC_CONTACT = os.environ.get("SEC_CONTACT", "")


def fetch_company_facts(cik: str) -> dict:
    """One company's full fact set. Raises on a missing contact address."""
    if not SEC_CONTACT:
        raise RuntimeError(
            "SEC contact is unset. SEC fair-access policy requires a User-Agent "
            "carrying a contact address; requests without one are throttled or "
            "blocked. Pass it as the third task parameter, or set SEC_CONTACT."
        )
    req = urllib.request.Request(
        BASE.format(cik=cik),
        headers={
            "User-Agent": f"account_signals/0.1 ({SEC_CONTACT})",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        # urllib advertises gzip but never decompresses it, so do it here.
        # The payload is several MB, so it is worth requesting compressed.
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw)


def flatten(symbol: str, payload: dict):
    """Nested company-facts JSON -> flat fact rows.

    Yields every fact under every taxonomy and unit. `end` is the period end
    date; `start` is present for duration concepts (revenue over a quarter) and
    absent for instant ones (cash on a balance-sheet date). That presence or
    absence is how Silver tells a flow from a stock, so both are preserved.
    """
    cik = payload.get("cik")
    entity = payload.get("entityName")
    for taxonomy, concepts in (payload.get("facts") or {}).items():
        for concept, body in concepts.items():
            label = body.get("label")
            for unit, entries in (body.get("units") or {}).items():
                for e in entries:
                    yield {
                        "symbol": symbol,
                        "cik": cik,
                        "entity_name": entity,
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "label": label,
                        "unit": unit,
                        "period_start": e.get("start"),
                        "period_end": e.get("end"),
                        "value": (None if e.get("val") is None
                                  else float(e.get("val"))),
                        "fiscal_year": e.get("fy"),
                        "fiscal_period": e.get("fp"),
                        "form": e.get("form"),
                        "filed": e.get("filed"),
                        "accession": e.get("accn"),
                        "frame": e.get("frame"),
                    }


# Declared, not inferred. Across 27k facts `val` arrives as both int and float,
# and Spark's inference refuses to merge LongType with DoubleType. An explicit
# schema also means a provider changing a field's type fails loudly here rather
# than silently re-typing a Bronze column later.
SCHEMA = """
    symbol string, cik long, entity_name string, taxonomy string,
    concept string, label string, unit string,
    period_start string, period_end string, value double,
    fiscal_year int, fiscal_period string, form string,
    filed string, accession string, frame string
"""


def main() -> None:
    global SEC_CONTACT
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    if len(sys.argv) > 3 and sys.argv[3]:
        SEC_CONTACT = sys.argv[3]

    rows = []
    for symbol, cik in ACCOUNTS.items():
        rows.extend(flatten(symbol, fetch_company_facts(cik)))
        print(f"{symbol}: {len(rows)} facts so far")

    df = spark().createDataFrame(rows, schema=SCHEMA)
    bronze_write(df, catalog, schema, "xbrl_facts")
    print(f"wrote {len(rows)} facts to {catalog}.{schema}.bronze_xbrl_facts")


if __name__ == "__main__":
    main()
