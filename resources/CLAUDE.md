# CLAUDE.md

Scoped to this directory. Read [../CLAUDE.md](../CLAUDE.md) first (`--profile`
flag, dev-cron-fires-too gotcha, `postgres:` vs `database:`, workspace IDs to
update on a fresh deploy) and [../ARCHITECTURE.md](../ARCHITECTURE.md)
"Layout" (what each file is). This file covers only what's specific to
editing a resource definition here.

## Variable flow

`databricks.yml` declares `variables:` (catalog, schema, llm_endpoint, etc.)
with workspace defaults, overridden per-target. Resource files consume them
two ways:

- Pipeline/app config reads `${var.x}` directly — e.g. `signals.pipeline.yml`'s
  `catalog:`, `schema:`, `configuration.embedding_endpoint:`.
- Job config goes through an extra hop: a job's `parameters:` list defaults
  to `${var.x}`, and each task's `spark_python_task.parameters:` passes it on
  as a *positional* string via `"{{job.parameters.x}}"` — order must match
  what the target Python file expects from `sys.argv`. Adding a new job
  parameter means touching three places: the `parameters:` default, every
  task's `parameters:` array that needs it, and the script's argv parsing.

## The one cross-resource reference

`ingest.job.yml`'s `build_silver` task points at the pipeline by id:
`pipeline_id: ${resources.pipelines.signals.id}`. That's the only dependency
between resource files — rename the `signals:` key in `signals.pipeline.yml`
and this breaks too.

## `environment_key` convention

Each job file (`ingest`, `briefing`, `grading`) declares exactly one
`environments:` entry (`spec.client: "4"`, a `dependencies:` pip list), and
every task in that job references it via the same `environment_key:` — there
is no per-task environment. `signals.pipeline.yml` doesn't need one;
`serverless: true` at the pipeline level covers it.

## Resource keys don't always match the filename

`ingest.job.yml` → key `ingest`, `grading.job.yml` → key `grading`,
`briefing.job.yml` → key `briefing`, `signals.pipeline.yml` → key `signals` —
but `volume.yml` → key `audio` and `app.yml` → key `briefing_app`. Check the
actual key under `resources:` before referencing one resource from another
file, don't assume it matches the filename stem.

## Naming inconsistency worth knowing about, not "fixing"

Job/pipeline `name:` values (`account_signals_ingest`, `account_signals_briefing`,
`account_signals_grading`, `account_signals_pipeline`) are static snake_case
with no target embedded — `mode: development`'s automatic prefixing (see
ARCHITECTURE.md) is what keeps dev and prod apart. `app.yml` is the one
exception: `name: account-signals-${bundle.target}`, hyphenated and
explicitly interpolating the target. Not obviously a bug worth "fixing" by
guessing — but match the existing snake_case/no-target pattern if you add a
new job or pipeline rather than copying the app's.
