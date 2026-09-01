# src/ingest/CLAUDE.md

Six Bronze sources. See root [CLAUDE.md](../../CLAUDE.md) for the invariant
and the three pipeline paths, and [ARCHITECTURE.md](../../ARCHITECTURE.md)
for why Bronze is append-only and barely parsed. This file is what's specific
to writing or changing one of these six.

## `_common.py`

Every source calls `from _common import bronze_write, spark` inside `main()`,
not at module level — these run as `spark_python_task`, so top-level imports
would break local/test invocation of the module's other functions.

- `bronze_write(df, catalog, schema, table)` appends to
  `{catalog}.{schema}.bronze_{table}` and stamps `_ingested_at`. Every source
  writes through this — never `df.write` directly.
- `secret(scope, key)` wraps `dbutils.secrets.get`. Note four of the six
  sources (`edgar`, `macro`, `news`, `trends`) don't use it — they take
  `sec_contact` as a plain job parameter (it's not a secret) or read
  `SEC_CONTACT` from the environment directly. Only `ratings.py` and
  `transcripts.py` call a secret scope (`account_signals` /
  `fmp_api_key`, `roic_api_key`), and both do it in their own local
  `fmp_key()` / `roic_key()` rather than through `_common.secret` — inline
  `dbutils.secrets.get`, same effect, just not routed through the shared
  helper.

## Invocation (`resources/ingest.job.yml`)

All six run as parallel `spark_python_task`s with no `depends_on` between
them — one flaky feed doesn't block the others. `build_silver` depends on
all six finishing (success or handled failure) before the Bronze→Silver
pipeline runs. Every task gets `catalog` and `schema` as argv[1]/argv[2];
`edgar.py` additionally gets `sec_contact` (argv[3]) and `transcripts.py`
gets `fiscal_year`/`fiscal_quarter` (argv[3]/argv[4]) — both are job
parameters, not secrets, so passing them as task parameters is fine (the
secret-in-argv ban in root CLAUDE.md is about `roic_api_key`/`fmp_api_key`,
which stay off argv entirely).

## Failure handling: inconsistent by design, not oversight

- `macro.py`, `news.py`, `trends.py`: each series/feed is fetched in its own
  `try/except`, printed as `FAILED` on error, and skipped — the task only
  raises if *every* series/feed came back empty (`raise RuntimeError` guards
  against writing an empty batch).
- `ratings.py`: same per-symbol try/except pattern, but never raises even if
  every symbol fails — it just prints and writes whatever it has (nothing,
  if the free-tier ceiling was already hit for all three).
  `grades`/`grades-consensus` are written as two separate tables
  (`bronze_analyst_ratings`, `bronze_analyst_consensus`), each conditionally
  — a table is only written if it has rows.
- `edgar.py`: no per-account try/except. One CIK failing takes the whole
  task down. (SEC's one-call-per-company shape and 10 req/s ceiling make
  partial failure less likely than for the RSS/multi-endpoint sources, but
  this is a real asymmetry to know about before assuming all six sources
  degrade gracefully.)
- `transcripts.py`: no top-level raise on all-empty — an account with no
  transcript in the last 8 fiscal periods just logs and is skipped; the
  `main()` still calls `bronze_write` unconditionally (an all-empty run
  writes zero rows, not nothing).

## Per-source quirks actually in the code

- **`edgar.py`** — CIK must be zero-padded to 10 digits (`"1045810"` 404s,
  `"0001045810"` works; see `ACCOUNTS`). `urllib` advertises
  `Accept-Encoding: gzip` but never decompresses it — `fetch_company_facts`
  does that manually. `value` is declared as `double` in `SCHEMA` because raw
  `val` arrives as a mix of int and float across ~27k facts, and Spark's
  type inference won't merge `LongType`/`DoubleType` in one DataFrame.
- **`transcripts.py`** — identifier must be exchange-qualified
  (`NASDAQ:NVDA`, not `NVDA`) or the API 404s. `get()` retries on HTTP 429
  with a flat 65s sleep (limit is per-minute) — expect a task to take
  several minutes if the free tier is saturated. Fiscal calendars aren't
  aligned across accounts (Micron's fiscal year ends in August), so
  `candidates()` walks backward up to 8 fiscal-quarter coordinates per
  account and takes the first that returns turns, rather than asking every
  account for the same `(fiscal_year, fiscal_quarter)`.
- **`ratings.py`** — uses FMP's `/stable/*` paths, not legacy `/api/v3/*`.
  A 402 on any symbol means the free-tier ceiling was hit mid-loop; caught
  and logged per-symbol, not fatal to the other symbols already collected.
- **`news.py`** — Yahoo's per-ticker feed is not reliably scoped to the
  ticker (an NVDA query has returned Hershey/Buffett headlines), so both
  Google and Yahoo results are filtered post-fetch against a per-account
  `terms` regex matched on title+summary; anything not mentioning the
  company is dropped before it reaches Bronze. Seeking Alpha is deliberately
  excluded (ToS grounds, decided when evaluating it for transcripts — see
  module docstring for the reasoning).
- **`trends.py`** — not account-specific; these describe the industry, and
  relevance to a given account is decided at retrieval, not here. One feed
  (SemiAnalysis) is known stale (no posts since ~Sept 2025) and is kept
  anyway because downstream filters on `published_at`, so a stale feed just
  contributes nothing rather than something wrong.
- **`macro.py`** — no API key; pulls FRED's public CSV graph endpoint
  (`fredgraph.csv?id=...`), not the keyed JSON API. FRED marks missing
  observations with a literal `"."`, not an empty field — `fetch()` checks
  for that explicitly before the `float()` cast.

## Adding a new source

Match the existing shape: `main(catalog, schema, ...)` reads argv, builds
rows as plain dicts, declares an explicit `SCHEMA` string (don't rely on
`createDataFrame` type inference — see the `edgar.py` int/float note above),
and calls `bronze_write`. Decide deliberately whether a fetch failure should
be per-item-caught-and-logged or fatal — the six existing sources aren't
consistent with each other (see above), so "match the pattern" isn't
well-defined; pick based on whether partial data from this source is useful
downstream. Add the task to `resources/ingest.job.yml` under `tasks:` with
no `depends_on` on the other ingest tasks, and add it to `build_silver`'s
`depends_on` list so Silver waits for it.
