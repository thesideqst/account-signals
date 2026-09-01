# account_signals — Scope

**Status:** draft
**Owner:** Aliya Khan
**Created:** 2026-08-29

---

## Problem

For a strategic seller, staying current on strategic accounts requires manually reading quarterly filings, listening to earnings calls, tracking news and exec statements, and checking analyst ratings — and the highest-value part, figuring out what an earnings statement actually says versus what management's language is framing it as, takes real time to do properly. There's no consolidated, digestible version of this that fits into a normal day.

## Goal

A daily ~10-minute audio briefing per account that synthesizes real financial deltas (not management framing), recent news, exec statements, analyst ratings, industry trends, and macro context, listenable in passive moments (e.g. showering, commuting), plus a recall-and-grade loop so listening comprehension actually sticks. The goal is not just for the seller to be able to hear the briefing, but to verify that they actually understand it through a bi-directional feedback loop.

## Success criteria

- [x] Pipeline produces a ~10-minute audio briefing for at least 2 accounts, end to end — 2026-08-31: three accounts (NVDA, GOOG, MU) each publish an episode with audio, playable in the app. Episodes currently run 4-6 minutes rather than 10
- [x] Financial analysis is grounded in structured XBRL deltas, not LLM-paraphrased filing text — 2026-08-30: deltas computed in SQL, passed to the model as fixed numbers to narrate
- [x] Briefing synthesizes at least 5 source types — 2026-08-30: all six connected (XBRL filings, transcripts, news, analyst ratings, industry trends, macro)
- [x] Recall-and-grade loop works: rep speaks a recap, this is transcribed, written to Lakebase, and graded against the actual account brief — 2026-09-01: works through the comprehension questions, which is the path with a UI: the rep answers aloud, Whisper transcribes, the answer is scored and written to `app.recap_answers` with the point they missed. The FREE-FORM recap endpoint (`/api/recap`) also works end to end — 4 real spoken recaps, 4 grades in `gold_recall_grades` — but has never had a UI, so it is only reachable by hand
- [x] The recap write demonstrably returns from Lakebase to Unity Catalog and is graded there (phase 1: scheduled read job; phase 2 target: native Lakebase CDF) — 2026-09-01: 9 answers in Postgres, 9 in Unity Catalog, lag 0, visible live at `/api/roundtrip/NVDA`
- [x] Next briefing for a graded account includes a short callback and emphasis on the most recent missed point — 2026-09-01: verified in `NVDA-2026-07-26-20260901T034220`, which opens on the memory-price / TSMC / financing gap recorded from the previous session's questions

## Out of scope

- Live conversational Q&A voice agent (real-time STT, retrieval, and TTS loop)
- Paywalled analyst/research data (FactSet, Bloomberg, Gartner, TSIA, CB Insights API tier) — use freely available and RSS-automatable sources only
- More than 2-3 accounts for v1 — 2026-08-30: three live (NVDA, GOOG, MU)
- Pattern analysis of a rep's missed points across multiple sessions — gold re-run uses only the gaps from the single most recent graded recap, applied once, not a running history across sessions
- Recall recaps use test/synthetic data only (not real colleague or customer conversations)

---

## Data

| Source | Location | Notes |
|---|---|---|
| SEC EDGAR XBRL filings | Bronze Delta table, Unity Catalog | Structured tagged financial data; basis for real QoQ deltas, not LLM paraphrase of filing text |
| Earnings call transcripts | Bronze Delta table, Unity Catalog | Roic AI free tier, `/v3.0.0/earnings-calls/{EXCHANGE:SYMBOL}?fiscal_year=&fiscal_quarter=`. Returns speaker turns, not a text blob. 5 req/min, 2 years history |
| News / exec statements | Bronze Delta table, Unity Catalog | RSS: Google News search per account, Yahoo Finance per ticker. Headlines and a sentence, not article bodies — establishes that an event happened, not its substance. Seeking Alpha deliberately excluded on ToS grounds |
| Analyst ratings | Bronze Delta table, Unity Catalog | Financial Modeling Prep free tier: `/stable/grades`, `/stable/grades-summary`, `/stable/historical-grades` (backfill only). Purely quantitative — no rationale text, contributes nothing to Vector Search |
| Industry trends | Bronze Delta table, Unity Catalog | RSS: McKinsey Insights, IEEE Spectrum semiconductors, MIT Tech Review AI, SemiAnalysis. Not account-specific — relevance decided at retrieval |
| Macro indicators | Bronze Delta table, Unity Catalog | 2-3 sources (rates, export policy, capex trends) |
| Recall recaps (rep spoken understanding) | Lakebase Postgres → Unity Catalog via scheduled read job (interim; Lakebase CDF in phase 2) | Written from app, transcribed via speech-to-text; test data only. Grading reads the `recall_recaps_current` view, so the phase 2 swap does not touch grading code |

## Signals

| Signal | Definition | Grain | Refresh |
|---|---|---|---|
| Financial delta | QoQ and YoY change in key XBRL line items, trailing 4 quarters. Deltas joined on explicit period dates, never positional lag — the quarterly series has gaps at every fiscal year end | Per account, per fiscal quarter, per metric | On new filing |
| Full-year figure | Reported annual value from the 10-K, kept as a signal in its own right rather than only as an input to derived Q4 | Per account, per fiscal year, per metric | On new 10-K |
| Derived Q4 | Annual minus the three reported quarters. Flagged `is_derived` — the company never filed this number | Per account, per fiscal year, per metric | On new 10-K |
| Analyst rating change | Whether a firm's rating moved, its direction (up/down), and its magnitude in notches on a shared 1-5 scale | Per account, per firm, per action | On new grade action |
| Management framing | Transcript turns attributed to a named speaker, with derived role (management/analyst/operator) and section (prepared_remarks/qa) | Per account, per call, per turn | On new earnings call |
| Briefing script | ~10-min narrated synthesis across all sources | Per account | Daily or on new signal |
| Audio briefing | TTS-rendered version of the script | Per account | Daily or on new signal |
| Recall grade | Accuracy score + gaps between rep's spoken recap and the actual brief | Per account, per rep listen | On recap submission |
| Callback note | Gaps from the single most recent graded recap, surfaced as a short reminder in the next briefing | Per account | On next gold run after a graded recap |

## Deliverable

A minimal audio-first app that serves the daily briefing per account (queried from Lakebase) and accepts a spoken recap for grading. Built for an enterprise account exec prepping for calls — fast to scan, passive-listening-friendly; built and demoed by me as portfolio work, with an architecture doc explaining the design for reviewers.

---

## Open questions

- ~~Where do earnings call transcripts come from?~~ — resolved 2026-08-30: Roic AI free tier, verified live
- Does the Operator-handover rule for `role` hold across other companies' call formats? Verified on NVDA only. The `qa_boundary_found` expectation surfaces calls where no handover is detected — check it after the second account is added
- ~~Self-hosted Whisper vs. cloud STT/TTS API~~ — resolved 2026-08-29, see Decisions
- ~~Whether analyst rationale/commentary needs a separate endpoint or tier~~ — closed 2026-08-29: FMP grades carry no rationale at all, so analyst ratings are a quantitative signal only
- ~~Does a16z's State of AI report land in their RSS feed?~~ — closed 2026-08-30: a16z has no RSS feed at any standard path (six tried, all 404)
- **Future bolt-on:** `/stable/grade-latest-news` carries article text about grade changes and could add a qualitative layer to the ratings source, feeding the Vector Search path. Deliberately deferred — not in v1. Evaluate on its own terms when the quantitative signal is working; it is news *about* rating changes rather than the analyst's own reasoning, so it may not fill the gap it appears to
- ~~Does `GRADE_SCALE` cover the firms that actually rate these accounts?~~ — closed 2026-08-30: 99.8% coverage on 1,138 real NVDA grade actions. The only gap was the bare string "Perform", now mapped to neutral. The `grade_vocabulary_known` expectation is what surfaced it

## Decisions

<!-- Date — decision — why. Append only. -->

- 2026-08-29 — Reframed from generic "account signals" brief to a daily audio briefing podcast, grounded in a real recurring workflow problem rather than a hypothetical one
- 2026-08-29 — Financial analysis sourced from SEC EDGAR XBRL structured data, not LLM summarization of filing text — avoids reproducing the "flowery language" problem the project is meant to solve
- 2026-08-29 — Bidirectional/write-back built as recall-and-grade (spoken recap → transcribed → graded), not live conversational Q&A — smaller scope, same architectural proof point (Lakehouse Sync)
- 2026-08-29 — Grading feedback brought back into scope: next brief includes a callback to the most recent missed point, sourced directly rather than via pattern analysis — closes the loop without open-ended scope
- 2026-08-29 — ~~Analyst ratings via Benzinga~~ — superseded 2026-08-29, see below
- 2026-08-29 — XBRL delta comparison window set to trailing 4 quarters (QoQ + YoY) — enough for real trend signal without the normalization complexity of older segment/M&A changes
- 2026-08-29 — Industry trends sourced via RSS from McKinsey, SemiAnalysis, and a16z (all free, programmatically pullable) — Gartner, TSIA, CB Insights, and Bain dropped: paywalled, no public API/RSS, or would require fragile scraping
- 2026-08-29 — STT and TTS both via OpenAI (whisper-1, gpt-4o-mini-tts) — no speech model exists in this workspace, and self-hosting Whisper would require GPU compute the workspace does not have; one key, one vendor
- 2026-08-29 — Analyst ratings switched to Financial Modeling Prep free tier (`/stable/grades`, `/stable/grades-summary`, secret `fmp_api_key`) — neither Benzinga nor Massive worked out commercially; FMP's free tier gives grade actions without tier negotiation. Consequence: FMP grades carry no rationale text, so this source now feeds the structured path only and contributes nothing to Vector Search
- 2026-08-29 — Analyst ratings scoped to a purely quantitative signal (changed, direction, magnitude in notches) with no qualitative layer — FMP's three free-tier grade endpoints carry no analyst reasoning, and inventing one from grade strings alone would be the same framing problem the project exists to strip out. `/stable/grade-latest-news` noted as a possible future bolt-on, explicitly not in v1
- 2026-08-29 — Grade strings normalized to a shared 1-5 ordinal scale in Silver rather than compared as text — firms use different vocabularies for the same move ("Equal-Weight to Overweight" and "Hold to Buy" are both one notch up), so magnitude is only comparable across firms on a common scale. Unmapped grades resolve to NULL, never to a default, so a vocabulary gap never fabricates a rating move

- 2026-08-30 — Recap write-back implemented as a scheduled Postgres read job rather than native Lakebase CDF — CDF rejects Default Storage catalogs and Free Edition offers no alternative catalog. Deliberately interim; see Planned decisions below. Grading binds to the `recall_recaps_current` view so phase 2 is a view swap, not a rewrite

## Backlog

Ideas worth doing, not yet scheduled.

### Two-host conversation instead of a monologue

Rather than one narrator reading a script, make the briefing a conversation
between two hosts. One lays out what happened; the other asks the questions the
account executive would ask if they were in the room. "Wait, margins held at 75
but they're guiding to 71 next quarter - what changed?" The answer lands better
because someone asked for it.

It also does the work of making this understandable to someone who does not live in
financial statements. A second host is the natural place for "wait, what does that
actually mean" - the question a listener is already thinking. A monologue has to stop and
explain itself, which reads as a lecture; a conversation just asks.

Why it likely beats the monologue:
- A question creates a small gap the listener wants closed. That is most of why
  The Daily holds attention for thirty minutes and a report does not.
- The second host is a stand-in for the listener, so the script can voice the
  scepticism a rep would actually feel rather than narrating past it.
- Two voices are easier to follow for ten minutes than one. Attention resets on
  every handover.

What it changes:
- **The Gold table stops being a text blob.** The script becomes turns, the same
  shape `silver_transcript_turns` already uses: speaker, order, text. The
  existing chunk-and-attribute machinery mostly transfers.
- **TTS becomes one call per turn**, with a different voice per host, and the
  segments get stitched. OpenAI's TTS offers several distinct voices. Costs more
  calls and adds an audio-assembly step that the single-voice version does not need.
- **The prompt changes shape.** Rather than "write a script", it becomes "write a
  conversation", with rules about who knows what. The questioner should not
  already know the answer, or the exchange sounds staged.

Risks to watch:
- Conversational filler dilutes density. Ten minutes of dialogue carries less
  information than ten minutes of narration, and the whole point is that a rep
  gets current quickly. Cap the chat, keep the questions real.
- Two hosts agreeing with each other is worse than one host talking. The second
  voice has to push, not affirm.
- The recall-and-grade loop grades the rep against the brief. If the brief becomes
  more conversational and less dense, decide whether grading targets the whole
  conversation or only the substantive claims in it.

Do this after single-voice TTS works end to end. The monologue is the thing that
proves the pipeline; the conversation is a format change on top of it.

### Rep-requested topics

A second feedback loop alongside recall-and-grade. After listening, the rep says what they
want the next episode to dig into - "explain how their packaging supply chain actually
works", "go deeper on the memory pricing thing". That request drives a future briefing.

Why this matters more than it first looks: **Mode C currently has no way to choose its
subject.** It fires on quiet days and says "teach something structural about this account",
but nothing decides what, so the model would pick from its own knowledge - the exact
grounding hole the rest of the project is built to avoid. A requested topic closes it.
Mode C deep-dives what was asked for.

Architecturally it is nearly free. Same write-back path as the recall recaps: the app
writes to Lakebase, it returns to Unity Catalog, the next briefing reads it. Same table
shape, same mechanism, one more column on `silver_daily_signals` and one more branch in
mode selection.

It also makes the loop genuinely two-way. One signal is *did you understand it*; the other
is *what do you want next*. Grading alone only measures the rep. This lets them steer.

Open questions:
- Does a pending topic request outrank a real signal? If a filing lands the same day, does
  the request wait, or does the episode cover both?
- How long does a request stay live before it goes stale?
- Requests are free text. They need grounding against something - the primer table, or a
  retrieval over existing sources - or Mode C is back to inventing.

### Full-article news, not headlines and teasers

The single largest source of fabrication in this pipeline is that the news it
retrieves is not articles. **410 of 430 news chunks are 300 characters or
fewer** - a headline plus a truncated teaser - because Google News and Yahoo
Finance RSS carry summaries, not bodies. The 2026-09-01 audit traced every
ungrounded figure it found to one of these stubs: a 325-character teaser about
an IPO investment became an invented 1999 date and an invented $200,000 figure,
and a headline containing "15 gigawatts" became an invented global ceiling on
AI power with a consequence chain built on top of it.

**This is a data problem wearing a prompt problem's clothes.** The model is
handed a headline and asked to narrate its significance, which is a request to
speculate. No prompt rule fully survives that, because the instruction and the
task are in direct conflict. The HEADLINE ONLY labelling and the grounding
guard added on 2026-09-01 are mitigation - they stop the worst of it and catch
what gets through - but the fix is to hand the model the article.

What it would change:
- **Ingestion** gains a fetch-and-extract step: follow the RSS link, pull the
  page, extract the body. Readability-style extraction rather than raw HTML.
- **Chunking already works** - `chunk_and_embed.py` splits on sentence
  boundaries and would simply have real text to split.
- **Retrieval gets more selective, not less.** With bodies, keyword matching
  and Vector Search both start earning their keep; today they match against a
  headline, where almost any query looks equally relevant.
- **The KIND: ARTICLE / HEADLINE ONLY distinction stays.** Some sources will
  always be headline-only, and the model needs to know which.

Risks to weigh before doing it:
- **Terms of service.** Scraping article bodies is a different act from reading
  an RSS summary. Seeking Alpha was already ruled out on ToS grounds and that
  reasoning has to extend here rather than be quietly forgotten - the decision
  on 2026-08-30 was explicitly that taking their headlines while declining
  their transcripts would be picking whichever reading suited us.
- **Paywalls.** Many of the most useful outlets will return a stub or a consent
  wall, so the extractor must detect that and fall back to HEADLINE ONLY rather
  than treating boilerplate as body text.
- **A licensed API is the clean answer** if one fits the budget - it removes
  the ToS question and the extraction fragility together.

Until then the guard is what stands between a teaser and a confident invented
number, so it should not be removed when this lands - it should be the test
that proves this worked.

### Cross-account trends

With several accounts, surface the themes that cut across them rather than
rediscovering each one company by company.

**The sharper argument is repetition, not portfolio insight.** A rep plays
several of these back to back. If memory pricing is squeezing margins at three
accounts, they hear the same explanation three times. That does not only waste
minutes - it trains them to tune out, which undermines the recall loop the whole
project rests on. Cross-account awareness lets one episode carry the full
explanation and the others reference it in a sentence: "same memory pressure you
heard about on the NVIDIA episode, and here is how it lands differently here."
That alone justifies building it, even with no dedicated portfolio episode.

**The architecture is already half-shaped for this.** Industry trend chunks
carry the sentinel `account_id = '_industry'` precisely because they describe a
sector rather than a company, and macro deliberately never triggers a mode for
the same reason. Both are already signals that are not tied to one account. A
cross-account theme is the same shape, one level up.

**Detection can reuse what exists.** The cleanest signal is retrieval itself: if
the same trend chunk or macro condition is pulled as relevant for three of four
accounts on the same day, that is a shared theme by construction, with no new
inference needed. Correlated metric movement - margins compressing across the
portfolio - is the richer version and needs only the metric context tables that
already exist.

**The risk worth writing down.** Accounts in the same industry will always look
correlated. "All semiconductor companies face memory pricing" is a tautology,
not an insight, and a naive detector would surface it constantly. The useful
signal is a theme that cuts across sectors, or one that hits accounts
differently enough that the difference is the story. Whatever detects this needs
a way to tell a real cross-cutting theme from plain sector membership, or it
will produce confident noise.

Open questions:
- Where does it sit in the mode hierarchy? Probably below earnings and news, but
  plausibly above a quiet day, since a portfolio theme beats an empty topic queue.
- Does a portfolio episode get comprehension questions? The gaps would not belong
  to any single account's callback.

Blocked on having a second account, which is already needed for correctness
testing - every NVDA-shaped assumption in the concept priority lists, the
Operator-handover rule and the fiscal calendar handling needs a second company to
shake out. Cross-account trends need at least two or three before there is
anything to correlate.

## Planned decisions (phase 2)

### Enable native Lakebase CDF via external storage

**Why it is not done now.** Lakebase CDF (formerly Lakehouse Sync) refuses
destinations on Databricks Default Storage:

> Lakebase CDF is not supported for catalogs using Default Storage.
> Please use a catalog and schema backed by external storage.

This workspace is Free Edition. Its only catalog, `workspace`, is default
storage (`s3://dbstorage-prod-*` behind `__databricks_managed_storage_credential`),
and it is the only external location present. There is no second catalog to
point at, so this is structural rather than a permission that can be granted.

**Interim choice (2026-08-30).** A scheduled read job (`src/sync/read_recaps.py`)
moves recaps from Postgres to Delta. The recall loop closes and every other
success criterion holds. What is given up: native CDC, the SCD Type 2 history
table, update/delete capture, and seconds-level latency. Acceptable because
recaps are insert-only and the demo cares that the round trip works, not that
it is sub-second.

**What phase 2 requires** — the AWS side is the real work, and none of it is
Databricks configuration:

1. **S3 bucket** in the same region as the workspace (`us-east-2`), versioning
   on, public access blocked.
2. **IAM role** with a trust policy allowing the Databricks account principal to
   assume it, plus an `sts:ExternalId` condition matching the storage
   credential's external ID. Databricks issues that external ID only after the
   credential is created, so the role is created, then edited — a two-pass
   sequence that surprises people the first time.
3. **IAM policy** on the role granting `s3:GetObject`, `PutObject`,
   `DeleteObject`, `ListBucket`, `GetBucketLocation` on the bucket and its
   contents.
4. **Storage credential** in Unity Catalog wrapping the role ARN
   (`databricks storage-credentials create`).
5. **External location** binding an `s3://` path to that credential
   (`databricks external-locations create`).
6. **New catalog** backed by that external location — CDF writes here, not into
   `workspace`.
7. **CDF config**: `databricks postgres create-cdf-config <parent> <catalog>
   <schema> app` — see the CLI note below.
8. **Repoint the view**: `recall_recaps_current` moves from
   `bronze_recall_recaps` to `lb_recall_recaps_history` filtered to
   `_pg_change_type = 'insert'`. Delete the `sync_recaps` task. Grading code is
   untouched — that is what the view is for.

**Open risk.** Steps 4-6 may still be refused on Free Edition even with a valid
IAM role; the tier may block customer-managed storage outright. Verify by
attempting step 4 before doing any AWS work — it is a single CLI call and it
fails fast.

**CLI note.** The `databricks-lakebase` skill states Lakehouse Sync is UI-only
with no CLI or REST API. That is out of date as of CLI v1.14.1, which ships
`databricks postgres create-cdf-config` (beta). The blocker is storage, not
tooling, and phase 2 is fully scriptable.
- 2026-08-30 — XBRL concepts resolved against a priority list per metric, not a single hardcoded tag — NVDA reported revenue as `RevenueFromContractWithCustomerExcludingAssessedTax` through 2020 then switched to `Revenues`; hardcoding either truncates history at the switchover and makes the company appear to stop reporting
- 2026-08-30 — XBRL facts deduplicated on `(period_start, period_end)` keeping the latest `filed`, NOT on `fy`/`fp` — a 10-K restates the prior year as a comparative, so one economic quarter arrives under two fiscal years with identical values (164 raw NVDA revenue facts collapse to 63 real quarters)
- 2026-08-30 — The 10-K yields TWO outputs, not one: the reported full-year figure kept at annual grain as its own signal, and a derived Q4 (annual minus the three reported quarters) flagged `is_derived`. Q4 is never filed separately, so without derivation the quarterly series has a hole at every fiscal year end; without keeping the annual figure, an audited reported number is thrown away
- 2026-08-30 — QoQ and YoY computed by joining on explicit period dates with a tolerance, never `lag()` — positional lag over a gapped series compares against fifteen months back and labels it YoY. Measured on NVDA: lag(4) gave +118.4% for 2026-07-26, date-joined gives the correct +105.9%. Neither errors; the wrong one is simply a confident false number
- 2026-08-30 — Earnings call transcripts sourced from Roic AI free tier, after FMP's transcript endpoints returned HTTP 402 (paywalled, despite docs implying otherwise) and Seeking Alpha was ruled out on terms-of-service grounds. Roic's 2-year history cap is a non-issue against the trailing-4-quarter window already decided. Verified live with the project's own key, not from documentation
- 2026-08-30 — Transcript chunks follow SPEAKER TURN boundaries, not token counts — a turn is one complete thought by one person, and splitting mid-turn makes attribution ambiguous. Attribution is the point: "the CFO said margins compressed" is a different signal from "an analyst asked whether margins compressed"
- 2026-08-30 — Speaker `role` derived from the Operator handover, NOT from who speaks before the Q&A boundary. The obvious rule fails on real data: in NVDA FY2027 Q2 only the CFO gave prepared remarks, so "first appearance before Q&A" labels CEO Jensen Huang an analyst — the worst available misclassification, since his framing is what the briefing exists to contrast. The Operator names each analyst immediately before they speak, so the turn directly after a handover is the analyst; everyone else non-operator is management
- 2026-08-30 — API keys read inside tasks via `dbutils.secrets.get`, never passed as task parameters — Databricks does not substitute `{{secrets/...}}` into `spark_python_task` parameters (it arrives as the literal string and fails with 401), and argv is echoed into run logs even where substitution does work
- 2026-08-30 — Q4 is derived only when it was not actually filed. The earlier assumption that Q4 is never reported separately is wrong: NVDA filed Q4 as its own quarterly fact in 9 of 19 years for revenue, gross profit and net income, and in 0 of 19 for operating income. Deriving unconditionally would have produced two Q4 rows for the same period in half the years
- 2026-08-30 — Quarters collapsed on `period_start` as well as `(period_start, period_end)`, keeping the newest filing. NVDA's 2010 Q2 was filed twice with period ends one day apart and identical values; both survived the original dedupe. Left alone, a year with two real quarters plus a duplicate counts as three and produces a wrong derived Q4
- 2026-08-30 — Risks recorded in ARCHITECTURE.md under "Known risks" rather than left in conversation. Each entry states what could go wrong, how it would be noticed, and what to do — so the project can be picked up cold
- 2026-08-30 — Transcript turns split into ~1,500 character parts on sentence boundaries. Not a capacity fix: the embedding endpoint measured at 8192 tokens (~56,000 chars) and the longest turn is 18,376 chars. Split for retrieval quality — a single vector over an 18,000 character turn averages twenty topics, so a query about margins matches the whole block instead of the two parts that discuss margins. Verified lossless: 2,894 words in, 2,894 words out across 13 parts
- 2026-08-30 — Noted that `databricks-gte-large-en` truncates silently at 8192 tokens rather than erroring. Nothing in a transcript approaches that, but a future long-document source could be half-embedded with no signal that it happened
- 2026-08-30 — The prompt only mentions derivation when a figure actually is derived. Mentioning it unconditionally made the model announce that "all of these figures are derived from the SEC XBRL filings" when all five were filed as reported. The prompt now states explicitly which case applies, and forbids the words when nothing is derived
- 2026-08-30 — Briefing retrieval is a plain SQL read, not Vector Search. With one account the whole call is 32 chunks and fits in a 43,000 character prompt. Vector Search earns its place when accounts and sources grow, not before
- 2026-08-30 — Only management turns go into the briefing prompt. Analyst questions set up the answers but are not management framing, and including them roughly doubles the prompt for no gain
- 2026-08-30 — Briefing prompt rewritten for the ear, using The Daily and Unexplainable as references: open cold on the most consequential fact, one continuous narrative with spoken transitions instead of headings, build toward the gap between the numbers and management's framing, and land on the open question rather than a summary. Voice rules: short sentences, active voice, action verbs, one idea per sentence, no hedges
- 2026-08-30 — Added a programmatic format guard after generation that flags markdown bullets, numbered lists, headings, bold, leaked speaker tags, and metadata labels. The model dropped eight bullet points into an early version despite the prompt forbidding them, so the rule is checked rather than trusted
- 2026-08-30 — Chunk metadata reformatted from `[Speaker, section]` to `SPEAKER: x | SECTION: y | SAID: z`, because the model read the bracketed form aloud verbatim as part of the script
- 2026-08-30 — Comparison rule tightened to name the basis. The model compared cost of revenue up 86.8% year-over-year against revenue up 17.9% quarter-over-quarter and concluded costs were outrunning sales; on the same year-over-year basis revenue rose 105.9%, so the conclusion was inverted. Prompt now requires both sides of any comparison to share a basis and to say which
- 2026-08-30 — Numbers written as digits and rounded to one decimal ($96.2 billion, not $96.221 billion). An earlier instruction to "say numbers as a person would" produced "ninety-six point two two one billion dollars" spelled out in words, which is worse for TTS, not better
- 2026-08-30 — Prompt forbids stating any fact not supplied. An early version opened by claiming NVIDIA "shipped more than 99 gigawatts of AI compute", a figure that appears nowhere in the data
- 2026-08-30 — Briefing mode (A high-signal / B targeted / C deep-dive) selected in SQL from `silver_daily_signals`, not by the model. Code picks the mode, the prompt carries the narrative architecture for it. A wrong mode is then a SQL bug and a weak script is a prompt bug, which keeps them diagnosable separately. An agent deciding both at once would blur that line and trade auditability for nothing
- 2026-08-30 — Four-phase narrative architecture (cold open and callback, core analysis, macro context, playbook and open question) adopted from the producer prompt, with a mode-specific core swapped into phase 2. Phases are never spoken aloud
- 2026-08-30 — Phase 3 instructs the model to SKIP the macro section rather than write market context from its own knowledge, because no macro source is wired up. Letting it fill the gap would break the grounding rule that the whole project rests on. It switches on automatically when the macro ingest lands
- 2026-08-30 — Modes B and C are built but currently unreachable: B needs news or ratings signals, C fires only when nothing lands. `silver_daily_signals` carries those columns as zeros so adding a source is a change to one file and nothing downstream
- 2026-08-30 — TTS narration speed set to 0.8, and made a job parameter. Measured: speed 1.0 reads at 265 words per minute, 0.8 at 213. Normal narration is 150-160, which would need roughly 0.6. Speed is separate from the script so voice and writing can be tuned independently
- 2026-08-30 — `tts.py` runnable on its own against `gold_briefing_current`, so narration settings can be tested without regenerating the script. Previously every voice test also changed the words and neither variable could be isolated
- 2026-08-30 — Added `silver_metric_context`: margins, basis-point moves, and the relationships between metrics (did costs outrun revenue, is growth accelerating, is net income diverging from operating income), all computed in SQL. The briefing was reciting the earnings call because it was handed a table of five metrics and nothing about what they meant. Same discipline as the deltas: if the model has to derive that margins compressed it can get it wrong, so hand it the relationship already calculated and let it explain why
- 2026-08-30 — Mode A prompt rewritten around Metric to Context to Signal, with an explicit instruction to SELECT two or three metrics rather than list all of them. A rep cannot hold five metrics and ten growth rates in their head, and a script that recites all of them is a table read aloud - if the listener could get the same thing from the earnings call itself, the briefing has failed
- 2026-08-30 — Arithmetic rule loosened precisely: the model may say which of two given numbers is larger or that one moved while another held, because that is reading the data. It still may not add, subtract, divide, or produce a figure it was not handed. The previous absolute ban on arithmetic had squeezed out interpretation along with fabrication
- 2026-08-30 — Computed context states its DIRECTION IN WORDS (EXPANDED, COMPRESSED, ACCELERATING, SLOWING), never a signed number plus a legend. Handed "+20.6 percentage points (negative means growth is slowing)", the model reported that growth SLOWED by 20.6 points when it had accelerated by that much, and built a paragraph, a strategic signal and a discovery question on the inversion. It read the magnitude and guessed the direction. Never ask a model to apply a sign rule to a number that matters
- 2026-08-30 — Audience widened: the briefing must be understandable to a smart person who does not do this for a living, not only to a rep fluent in financial statements. Terms of art get a one-clause explanation on first use, and mechanisms get analogies. The reasoning is practical rather than aesthetic - nobody listens to something boring or over their head, so density that does not land is not density
- 2026-08-30 — Analogies may ILLUSTRATE a supplied fact but never introduce one. Comparing margin compression to a squeezed pipe is fine; invoking another company's situation or any real event, product or figure not handed to the model is fabrication in a comparison's clothes. Analogies are restricted to everyday things - kitchens, traffic, rent - never to other companies or markets
- 2026-08-30 — The playbook questions are framed as advising the ACCOUNT'S CEO. They were vague because the counterpart was never named: unclear whether the rep was asking NVIDIA, a customer of NVIDIA, or which stakeholder. MVP assumes one meeting with the person who decides, and each question must say which number or which piece of management framing gives the rep standing to ask it. Other personas are parked
- 2026-08-30 — Format guard extended to catch ANY bracketed aside. Adding named phases to the prompt made the model label its own sections in the script - "[Cold open, no preamble]", "[Core analysis - first metric]" - which the voice engine would read out as "open square bracket". The guard only looked for leaked speaker tags and missed them
- 2026-08-30 — VOICE_RULES now opens with a blunt prose-only rule rather than a list of banned formats. Each time a specific format was banned the model found another list shape: bullets, then bracketed labels, then a numbered recap. The rule now states the principle - there is no page, anything typed is spoken - and gives the boundary: "first" and "second" inside a sentence are fine, a line starting "1." is not
- 2026-08-30 — Script must never refer to the rep in the third person. It is spoken TO them, so "the takeaway you want to leave the rep with" breaks the frame; there is no one else in the room
- 2026-08-30 — a16z dropped as a trend source: no RSS feed exists at any standard path (/feed/, /rss/, /rss.xml, /blog/feed/, /index.xml, /posts/feed/ all 404). Replaced with IEEE Spectrum's semiconductors feed, which is closer to this account's actual industry, plus MIT Tech Review AI
- 2026-08-30 — SemiAnalysis kept despite being stale (latest RSS post September 2025). Its subject matter is the best fit of any source, and everything downstream filters on publish date, so a dormant feed contributes nothing rather than contributing something a year old
- 2026-08-30 — Macro sourced from FRED's public CSV endpoint rather than its keyed JSON API. No registration, no secret to rotate, and the five series cover rates, the yield curve, capital spending and inflation. Export policy has no clean series and is left to the news and trends feeds
- 2026-08-30 — Industry trend chunks carry the sentinel account_id `_industry` rather than a symbol. They describe the industry an account sits in, not the account, so relevance is a retrieval decision rather than an ingestion one
- 2026-08-30 — Feed dates parsed by regex extraction, not a datetime pattern. Spark 3 rejects the RFC-822 weekday token outright ("Illegal pattern character found: E"), and the briefing only needs the day
- 2026-08-30 — SECOND occurrence of the DLT expectation bug: `macro_context` declared `expect_or_drop("value IS NOT NULL")` while the output column is `latest_value`. Expectations are evaluated against the returned dataframe, so they can only name columns that survive the final select
- 2026-08-30 — News sourced from Google News search RSS (per account query) and Yahoo Finance (per ticker). Seeking Alpha publishes a working per-ticker feed and is deliberately excluded: it was ruled out on terms-of-service grounds when we looked for transcripts, and taking their headlines while declining their transcripts would be picking whichever reading suited us
- 2026-08-30 — News and rating changes now feed mode selection, which makes Mode B reachable for the first time (103 days qualify). Macro deliberately does NOT trigger a mode: it describes the world rather than the account, so a rate move is never on its own a reason to brief about one company. Ratings only count when the rating actually moved — a firm reiterating its view is not a signal
- 2026-08-30 — Deduplication added to transcripts, trends, news and ratings in Silver. Bronze is append-only by design, so three ingest runs in one day had tripled the trend items, doubled the transcript turns and doubled the rating actions. The XBRL path had dedupe from the start and the newer sources inherited none of it — appending is only safe when every consumer dedupes
- 2026-08-30 — Automatic re-granting of the app's Postgres access was attempted and reverted. Any psycopg connection inside a serverless task kills the Python kernel, so the briefing job was losing its audio to fix a grant. Confirmed with an isolated minimal job, and identical with psycopg2, so it is the platform rather than the driver
- 2026-08-30 — The briefing job now triggers the Lakebase sync pipeline after rebuilding the serving table. SNAPSHOT scheduling does not refresh on its own, so the app was showing the previous episode while the new one sat in Unity Catalog
- 2026-08-30 — Narration voice set to `nova` and speed returned to 1.0. The earlier 0.8 was compensating for a words-per-minute figure derived from a misparsed mp3 bitrate; measured against the player's own duration the real rate is about 153 wpm, which is normal narration
- 2026-08-30 — Mode B was firing on news signals while the prompt received no news at all. Only transcript and industry-trend chunks were ever retrieved, so a news day produced an earnings recap labelled "Today's news" - the label was honest about the trigger and dishonest about the content. News is now retrieved, and on a Mode B day the block order flips so news leads and the filings are labelled background
- 2026-08-30 — On a Mode B day the earnings transcript is trimmed to four prepared-remarks chunks. Passing all 32 buried 18 headlines by sheer volume: the model wrote about whatever it was given most of
- 2026-08-30 — Every non-filing source now carries its publication and headline into the prompt, and the script must name both. "A recent report" is banned: an unattributed claim is one the listener cannot check, which in a briefing built on traceable sources is the one thing that cannot be sloppy
- 2026-08-30 — The one-figure-per-paragraph rule moved from MODE_A_CORE into the shared VOICE_RULES. It had been written into a single mode's core, so on a Mode B day it never applied - which is why the restructure appeared to do nothing at first
- 2026-08-30 — Narration voice set to `ballad` with delivery instructions passed to gpt-4o-mini-tts. The instructions parameter steers pace and emphasis without changing a word of the script, and its absence was most of what made earlier versions sound synthetic
- 2026-08-30 — Scheduled: ingest 05:00 ET weekdays, briefing 05:45, grading 20:00. The cadence follows when the sources change - filings land after the close, calls are held after hours, overnight news is in the feeds by dawn - so a 05:00 pull means the commute briefing covers everything that happened overnight. Weekdays only, because nothing files on a Saturday and pulling anyway burns free-tier quota
- 2026-08-30 — The Bronze to Silver pipeline runs as a task inside the ingest job rather than on its own schedule. On separate clocks the raw data would refresh while every derived table stayed stale, which is the failure that looks most like everything working
- 2026-08-30 — Schedules explicitly UNPAUSED in the dev target. `mode: development` pauses them by default, which is right for iterating and wrong for a system meant to demonstrate that it runs on its own
- 2026-08-30 — Second and third accounts added (Alphabet, Micron), and both broke NVDA-shaped assumptions immediately: fiscal calendars differ, so asking every company for the same fiscal year and quarter 404s for most of them (now tries up to eight recent periods per account); and one symbol exhausting FMP's free tier was failing the whole ratings task rather than the other two continuing
- 2026-08-31 — News items are kept only if the company is actually named in the title or summary. Yahoo Finance's per-ticker feed does not filter by ticker: a request for NVDA returned Hershey, Warren Buffett and Dow Jones headlines, and because those were passed to the prompt they were then listed as sources the episode had used. The filter cut NVIDIA's Yahoo feed from 19 items to 5
- 2026-08-31 — `gold_briefing_audio` appends instead of CREATE OR REPLACE. With three accounts generated in sequence the table was rebuilt each run, so only the last account kept its audio and the other two published silent. The same bug had already been fixed on `gold_briefing` and was not looked for here
- 2026-08-31 — Industry trend chunks are only retrieved when there is a requested subject to match them against. On a news day "the ten most recent industry items" put unrelated coverage into the prompt, which is how a McKinsey piece about Moderna became a cited source on an NVIDIA episode
- 2026-08-31 — Episodes are hidden, not deleted. `gold_briefing_serving` is a synced table and Databricks permits only reads, indexes and DROP on those, so the DELETE could never have worked. The hide is recorded and every read filters on it, which also survives the next sync
- 2026-08-31 — Per-episode lineage records the actual items used, not only counts: which metrics were selected, which relationships were computed, which headlines and transcript passages went in. It is the same JSON already travelling with the episode, so the detail costs no extra query path and stays correct because the run that used the data writes it
- 2026-08-31 — The audio endpoint implements real HTTP range requests (206 with `Content-Range`, 416 past EOF) instead of only advertising `Accept-Ranges`. The header without the behaviour is what broke playback: Chrome asks for a range, gets a 200 carrying the whole file, and stalls at `readyState 0` with no duration - the 0:00/0:00 player. The bytes were never the problem, a plain fetch pulled all 5 MB in 1.3 seconds. Either honour the header or do not send it
- 2026-08-31 — Range parsing treats a malformed `Range` header as no range (serve 200) and only a well-formed but out-of-bounds one as 416, per RFC 7233. `bytes=-100` means the LAST 100 bytes, not the first 100, and multi-range requests are served whole rather than as `multipart/byteranges`, which no media element asks for
- 2026-08-31 — The app's Lakebase resource is declared as `postgres:` (branch path), NOT `database:` (instance_name). They are near-identical config for two different backends, and the wrong one failed every `bundle deploy` with "Database instance account-signals-dev does not exist" - true, because the store was always a Lakebase Autoscaling project. Both `branch` and `database` want full `projects/.../branches/...` resource paths, and the database ID is hyphenated (`databricks-postgres`) while the Postgres database it maps to is underscored (`databricks_postgres`)
- 2026-08-31 — `pg()` mints an OAuth token when `PGHOST` is injected but `PGPASSWORD` is not. Lakebase Autoscaling injects host and user and authenticates with a short-lived token; only the legacy instance resource injects a static password. Attaching the resource therefore took down every endpoint at once with `KeyError: 'PGPASSWORD'`, because the code treated `PGHOST` as proof that a full set of credentials had arrived
- 2026-08-31 — Deploys are verified by exercising the endpoint, not by reading the deploy's exit status. `apps deploy` reported "App started successfully" while every route 500'd, because a successful start says nothing about whether the app can reach its database
- 2026-08-31 — The app's Lakebase resource is templated from bundle variables (`lakebase_project`, `lakebase_branch`, `lakebase_database`), so that path follows whatever workspace it is deployed into. Two of those variables were declared and never referenced, which made the config look parameterised while the real values sat hardcoded a file away
- 2026-08-31 — The app's env stays in `src/app/app.yaml` rather than moving to the bundle's `config:` block. The block is accepted by the bundle schema and silently ignored by this workspace's Apps API: with config in the bundle and no app.yaml the app received no start command and exited immediately, and `apps get` returns no config field at all. Documented as a hand-edit in the README rather than left looking templated
- 2026-08-31 — CI runs offline checks only, with no workspace credentials. `databricks bundle validate` cannot run in GitHub Actions: under `mode: development` it makes a live SCIM call to resolve the username for resource prefixing, so it needs a real token. Storing one in a public repo to lint YAML is a bad trade, so the workflow runs `compileall`, the unit tests, and a schema check built on `databricks bundle schema`, which needs no auth
- 2026-08-31 — The offline schema check rewrites the CLI's `\p{L}` regexes into Python-compatible ones rather than deleting them. Deleting compiles, but it leaves the `${var.x}` branch of each `oneOf` matching everything, which silently disables every enum check — verified by planting `permission: CAN_DO_ANYTHING` and watching it pass clean. A check that cannot fail is worse than no check, so each guard was tested against a deliberately broken config before being trusted
- 2026-09-01 — Grading targets the episode the rep ACTUALLY HEARD: the most recent one generated at or before the recap. It took the newest episode outright, so recap 1 was scored against a script generated about twenty hours after the rep spoke — and measured on today's data all four recaps would be graded against an episode from days later. Where no episode existed at recap time the recap is left ungraded with a printed reason, because a score against a script they never heard is not merely wrong, its gaps become the next episode's cold open
- 2026-09-01 — News relevance is re-applied in Silver, not only at ingest. Bronze is append-only, so fixing the ingest filter never retired the 56 off-topic chunks already landed — SK Hynix and Druckenmiller under GOOG, Moderna and Venezuelan oil under NVDA — and their recent publish dates meant no recency window excluded them either. Both filters are needed: one stops new ones arriving, the other stops old ones being used. Measured after: NVDA 29 to 0, GOOG 14 to 0, MU 13 to 0
- 2026-09-01 — The two copies of the relevance terms are pinned together by a test rather than a shared import. The DLT pipeline and the ingest tasks have different import paths, so the lists are duplicated deliberately and `tests/test_news_relevance.py` fails if they drift
- 2026-09-01 — MU's `\bmu\b` term is a RAW string. Written plain it is a backspace character, not a word boundary, so the branch never matched and the filter was effectively `micron` only — any headline saying just MU was dropped at ingest and is unrecoverable from Bronze
- 2026-09-01 — The ratings ingest rotates its account order by day and reports partial failure loudly, failing outright only when every account fails. FMP returns 402 partway through the list once the free tier is reached, and with a fixed order the same accounts lost every time: NVDA had 6,828 rating rows while GOOG and MU had ZERO, in every run, and the task still reported SUCCESS. The per-symbol counts were also the running totals, so every log line after the first overstated its symbol
- 2026-09-01 — The not-hidden clause is one constant used at all eight read sites. It had been written by hand at two of them, so hiding an episode made the text fall back correctly while the play button still streamed the hidden audio and the episode count still counted it. Four of the eight call sites were plain strings rather than f-strings, which would have put the literal text `{NOT_HIDDEN}` into the SQL — checked programmatically rather than by eye
- 2026-09-01 — `/api/grade` reads Unity Catalog rather than Postgres. It queried `app.gold_recall_grades` in Lakebase inside a bare `except: return {}` commented "absence is normal" — but that table is not synced there and never has been, so the endpoint returned an empty object for every account on every call, permanently. The write-back is Postgres to UC by design; only the briefing travels the other way. An empty result now means genuinely no graded recap, and a failure is reported as one
- 2026-09-01 — Audio rows were backfilled for 13 episodes whose MP3 survived in the Volume but whose pointer was lost to the old CREATE OR REPLACE. Reconstructed from the filenames, which are the briefing_id, and inserted with a LEFT ANTI JOIN so the repair is idempotent. `voice`, `speed` and `tts_model` are left NULL rather than guessed — the settings changed across that period and inventing them would put a false claim in the provenance record. NVDA's past episodes went from 5 playable of 16 to 15 of 17; the two that remain have no file anywhere and would need re-narration
- 2026-09-01 — `silver_metric_context` compares periods by DATE JOIN, not `lag()`. This file had reintroduced the exact positional-lag bug that the deltas were fixed for on 2026-08-30, one layer down, on the numbers that become the spoken direction words. Measured: the `lag(4)` target was not a year back in 121 of 220 rows, and NVDA 2025-10-26 published gross margin EXPANDED by 38.5 bps when it had COMPRESSED by 114.5. The same fix twice in one codebase says the rule belongs in a shared helper, not in each file's discipline
- 2026-09-01 — `silver_metric_context` groups on (symbol, period_end) only, never including `fiscal_year`. That column in the deltas is the year of the FILING a fact survived dedupe in, not of the period, so it differs between metrics for the same quarter — which fanned 186 real periods into 220 rows, blanked gross margin on 5 periods entirely because revenue and gross_profit landed in different groups, and left `synthesize.py`'s unordered `ctx[0]` choosing arbitrarily between a populated row and an empty one. Now 186 rows for 186 periods
- 2026-09-01 — The period join breaks ties on `period_end` as well as closeness, so a full refresh cannot silently change a published number. The previous window had no tie-breaker and duplicate rows existed, so the same quarter could return +38.5 or NULL depending on sort order
- 2026-09-01 — Verified by recomputing all 186 rows independently from `silver_financial_deltas`: 0 mismatches. The first attempt at that check reported 1 mismatch and compared 188 rows against 186 — the verification query itself was double-matching partners because it did not pick the closest one. A check that disagrees with production is as likely to be the broken half; recompute the recomputation before believing it
- 2026-09-01 — The industry-trend retrieval is scoped to `account_id IN ('_industry', <account>)`. The query selected `source_type IN ('industry_trend', 'news')` with no account filter, so the news half swept up every other account's articles: one Micron episode cited 9 NVDA items and 1 GOOG item out of 28 - Venezuelan oil, a congressman selling Alphabet stock - and `/api/sources` presented them to the rep as the sources Micron's episode used. Measured after the fix on the same account: 32 of 32 sources belong to MU
- 2026-09-01 — Provenance counts are scoped to the account wherever the source has one, and the two that do not (industry trends, macro) say so explicitly rather than appearing per-account. Table-wide counts meant a GOOG episode reported "6,828 analyst rows, ingested today" when zero were GOOG - FMP's free tier is exhausted by NVDA's backfill before GOOG is reached, so that account has no ratings at all. The one panel whose whole purpose is honesty about sources was quoting another company's numbers; it now reads 0
- 2026-09-01 — Retrieved items are labelled `KIND: ARTICLE` or `KIND: HEADLINE ONLY`, and the prompt states what may be done with each: a headline may be reported and quoted, never elaborated into claims, figures, causes or consequences. 410 of 430 news chunks are 300 characters or fewer, and every fabricated figure the audit found sat on one. Labelled with a `KIND:` field rather than a bracket, because the model reads brackets aloud — the same reason transcript chunks use `SPEAKER:/SECTION:/SAID:`
- 2026-09-01 — A grounding guard checks every figure in the finished script against the material the model was handed, and warns on any that appear nowhere. The rule "never state a fact you were not given" is the one the project rests on, so it is checked rather than trusted — the same reasoning as the format guard, which exists because the model produced bullets after being told not to. Rounding to one decimal is tolerated (1%, or 0.05 for small values) because the prompt asks for it; a guard that flags rounding would cry wolf every episode. Spelled-out figures are reported separately for a human to read, because the original "ninety-nine gigawatts" fabrication contained no digits and survived every numeric check
- 2026-09-01 — The guard was proved on real fabrications before being trusted, not on invented examples: run against the episode that shipped the IPO claim it flags exactly the invented 1999 and $200,000 while leaving the genuine $1,000 alone, and it accepts four real rounding cases. A guard that cannot be shown to fire is indistinguishable from no guard
- 2026-09-01 — The cold-open callback reads gaps from BOTH the comprehension questions and the free-form recap, and simply takes whichever is newer. It previously read only `gold_recall_grades`, which only the free-form recap writes — and that path has never had a UI, so the loop was closing on the path nobody could use while every gap a real person generated was ignored. Plain recency avoids inventing a precedence rule and keeps working unchanged if the recap ever gets a UI
- 2026-09-01 — A recorded gap is trimmed to its first point before it reaches the prompt. The two sources write `missed` in different shapes, and comprehension grading got more verbose as the prompt grew: early answers stored ~110 characters, later ones store the gap, a blank line, then a paragraph explaining it — up to 820. Handed the whole thing the cold open recites an essay before reaching the news, which is the same failure that made the recap path pass one ranked gap rather than the whole list. Verified against all nine recorded gaps
- 2026-09-01 — A free-form recap UI was considered and NOT built. The comprehension questions already measure recall, record the missed point, and round-trip to Unity Catalog, and they are what people actually use. What the free-form recap uniquely measures is UNAIDED recall — nobody hands you the questions before a customer meeting — which is a real difference but a refinement, not an MVP requirement. The endpoint stays, documented as having no UI rather than left looking live
- 2026-09-01 — `cost_of_revenue` accepts `CostOfGoodsAndServicesSold` as well as `CostOfRevenue`. Companies pick one tag and keep it: NVDA files 3,360 CostOfRevenue facts, MU files 1,278 CostOfGoodsAndServicesSold and ZERO CostOfRevenue. The single-tag list gave Micron no cost line at all, so "did costs outrun revenue" never appeared in a Micron briefing — 70 of 70 rows NULL, now 7. The priority order also fills NVDA's one hole at 2019-01-27
- 2026-09-01 — `gross_profit` is DERIVED from revenue minus cost of revenue where a company does not tag it. Alphabet never files `GrossProfit` — zero facts, ever — so gross margin was NULL on every GOOG row; it is now populated on all 47. This is the metric's definition rather than an estimate, so it is arithmetic of the kind this pipeline exists to do, and it is derived only where not filed, for the same reason Q4 is never derived twice. Verified against MU, whose filed GrossProfit equals revenue minus cost exactly
- 2026-09-01 — Briefing mode comes from the STRONGEST signal in a two-day window, not from `max(signal_date)`. Those differ in exactly the case that matters: a company reports four times a year and files after the close, news arrives every day, and the briefing runs 05:45 the next morning — so the newer news-only row won and the quarter was skipped. Measured on NVDA's real 2026-08-26 earnings day: run on the 27th the old logic picks B and the new picks A; by the 29th both pick B, so the window closes on its own
- 2026-09-01 — Mode A had never produced an episode. 178 A-days exist in `silver_daily_signals` — correctly four per company per year over 17 years of filings — but no briefing had ever run on or near one, so the entire Mode A prompt path was unexercised in production. Forced once to check: it works, and trips the density guard on 4 sentences against Mode B's 1, which is the metric-heavy failure that mode is most prone to and worth watching
- 2026-09-01 — Macro observations carry their unit into the prompt, keyed on FRED `series_id`. They were passed as bare numbers, so "Private nonresidential fixed investment: 4623.36" became the spoken "climbing to 4 623" — which a listener hears as "four, six twenty three", and which the grounding guard flagged because the space split the number in two. A figure with no unit is not a fact, it is a digit string, and the model will mangle it or invent a unit rather than leave a gap. Now: "$4.62 trillion at an annual rate, climbing $132.8 billion"
- 2026-09-01 — A level and a MOVE carry different units: a yield at 4.73 percent that rises 0.28 does so in PERCENTAGE POINTS, not percent. The two are separate fields per series rather than one, because that distinction is exactly the kind a briefing states confidently and gets wrong
