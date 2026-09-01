# account_signals

A daily audio briefing for enterprise account executives, built on Databricks.

It reads a company's SEC filings and earnings calls, computes what actually
changed, contrasts that against how management described it, and narrates the
result as a ten-minute podcast episode. Then it listens to what the rep
remembers and folds their gaps into tomorrow's episode.

---

## The idea

Staying current on a strategic account means reading filings, listening to
earnings calls, and tracking news. The most valuable part — working out what a
quarter actually says versus how management framed it — takes hours.

The design principle follows from that: **numbers are computed in SQL, never by
a language model.** SEC XBRL data is machine-tagged, so a quarter-over-quarter
delta is arithmetic. Margins, whether costs outran revenue, whether growth is
accelerating — all calculated in the pipeline and handed to the model already
done. The model narrates and explains. It never derives a figure.

If a model summarised the filing text instead, it would reproduce exactly the
management framing the project exists to strip out.

## What it does

```
SEC EDGAR ──┐
Roic AI     │                        ┌─ financial deltas ──┐
Google News ├─► Bronze ─► Silver ────┤  metric context     ├─► briefing script
FMP ratings │   (raw)     (cleaned)  │  rating changes     │   ↓
RSS trends  │                        └─ doc chunks ────────┘   TTS ─► MP3
FRED macro ─┘                                                   ↓
                                                        Unity Catalog Volume
                                                                ↓
                              Lakebase Postgres ◄── synced table
                                     ▲    │
                     spoken recap ───┘    └──► FastAPI app (Databricks Apps)
                                     │
                     Lakehouse Federation ─► grading ─► gaps ─► tomorrow's cold open
```

Six sources. The episode's shape is chosen in SQL by strict priority, not a
score — a weighted blend would occasionally let three minor news items outvote
an earnings call:

| | Trigger | The episode is |
|---|---|---|
| **A** | an earnings call or filing landed | that quarter, numbers against management's framing |
| **B** | real news, no earnings | the news; feedback comes later if there is room |
| **C** | neither | whatever the rep has queued |

Mode C is why the topic queue exists. Without it the model would choose the
subject from its own knowledge, which is the one thing this project is built
not to do.

## The loop

After listening, the rep answers **three comprehension questions** out loud.
Each is transcribed and graded the moment they stop speaking — score, verdict,
and what they missed. Anything they miss becomes a one-click request for a
future episode, and on a quiet day that queue chooses the subject.

Free-form recall was the first design and it was worse: hard to grade fairly,
and it let a rep skate by on whatever they happened to remember.

## Built with

| | |
|---|---|
| Declarative Automation Bundles | infrastructure as code, dev and prod targets |
| Lakeflow Declarative Pipelines | Bronze → Silver, with data-quality expectations |
| Unity Catalog | catalogs, schemas, Volumes for audio, secret scopes |
| Lakebase Postgres | serving layer, synced from Delta, and the write-back source |
| Databricks Apps | FastAPI briefing player |
| Foundation Model APIs | synthesis, episode titles, and the grading judge |
| Serverless compute | every job and pipeline |

## Running it

Needs [git](https://git-scm.com/downloads), the [Databricks
CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install) (v1.14+), and a
Databricks workspace you can log into.

**1. Get the code onto your computer.** Open a terminal (macOS: Terminal app;
Windows: Command Prompt or PowerShell) and run:

```bash
git clone https://github.com/thesideqst/account-signals.git
cd account-signals
```

**2. Log in and set everything up.** Replace `<your-workspace-url>` with your
Databricks workspace address (looks like `https://your-company.cloud.databricks.com`),
then run each line below in order:

```bash
databricks auth login --host <your-workspace-url> --profile DEFAULT

# API keys: SEC needs none, the rest are free tiers
databricks secrets create-scope account_signals
databricks secrets put-secret account_signals fmp_api_key    # financialmodelingprep.com
databricks secrets put-secret account_signals roic_api_key   # roic.ai
databricks secrets put-secret account_signals tts_api_key    # OpenAI
databricks secrets put-secret account_signals stt_api_key    # OpenAI

# set var.sec_contact in databricks.yml to your own address first —
# SEC fair-access policy requires it
databricks bundle deploy -t dev
databricks bundle run ingest   -t dev   # pull the sources
databricks bundle run signals  -t dev   # Bronze → Silver
databricks bundle run briefing -t dev   # write and narrate the episode
databricks bundle run grading  -t dev   # bring recaps back, grade them
```

## Deploying to your own workspace

The bundle's defaults point at the workspace it was built in. Most of them
follow `databricks.yml` variables and can be overridden on the command line;
one file has to be edited by hand.

**1. Find your own values.** Each of these is a single command:

```bash
databricks warehouses list                       # warehouse_id
databricks postgres list-projects                # lakebase_project
databricks postgres list-branches projects/<project>              # lakebase_branch
databricks postgres list-databases projects/<project>/branches/<branch>   # lakebase_database
databricks jobs list                             # BRIEFING_JOB_ID, after the first deploy
databricks postgres get-synced-table <name>      # sync_pipeline_id
```

**2. Override the bundle variables.** No file edit needed:

```bash
databricks bundle deploy -t dev \
  --var="warehouse_id=<yours>" \
  --var="lakebase_project=<yours>" \
  --var="sec_contact=you@example.com"
```

Or change the `default:` under `variables:` in `databricks.yml` if you would
rather not repeat the flags. `catalog`, `schema`, the model endpoints and the
whole Lakebase path are all wired through this way.

**3. Edit `src/app/app.yaml` by hand.** This is the one exception, and it is a
platform constraint rather than an oversight: the file is copied into the
workspace verbatim and gets no `${var...}` substitution. The bundle's `config:`
block would be the templated alternative, but this workspace's Apps API accepts
it and then silently ignores it — deployed that way with no `app.yaml`, the app
receives no start command and exits immediately. So set `BRIEFING_JOB_ID`,
`DATABRICKS_WAREHOUSE_ID`, `LAKEBASE_ENDPOINT` and `SCHEMA` there directly.

`BRIEFING_JOB_ID` only exists after the first deploy, so the sequence is:
deploy once, read the job id, put it in `app.yaml`, deploy again. Until then
everything works except generating an episode on demand from a queued topic.

**4. Secrets are never in the repo.** They live in the `account_signals` secret
scope created above, and are read inside tasks via `dbutils.secrets.get` rather
than passed as job parameters.

## Layout

```
databricks.yml          bundle config, variables, dev/prod targets
resources/              one file per deployed resource
src/ingest/             six sources → Bronze
src/pipelines/          Bronze → Silver, including all the arithmetic
src/briefing/           retrieval, synthesis, narration, prompts
src/sync/               Lakebase → Unity Catalog write-back
src/grading/            recall grading
src/app/                FastAPI briefing player
```

## Documents

- [SCOPE.md](SCOPE.md) — the problem, success criteria, and an append-only
  decision log. Every non-obvious choice is recorded with the evidence behind
  it, including the ones that turned out wrong.
- [ARCHITECTURE.md](ARCHITECTURE.md) — how it fits together, and a **Known
  risks** register: what could go wrong, how you would notice, what to do.

## Things worth knowing

Several of these cost real time to find, and none were discoverable from
documentation:

**Companies change XBRL tags.** NVIDIA reported revenue under one concept until
2020, then switched. Hardcoding either makes the company appear to stop
reporting. Metrics resolve against a priority list.

**Q4 is usually not filed.** The year closes and the 10-K reports the full year,
so a quarterly series has a hole at every fiscal year end — filled by
subtracting the three reported quarters. But *usually*: NVIDIA filed Q4
separately in 9 of 19 years for revenue and 0 of 19 for operating income, so
the derivation has to check first.

**Positional `lag()` is wrong** over a series with holes. It compares a quarter
to one fifteen months back and calls it year-over-year. Deltas join on explicit
dates, so a missing counterpart yields NULL instead of a confident wrong number.

**Serverless compute cannot open a Postgres connection.** Verified in isolation,
identical with psycopg2 and psycopg3, so it is the platform and not the driver.
The write-back reads Postgres through Lakehouse Federation as ordinary SQL
instead, which sidesteps it entirely.

**Prompt rules need checking, not trusting.** The model has broken explicit
formatting instructions in almost every run — bullet points in a script written
for audio, chunk metadata read aloud, numbers spelled out as words. A format
guard runs over every generated script.

---

Portfolio project. Recall recaps use synthetic data only.
