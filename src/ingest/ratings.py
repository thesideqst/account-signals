"""Financial Modeling Prep analyst grades -> bronze_analyst_ratings.

Replaces Benzinga (SCOPE.md, 2026-08-29). Three endpoints, all free tier,
all "stable" API rather than the legacy /api/v3 paths:

    GET {BASE}/grades?symbol={sym}&apikey={k}
        Latest grade actions. One record per analyst firm action.

    GET {BASE}/grades-summary?symbol={sym}&apikey={k}
        Aggregated distribution: counts of strongBuy/buy/hold/sell/strongSell
        plus consensus. One row per symbol — gives the briefing a "where the
        street sits" line without replaying every individual action.

    GET {BASE}/historical-grades?symbol={sym}&apikey={k}
        Time series of grade changes. Backfill only, not the daily path.

Expected /stable/grades record shape (camelCase; confirm against a live
response on first run — FMP documents the field names but publishes no
sample body):

    {"symbol": "NVDA", "date": "2026-08-27",
     "gradingCompany": "Morgan Stanley",
     "previousGrade": "Equal-Weight", "newGrade": "Overweight",
     "action": "upgrade"}

SCOPE: this source is PURELY QUANTITATIVE (SCOPE.md, 2026-08-29). It answers
did the rating change, in which direction, and by how much — nothing more.
FMP grades carry no analyst reasoning, so there is no prose here to chunk and
this source contributes nothing to the Vector Search path.

Bronze stays raw: land the grade strings exactly as returned. Turning them
into a direction and a magnitude is a Silver concern, and it needs a
vocabulary map — see src/pipelines/rating_changes.py.

Free tier is rate limited (a few hundred calls/day). With 2-3 accounts on a
daily schedule that is ample; do not loop over a symbol universe here.
"""

import sys

BASE = "https://financialmodelingprep.com/stable"

ACCOUNTS = {"NVDA": "NVDA", "GOOG": "GOOG", "MU": "MU"}

GRADES_SCHEMA = """
    symbol string, rating_date string, grading_company string,
    previous_grade string, new_grade string, action string
"""
SUMMARY_SCHEMA = """
    symbol string, strong_buy int, buy int, hold int, sell int,
    strong_sell int, consensus string
"""


def fmp_key() -> str:
    """Read from the secret scope at run time, never a task parameter.
    Databricks does not substitute {{secrets/...}} into spark_python_task
    parameters, and argv is echoed into run logs."""
    from databricks.sdk.runtime import dbutils

    return dbutils.secrets.get(scope="account_signals", key="fmp_api_key")


def get(path: str, key: str, **params):
    import json
    import urllib.request

    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}/{path}?{q}&apikey={key}"
    with urllib.request.urlopen(url, timeout=45) as r:
        return json.loads(r.read())


def main() -> None:
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    key = fmp_key()

    grades, summaries = [], []
    for symbol, ticker in ACCOUNTS.items():
      # One symbol hitting a tier limit should not cost the other two their
      # data. FMP returns 402 once the free plan's ceiling is reached, and it
      # can happen partway through the list.
      try:
        for rec in get("grades", key, symbol=ticker) or []:
            grades.append({
                "symbol": symbol,
                "rating_date": rec.get("date"),
                "grading_company": rec.get("gradingCompany"),
                "previous_grade": rec.get("previousGrade"),
                "new_grade": rec.get("newGrade"),
                "action": rec.get("action"),
            })
        for rec in get("grades-consensus", key, symbol=ticker) or []:
            summaries.append({
                "symbol": symbol,
                "strong_buy": rec.get("strongBuy"), "buy": rec.get("buy"),
                "hold": rec.get("hold"), "sell": rec.get("sell"),
                "strong_sell": rec.get("strongSell"),
                "consensus": rec.get("consensus"),
            })
        print(f"{symbol}: {len(grades)} grade actions, {len(summaries)} consensus rows")
      except Exception as e:
        code = getattr(e, "code", "")
        note = " (free tier limit)" if code == 402 else ""
        print(f"{symbol}: FAILED {type(e).__name__} {code}{note} - continuing")

    if grades:
        bronze_write(spark().createDataFrame(grades, schema=GRADES_SCHEMA),
                     catalog, schema, "analyst_ratings")
        print(f"wrote {len(grades)} rows to {catalog}.{schema}.bronze_analyst_ratings")
    else:
        print("no grade actions returned; nothing written")

    if summaries:
        bronze_write(spark().createDataFrame(summaries, schema=SUMMARY_SCHEMA),
                     catalog, schema, "analyst_consensus")
        print(f"wrote {len(summaries)} rows to {catalog}.{schema}.bronze_analyst_consensus")


if __name__ == "__main__":
    main()
