# account_signals — Architecture

Companion to [SCOPE.md](SCOPE.md). Scope says *what* and *why*; this says *how*.

**Workspace:** `<your-workspace>` · **Catalog:** `workspace` · **Schema:** `account_signals_dev`

---

## The one decision everything else follows from

The six sources split by **shape**, not by topic, and that split is the whole design.

SEC XBRL filings are structured: every value carries a machine-readable concept tag,
so a quarter-over-quarter delta is arithmetic — a window function and a subtraction.
**No language model touches this path.** That is what makes "real deltas, not
management framing" a property of the system rather than a hope. Letting an LLM read
filing prose and report that revenue moved 4% would rebuild the exact problem the
project exists to solve.

The other five sources are prose. They get chunked, embedded, and retrieved, so the
briefing prompt carries only passages relevant to one account and every claim traces
back to a source row.

Two paths, joined only at synthesis — where the model narrates numbers it was handed
and contrasts them against how management described them.

## Transcripts carry attribution, so chunks follow speakers

Roic AI returns earnings calls as an array of speaker turns rather than a text
blob, which changes what the language path can do. A chunk is one speaker's
complete thought, tagged with `speaker`, a derived `role`
(management / analyst / operator) and a derived `section`
(prepared_remarks / qa).

Both derived fields come from structure, not prose parsing. Section is the
Operator's first handover to an analyst. Role is *who the Operator handed over
to* — deliberately not "who spoke before Q&A", which fails on real calls: in
NVDA FY2027 Q2 only the CFO gave prepared remarks, so that rule labels CEO
Jensen Huang an analyst.

This matters because prepared remarks are written in advance by IR and legal —
maximum framing — while Q&A is unscripted, where analysts push and the language
slips. Retrieval can ask for "what the CFO said in Q&A about margins" instead
of hoping a similarity search lands on the right paragraph.

## Flow

```
EXTERNAL                 BRONZE              SILVER                  GOLD
─────────────────────────────────────────────────────────────────────────────
SEC EDGAR XBRL ──┐                    ┌─ silver_financial_deltas ─┐
FMP ratings      │                    │  silver_rating_changes    │
Macro ───────────┤                    │   (arithmetic, no LLM)    │
                 ├─ ingest.job ─► bronze_* ─┤                     ├─► gold_briefing
Transcripts      │   (6 parallel      │  ┌─ silver_doc_chunks ────┘    script + audio
News / exec      │    Python tasks)   │  │   └─► Vector Search              │
RSS trends ──────┘                    │  │                                  │
                                signals.pipeline (declarative)              │
                                                                            ▼
                                                              UC Volume: /Volumes/.../audio
                                                                            │
                          ┌── synced table (UC ─► Lakebase) ────────────────┘
                          ▼
                   LAKEBASE POSTGRES  ◄──── FastAPI app (read briefing, ms latency)
                          │                        ▲
                          │                        └── rep records spoken recap
                          │                             └─► STT ─► INSERT recall_recaps
                          │
                   sync_recaps job (scheduled read)   ⚠ INTERIM — see below
                          ▼
              bronze_recall_recaps ─► recall_recaps_current (view)
                          │
                   grading.job ─► gold_recall_grades (accuracy + gaps)
                          │
                          └──► read by the NEXT briefing run as a callback
```

## Why each piece is what it is

**Bronze is append-only and barely parsed.** When a provider changes their JSON
shape, you re-parse from Bronze instead of re-downloading years of filings.
It is the undo button.

**Jobs pull, pipelines derive.** A declarative pipeline is good at "given these
tables, derive those" and clumsy at "authenticate, paginate, handle a 429." So the
six external pulls are Job tasks, and Bronze → Silver is a pipeline. Each half
fails independently. The six ingest tasks have no `depends_on` between them —
one flaky feed cannot block the other five.

**Serverless everywhere.** This workspace has no clusters. Tasks omit cluster
config and declare an `environment_key` with `client: "4"` for pip dependencies.

**Lakebase serves the app; Delta does not.** Delta is columnar files in object
storage — excellent for scanning millions of rows, slow for "fetch one row now."
A synced table mirrors `gold_briefing` into Postgres and the app reads that.

**The write-back is the point.** UC → Lakebase for reads, Lakebase → UC for writes.
Two different mechanisms, opposite directions. That round trip is the architectural
claim the project demonstrates.

**The callback loop is broken by time.** Briefings produce grades; grades feed
briefings. This looks circular. It is not, because `synthesize.py` reads the most
recent grade *as of the previous run* — a dependency on past state, not a cycle.
Per SCOPE.md, only the single most recent graded recap is used, applied once.

## Layout

```
databricks.yml          bundle name, variables, dev/prod targets
resources/              one file per resource, <name>.<type>.yml
  ingest.job.yml        6 parallel Python tasks -> Bronze
  signals.pipeline.yml  Bronze -> Silver (declarative)
  briefing.job.yml      retrieve -> synthesize -> narrate
  grading.job.yml       recap vs. brief -> accuracy + gaps
  volume.yml            audio storage
  app.yml               FastAPI briefing app
src/                    implementation, mirrors resources/
tests/                  pure logic only (delta math, chunking, grade parsing)
```

`dev` and `prod` differ only in variables. `mode: development` prefixes resources
with your username and pauses schedules, so dev deploys never fire on a cron.
`mode: production` requires an explicit `root_path` so exactly one copy is deployed.

## Verified available in this workspace

| Capability | Status |
|---|---|
| Foundation models | ✅ `gpt-oss-120b`, `llama-4-maverick`, `llama-3-3-70b` |
| Embeddings | ✅ `gte-large-en`, `bge-large-en` |
| Vector Search | ✅ endpoint `account-signals-vs` ONLINE |
| Lakebase Autoscaling | ✅ project `account-signals-dev`, Postgres 17 |
| UC Volumes, secret scopes | ✅ |
| **Speech (TTS/STT)** | ❌ **none in-platform — external API required** |

## Write-back: interim, by necessity

The intended mechanism is **Lakebase CDF** (formerly Lakehouse Sync): native CDC
from Postgres into Unity Catalog, no compute, an SCD Type 2 history table.
It is blocked here:

> Lakebase CDF is not supported for catalogs using Default Storage.
> Please use a catalog and schema backed by external storage.

This is Free Edition. Its only catalog, `workspace`, is Databricks Default
Storage, and it is the only external location that exists. There is no second
catalog to point at, so the block is structural, not a permission.

**Interim:** a scheduled task (`sync_recaps`, in the grading job) reads new rows
from `app.recall_recaps` over a normal Postgres connection and appends them to
Delta. Incremental by `recap_id`, so re-runs are free and never double-count.
The recall loop closes; what is given up is native CDC, SCD Type 2 history,
update/delete capture, and seconds-level latency. Recaps are insert-only, so
nothing is lost today.

**The seam.** Grading never reads a physical table. It reads the
`recall_recaps_current` **view**:

| | view points at |
|---|---|
| now | `bronze_recall_recaps`, filled by `sync_recaps` |
| phase 2 | `lb_recall_recaps_history`, filtered to `_pg_change_type = 'insert'` |

Phase 2 is one `CREATE OR REPLACE VIEW` plus deleting one task. No grading code
changes. That indirection is the only reason the view exists.

Phase 2 needs an S3 bucket, an IAM role, a storage credential, an external
location, and a catalog backed by it — all AWS-side work, spelled out in
SCOPE.md under *Planned decisions*.

**Correction to the vendor docs:** the `databricks-lakebase` skill says Lakehouse
Sync is UI-only with no CLI or REST API. That is out of date as of CLI v1.14.1,
which ships `databricks postgres create-cdf-config` (beta). Phase 2 is fully
scriptable; the blocker is storage, not tooling.

## Manual setup steps

1. **API keys** into the `account_signals` secret scope: `fmp_api_key`,
   `tts_api_key`, `stt_api_key`.


## Known risks

Written down so they survive past whatever conversation found them. Each one
says what could go wrong, how you would notice, and what to do about it.

### Data correctness

**Derived Q4 inherits any error in the annual figure.**
Q4 is annual minus the three reported quarters. If a 10-K restates the year,
every derived Q4 shifts with it, and nothing in the pipeline flags that the
number moved. Worst case a briefing quotes a Q4 that a later filing contradicts.
Notice it: `is_derived = true` marks these rows; `any_derived` marks any delta
computed from one. Do about it: have the briefing say a derived number was
computed rather than reported.

**Operating income is 100% derived.**
NVDA has never filed Q4 operating income as its own fact — 0 of 19 years,
versus 9 of 19 for revenue. So every operating-income Q4 depends entirely on
the annual figure being right. Higher exposure than the other metrics.

**Concept priority lists are verified on NVDA only.**
`CONCEPTS` in `xbrl_metrics.py` lists the tags each metric accepts. Another
company using a tag that is not listed produces no rows for that metric — a
silent gap, not an error. Notice it: a metric missing entirely for one account.
Do about it: check coverage per metric when adding an account.

**Same quarter filed twice with different dates.**
NVDA's 2010 Q2 appears as both `2010-05-03..2010-07-31` and
`2010-05-03..2010-08-01`, same value. Fixed by collapsing on start date and
keeping the newest filing, but the underlying habit — a company refiling a
period with shifted dates — may show up in other shapes.

**Date-join tolerances could match the wrong quarter.**
QoQ allows +/-20 days around 91, YoY +/-25 around 365, to absorb 13-week
quarters and 52/53-week years. A company with irregular periods, or a gap
where the true counterpart is missing, could match a neighbour instead.
Notice it: `prev_q_end` and `prev_y_end` are on every delta row — read them.

**Restatements make history mutable.**
Dedupe keeps the newest filing, so a re-run after a restatement can change
figures for quarters years in the past. Briefings are therefore not reproducible
over time unless the evidence is snapshotted at generation.

**Transcript role and section rules are verified on one call.**
The Operator-handover rule for `role`, and the first-handover rule for
`section`, both come from NVDA FY2027 Q2. Other companies run calls
differently. Notice it: the `qa_boundary_found` expectation fires when no
handover is detected.

**The embedding endpoint truncates silently rather than failing.**
Measured on 2026-08-30: `databricks-gte-large-en` accepts 8192 tokens, about
56,000 characters. At 60,000 characters it returned `prompt_tokens=8192` and
no error — the tail was dropped with nothing to indicate it. Nothing in a
transcript comes near that (the longest NVDA turn is 18,376 characters, 2,670
tokens), but any future source with long documents could be half-embedded and
look fine. Notice it: compare `char_count` against roughly 56,000.

*Earlier note here claimed the embedding window was near 3,000 characters and
that chunking was needed to avoid failure. That was wrong on both counts.
Chunking is now implemented, but for retrieval quality: one vector for an
18,000 character turn averages twenty topics, so a search for "margins" matches
the whole block with a diluted signal.*

**DLT expectations can only name columns that survive the final select.**
This has now failed a pipeline twice - once on `qa_start_index`, once on
`value` versus `latest_value`. The expectation is evaluated against the
returned dataframe, not the intermediate one, and the failure is an unresolved
column at analysis time rather than anything data-related.

**Serverless compute cannot open a Postgres connection.**
Verified 2026-08-30 with a minimal isolated job: importing a driver and calling
connect() against the Lakebase endpoint kills the task outright with "Fatal
error: The Python kernel is unresponsive". It fails identically with psycopg v3
and psycopg2, so it is not the driver. Outbound HTTPS works - every ingest job
calls SEC, FMP, Roic and RSS - so the block is specific to the database port.

Two things depend on this and are affected:
- `src/sync/read_recaps.py`, the interim write-back that reads recaps from
  Postgres, is written against psycopg and has never been run. It will fail.
- Re-granting the app's Postgres access after a sync cannot be automated from
  a job, so it is a manual step.

The likely way through is Lakebase's Data API, which is PostgREST-compatible
CRUD over HTTPS rather than the Postgres wire protocol. Untested.

**A rebuilt synced table loses its grants.**
Changing the source table's schema makes the sync drop and recreate the
Postgres table. The new table is a fresh object owned by the sync's writer
role, so every grant on the old one is gone and the app silently returns no
rows. The clean fix, ALTER DEFAULT PRIVILEGES FOR ROLE <writer>, needs
membership in that writer role: Postgres refuses with "permission denied to
change default privileges". Until the Data API path is proven, re-grant by hand
after any schema change:

    GRANT USAGE ON SCHEMA app TO "<app-sp-client-id>";
    GRANT SELECT ON ALL TABLES IN SCHEMA app TO "<app-sp-client-id>";

### Platform

**Lakebase CDF is unavailable on Free Edition**, so the recap write-back runs
as a scheduled read job instead of native CDC. No SCD Type 2 history, no
update/delete capture, latency equal to the schedule. See *Write-back* above.

**Free-tier rate limits.** Roic 5 requests/minute (the ingest backs off on 429).
FMP roughly 250/day. SEC 10/second and requires a contact address in the
User-Agent. None bind at 2-3 accounts daily; all bind if the account list grows.

**One 2X-Small serverless warehouse**, so work is sequential. It also auto-stops,
and Catalog Explorer shows an empty catalog when it is stopped, which looks
like data loss and is not.

**Lakebase branch limit is 512 MB.** Fine for briefings and recaps. Do not
mirror Bronze into Postgres.

**The model will repeat a prompt instruction even when it does not apply.**
Telling it to "note when a figure is derived" produced the claim that every
figure was derived, when none were. Instructions about data provenance must be
conditional on the data, and state the negative case explicitly.

**Prompt rules need programmatic checking, not trust.**
The model has broken explicit prompt instructions in every run so far: it added
bullet points to a script that forbade them, read chunk metadata aloud, spelled
numbers out as words, and stated a fabricated figure. `synthesize.py` now runs
a format guard over the output. Treat every new prompt rule as unverified until
something checks it.

**The script undershoots its length target, and the causes compound.**
Mode A asks for 1,400-1,800 words; the last run produced 1,060. Phase 3 is
instructed to skip itself because no macro source exists, removing roughly a
quarter of the intended runtime, and the model has only five metrics and one
earnings call to draw on. At 213 words per minute that lands near five minutes
against a ten-minute goal. More sources should fix more of this than more
prompt tuning will.

**A signed number plus a rule for reading the sign will be read wrong.**
The model was given "+20.6 percentage points (negative means growth is
slowing)" and reported growth slowing by 20.6 points when it had accelerated by
that much - then built a strategic signal and a customer discovery question on
the inversion. Nothing errored. Any directional value handed to a model must
state its direction in words.

### Product and licensing

**Analyst ratings carry no reasoning.** FMP grades give a firm, two grades and
an action verb. The briefing can say a firm moved, not why.

**Roic free tier holds 2 years of history**, which covers the trailing-4-quarter
window but rules out longer backfills.

**Check the terms before publishing.** Roic and FMP free tiers may restrict
commercial use or redistribution. This is portfolio work shown to employers,
which is a grey area worth reading rather than assuming.

**`rating_changes.py` is written but not wired into the pipeline**, because
`bronze_analyst_ratings` does not exist until the FMP ingest is implemented,
and one unresolvable table fails the whole pipeline run.

## Known constraints

- **Lakebase branch size limit: 512 MB.** Fine for briefings and recaps; do not
  mirror Bronze into Postgres.
- **Warehouse is 2X-Small serverless**, one of them. Sequential, not concurrent.
- **Speech is off-platform**, so STT/TTS latency and cost sit outside Databricks
  monitoring.

## Open questions

- Analyst ratings are a **quantitative signal only**: changed, direction, magnitude
  in notches. FMP grades carry no analyst reasoning, so the briefing reports *that*
  a firm moved, not *why*. Deferred, not forgotten: `/stable/grade-latest-news`
  carries article text and could add a qualitative layer later — evaluate it on its
  own terms, since it is news *about* rating changes rather than analyst reasoning.
- Grade vocabularies differ by firm, so Silver maps them onto a shared 1-5 ordinal
  scale before differencing. Unmapped grades resolve to NULL rather than a default —
  defaulting to neutral would invent a rating move no analyst made.
- FMP free tier is rate limited to a few hundred calls/day. Ample for 2-3
  accounts daily; do not loop a symbol universe.
- Confirm a16z's State of AI report actually lands in the RSS feed — it is a
  periodic report, not a standard post.

## Build order

One **vertical slice** first: one account, one filing, one script, one MP3, played
back. That proves the spine in days and surfaces integration problems early.
Breadth — six sources, two accounts, the recall loop — comes after.
