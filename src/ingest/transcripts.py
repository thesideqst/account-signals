"""Roic AI earnings call transcripts -> bronze_transcript_turns.

This is the counterweight to the XBRL path. The measured numbers say what
happened; the call says how management chose to describe it. The briefing's
whole value is the gap between those two, so this source is load-bearing.

WHY ROIC
Verified 2026-08-30 against the live API with the project's own key. FMP's
transcript endpoints return HTTP 402 (paywalled) despite docs implying free
tier. Seeking Alpha prohibits automated access. Roic's free tier includes
transcripts, capped at 5 req/min and 2 years of history — and 2 years is
double the trailing-4-quarter window SCOPE.md already settled on.

ENDPOINT
    GET https://api.roic.ai/v3.0.0/earnings-calls/{identifier}
        ?fiscal_year={yyyy}&fiscal_quarter={n}
    Authorization: Bearer <key>

Identifier must be exchange-qualified. Bare "NVDA" is rejected with
invalid_parameter; "NASDAQ:NVDA" works.

RESPONSE
    {"symbol": "NASDAQ:NVDA", "fiscal_year": 2027, "fiscal_quarter": 2,
     "date": "2026-08-26",
     "transcript": [{"speaker": "Operator", "text": "..."}, ...]}

A real NVDA call is ~31 turns and ~46,000 characters.

WHAT IS NOT IN THE PAYLOAD
Turns carry only `speaker` and `text`. There is no role field and no section
marker, so "is this the CFO or an analyst" and "are we in prepared remarks or
Q&A" must both be derived. That derivation is a Silver concern; Bronze lands
the turns exactly as returned, with their order preserved.

Order is the one thing Bronze must not lose: a transcript is a sequence, and
turn_index is the only way to reconstruct it after a shuffle.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.roic.ai/v3.0.0"

# Exchange-qualified, per the identifier rule above.
ACCOUNTS = {
    "NVDA": "NASDAQ:NVDA",
}

def roic_key() -> str:
    """Read the key from the secret scope at run time.

    Not passed as a task parameter: Databricks does not substitute
    {{secrets/...}} into spark_python_task parameters (it arrives as the
    literal string, which fails with 401), and argv is echoed into run logs
    even when substitution works.
    """
    from databricks.sdk.runtime import dbutils

    return dbutils.secrets.get(scope="account_signals", key="roic_api_key")


def get(path: str, key: str, tries: int = 4, **params):
    """GET with backoff. On the free tier 429 is normal traffic, not failure."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        f"{BASE}{path}?{q}", headers={"Authorization": f"Bearer {key}"}
    )
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                time.sleep(65)   # limit is per-minute, so wait one out
                continue
            raise


def flatten(symbol: str, payload: dict):
    """Transcript turns -> flat rows, order preserved in turn_index."""
    d = payload.get("data", payload)
    for i, turn in enumerate(d.get("transcript") or []):
        yield {
            "symbol": symbol,
            "identifier": d.get("symbol"),
            "call_id": d.get("id"),
            "fiscal_year": d.get("fiscal_year"),
            "fiscal_quarter": d.get("fiscal_quarter"),
            "call_date": d.get("date"),
            "turn_index": i,
            "speaker": turn.get("speaker"),
            "text": turn.get("text"),
        }


SCHEMA = """
    symbol string, identifier string, call_id string,
    fiscal_year int, fiscal_quarter int, call_date string,
    turn_index int, speaker string, text string
"""


def main() -> None:
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    fy, fq = int(sys.argv[3]), int(sys.argv[4])
    key = roic_key()

    rows = []
    for symbol, identifier in ACCOUNTS.items():
        payload = get(f"/earnings-calls/{identifier}", key,
                      fiscal_year=fy, fiscal_quarter=fq)
        turns = list(flatten(symbol, payload))
        rows.extend(turns)
        print(f"{symbol} FY{fy}Q{fq}: {len(turns)} turns, "
              f"{sum(len(t['text'] or '') for t in turns):,} chars")

    df = spark().createDataFrame(rows, schema=SCHEMA)
    bronze_write(df, catalog, schema, "transcript_turns")
    print(f"wrote {len(rows)} turns to {catalog}.{schema}.bronze_transcript_turns")


if __name__ == "__main__":
    main()
