"""Macro indicators -> bronze_macro.

FRED, the St. Louis Fed's economic data service. Verified live 2026-08-30.

No API key needed. FRED's graph endpoint serves any series as CSV:

    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10

That is the whole integration. FRED does have a keyed JSON API, but the CSV
route needs no registration and no secret to rotate.

Series chosen against the three things SCOPE.md names - rates, export policy,
capex - as far as public series allow. Export policy has no clean series, so
it is not represented here; it shows up in the news and trends feeds instead.
"""
import io
import sys
import urllib.request

BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
import os

# SEC and most feeds want a contact address in the User-Agent.
UA = f"account_signals/0.1 ({os.environ.get('SEC_CONTACT', 'contact@example.com')})"

SERIES = {
    "DGS10":    "10-year Treasury yield",
    "FEDFUNDS": "Federal funds effective rate",
    "PNFI":     "Private nonresidential fixed investment",
    "T10Y2Y":   "10-year minus 2-year Treasury spread",
    "CPIAUCSL": "Consumer price index, all urban consumers",
}

SCHEMA = "series_id string, series_name string, obs_date string, value double"


def fetch(series: str):
    req = urllib.request.Request(BASE.format(series=series),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8", "replace")

    reader = io.StringIO(text)
    header = reader.readline().strip().split(",")
    value_col = 1 if len(header) > 1 else 0
    for line in reader:
        parts = line.strip().split(",")
        if len(parts) <= value_col:
            continue
        raw = parts[value_col]
        # FRED marks missing observations with a dot, not an empty field.
        if raw in (".", ""):
            continue
        try:
            yield {"series_id": series, "series_name": SERIES[series],
                   "obs_date": parts[0], "value": float(raw)}
        except ValueError:
            continue


def main() -> None:
    from _common import bronze_write, spark

    catalog, schema = sys.argv[1], sys.argv[2]
    rows = []
    for series in SERIES:
        try:
            obs = list(fetch(series))
            rows.extend(obs)
            last = obs[-1] if obs else None
            print(f"{series}: {len(obs)} observations"
                  + (f", latest {last['obs_date']} = {last['value']}" if last else ""))
        except Exception as e:
            print(f"{series}: FAILED - {type(e).__name__}: {str(e)[:120]}")

    if not rows:
        raise RuntimeError("every macro series failed; not writing an empty batch")

    df = spark().createDataFrame(rows, schema=SCHEMA)
    bronze_write(df, catalog, schema, "macro")
    print(f"wrote {len(rows)} observations to {catalog}.{schema}.bronze_macro")


if __name__ == "__main__":
    main()
