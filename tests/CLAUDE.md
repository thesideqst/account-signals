# CLAUDE.md

Gotchas for writing the first tests here. See root [CLAUDE.md](../CLAUDE.md)
"Tests" section for status (no test files, no pytest config yet) and
[README.md](README.md) for the three targets in scope.

None of those three targets are plain functions today — that's the thing to
know before writing the first one:

- `silver_financial_deltas` (`src/pipelines/xbrl_metrics.py`) and
  `silver_doc_chunks` (`src/pipelines/chunk_and_embed.py`) are `@dlt.table`
  functions that take no arguments and pull their input via `dlt.read(...)`
  inside the function body. There's no seam to hand them a fixture
  DataFrame — exercising the delta-arithmetic or chunk-boundary logic means
  either a local Spark session plus something standing in for `dlt.read`,
  or factoring the transformation into a plain function the `@dlt.table`
  wrapper calls. Neither exists yet; pick one before writing these tests.
- `grade.py`'s JSON parsing (fence-stripping, then
  `json.loads(text[text.index("{"):...])`) is inline in `main()`, mixed
  with the serving-endpoint call and the Spark write — not a standalone
  function to import. `extract_text()` in the same file has no Spark or
  network dependency and is the one target here that's testable as-is.
