# src/sync/CLAUDE.md

Scoped to this directory. Read root [CLAUDE.md](../../CLAUDE.md) and
[ARCHITECTURE.md](../../ARCHITECTURE.md) first — they already cover why
`read_recaps.py` goes through Lakehouse Federation instead of a Postgres
driver, and why the write-back is interim (Lakebase CDF blocked on Free
Edition's Default Storage catalog), incremental by id, and never
double-counts. This file only has what's specific to this folder past that.

## Three write-backs in one run, not one

`read_recaps.py` syncs three Lakebase tables per run, handled three
different ways:

- `app.recall_recaps` → `bronze_recall_recaps`, incremental by `recap_id`.
  Read by `src/grading/grade.py` through the `recall_recaps_current` view —
  the indirection ARCHITECTURE.md describes, kept for the phase-2 CDF swap.
- `app.recap_answers` → `bronze_recap_answers`, incremental by `answer_id`
  the same way. The task also creates a `recap_answers_current` view, but
  nothing in the repo reads it — `src/app/app.py`'s `/api/roundtrip`
  endpoint queries `bronze_recap_answers` directly instead.
- `app.topic_requests` → `topic_queue_current`. No bronze table and no
  watermark here: it's a live `CREATE OR REPLACE VIEW` straight over
  federation, filtered to `status = 'queued'`, re-evaluated every run. Read
  by `src/briefing/synthesize.py`.

Only the `recall_recaps` path is in ARCHITECTURE.md's diagram. The other two
are real and wired into the app/briefing paths, just not drawn there.

## Watermark mechanism

No separate checkpoint table or job state. Each incremental sync computes
its own watermark by querying `max(id)` off the target Delta table itself
(`coalesce(max(recap_id), 0)` / `coalesce(max(answer_id), 0)`), then does one
`INSERT INTO ... SELECT ... WHERE id > hwm` from the federated source table.
That's the entire mechanism — no CDC, no state file, re-running is free.

## Gotchas

- `LAKEBASE_CATALOG = "account_signals_pg"` is hardcoded at module level —
  not passed as a job parameter the way `catalog`/`schema` are, and not in
  root CLAUDE.md's "workspace-specific IDs to update on a fresh deploy"
  list. Update it here too on a fresh deploy.
- `catalog` and `schema` arrive positionally (`sys.argv[1]`, `sys.argv[2]`)
  from the `sync_recaps` task parameters in `resources/grading.job.yml` — no
  argparse, no validation. Both are plain strings, so a swapped order won't
  fail fast; it creates tables in the wrong place or fails later on a
  missing-table error that doesn't point back here.
- No `try`/`except` anywhere in the file. `grade_recaps` in
  `resources/grading.job.yml` `depends_on: sync_recaps`, so any failure here
  (a federation query erroring, Lakebase unreachable) fails the task and
  blocks that day's grading run entirely, not just that day's recap data.
