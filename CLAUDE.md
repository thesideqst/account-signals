# CLAUDE.md

Working memory for Claude Code sessions in this repo — not a summary of the
other docs, so read them for the *what* and *why*:
[README.md](README.md) (pitch, run commands), [ARCHITECTURE.md](ARCHITECTURE.md)
(design, Known risks register), [SCOPE.md](SCOPE.md) (status, decision log),
[tests/README.md](tests/README.md) (test scope). This file is the *gotchas*.

## The invariant

Financial numbers are computed in SQL/Spark, never by the model. The boundary
is concrete code, not a style rule:

- `src/pipelines/xbrl_metrics.py` (`silver_financial_deltas`) computes QoQ/YoY
  deltas, joined on explicit period dates.
- `src/pipelines/metric_context.py` (`silver_metric_context`) computes
  margins and cross-metric relationships (did costs outrun revenue, is growth
  accelerating).
- `src/briefing/synthesize.py` only narrates what those tables hand it — its
  own docstring says "It does not calculate." Any change that lets
  `synthesize.py` or `prompts.py` derive a figure instead of narrate one
  breaks the reason the project exists.

## Where the three pipeline paths live

Per ARCHITECTURE.md, the six sources split by shape, and that split is the
one decision everything else follows from:

- **Structured XBRL, no LLM:** `src/ingest/edgar.py` → `src/pipelines/xbrl_facts.py`
  → `src/pipelines/xbrl_metrics.py` → `src/pipelines/metric_context.py`.
- **Prose / chunking, retrieved by the LLM:** `src/ingest/{transcripts,news,trends,macro}.py`
  → `src/pipelines/transcript_turns.py` + `src/pipelines/chunk_and_embed.py`
  → `silver_doc_chunks`. Retrieval (`src/briefing/retrieve.py`) is currently a
  plain SQL read, not Vector Search, despite the index existing (SCOPE.md,
  2026-08-30: too few chunks yet to need it).
- **Sync / grading:** `src/sync/read_recaps.py` (Lakebase → UC over Lakehouse
  Federation, not a Postgres driver — see gotchas) → `src/grading/grade.py`
  (LLM-judged recall accuracy — the one place an LLM legitimately scores
  something; it's comprehension, not a financial figure).

`src/pipelines/rating_changes.py` (analyst ratings) is purely quantitative
and feeds the SQL path, not retrieval — FMP grades carry no rationale text.

## Running / deploying

Full sequence is in README.md "Running it". Two things worth knowing that
aren't there:

- **Always pass `--profile <name>` explicitly** rather than relying on a
  default. `databricks.yml` currently pins `workspace.profile: DEFAULT` on
  both the `dev` and `prod` targets — that's this workspace's profile name,
  not a convention; repoint it if yours differs.
- **`dev` runs on its cron like `prod`.** `mode: development` normally pauses
  schedules, but every job in `resources/*.job.yml` sets
  `schedule.pause_status: UNPAUSED` explicitly, overriding that default
  (SCOPE.md, 2026-08-30). Don't assume a dev deploy is inert.

**Workspace-specific IDs to update on a fresh deploy.** See README.md >
"Deploying to your own workspace" for the command that finds each one.

- `databricks.yml` variables, overridable with `--var=` and needing no file
  edit: `warehouse_id`, `sync_pipeline_id` (from
  `databricks postgres get-synced-table <name>`), `lakebase_project`,
  `lakebase_branch`, `lakebase_database`. The app's Lakebase resource in
  `resources/app.yml` is templated from these, so that path follows the target.
- `src/app/app.yaml`: `BRIEFING_JOB_ID`, `DATABRICKS_WAREHOUSE_ID`,
  `LAKEBASE_ENDPOINT`, `SCHEMA` — hardcoded, and they have to be. The file is
  copied into the workspace verbatim with no `${var...}` substitution, and the
  bundle's `config:` block, which would be the templated alternative, is
  accepted by the schema and then silently ignored by the Apps API: deployed
  that way with no `app.yaml`, the app gets no start command and exits.
  `BRIEFING_JOB_ID` only exists after the first deploy, so a fresh workspace
  deploys twice.

## Repo-specific gotchas (verified in code)

- **Secret decoding differs by call site.** `dbutils.secrets.get()` (every
  job, via `src/ingest/_common.py:secret`) returns the plain value.
  `WorkspaceClient().secrets.get_secret()` (the app, `src/app/app.py:stt_key`)
  returns it **base64-encoded** — decode before use or you get
  `Incorrect API key provided: c2stcHJv...`.
- **Never pass a secret as a job/task parameter.** `{{secrets/scope/key}}` is
  not substituted into `spark_python_task` parameters — it arrives as the
  literal string and 401s — and argv is echoed into run logs either way. Read
  secrets inside the task instead.
- **Serverless compute cannot open a raw Postgres connection.** Confirmed
  with psycopg2 and psycopg3 alike — the kernel dies outright, not an auth
  error. `src/sync/read_recaps.py` goes through Lakehouse Federation (plain
  SQL against the UC-registered Lakebase project) instead of a driver.
  `src/app/app.py` uses `psycopg` directly because the app itself isn't
  serverless job compute.
- **`resources/app.yml` needs `postgres:`, not `database:`.** They look
  interchangeable but address different backends (`database:` = legacy
  Lakebase instance; `postgres:` = Lakebase Autoscaling, by branch path). The
  wrong one fails `bundle deploy` with a misleading "Database instance ...
  does not exist" — the instance genuinely doesn't exist because this project
  was never one.
- **DLT expectations can only name columns that survive the final `select`.**
  Has broken a pipeline twice already (`qa_start_index`, then `value` vs.
  `latest_value`) — the failure is an unresolved-column error at analysis
  time, unrelated to the data itself.
- **A rebuilt synced table loses its Postgres grants.** Changing the source
  table's schema drops and recreates the Postgres table under the sync
  writer's role; the app then silently returns zero rows. Re-grant by hand
  (exact `GRANT` statements in ARCHITECTURE.md "Known risks") until the Data
  API path is proven.
- **`databricks-gte-large-en` truncates silently past 8192 tokens**
  (~56,000 characters) — no error, just a shorter embedding. Nothing in
  current data reaches that, but check `char_count` before trusting a new
  long-document source.

## Tests

`tests/README.md` names three pure-logic targets, but as of this writing
`tests/` contains only that README — no test files exist yet, and no pytest
config or run command exists anywhere in the repo. Verify before assuming a
test command works as documented.
