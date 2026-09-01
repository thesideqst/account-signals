# src/briefing/CLAUDE.md

Scoped to this directory only. Root [`/CLAUDE.md`](../../CLAUDE.md) already
covers the SQL-not-LLM invariant and that retrieval is a plain SQL read, not
Vector Search — read that first. This file is what's specific to working in
`retrieve.py`, `prompts.py`, `synthesize.py`, `tts.py`.

## `retrieve.py` has no code

It's a docstring plus two `# TODO` lines (the `VectorSearchClient` call and
per-`source_type` quota enforcement) — no `main()`, nothing executes. The
`retrieve` task in `resources/briefing.job.yml` still runs it every day; it
just succeeds trivially and does nothing. Every actual query — deltas,
`silver_metric_context`, transcript chunks, macro series, news, industry
trends — is inline in `synthesize.py`'s `main()`. If you're changing what gets
retrieved, that's the file to edit; `retrieve.py` is where the Vector Search
migration is meant to land, not where today's retrieval lives.

## Mode and metric selection are code, not prompt, in `synthesize.py`

`prompts.py` only assembles text from what it's handed — it never picks
anything. The selection logic worth knowing before touching either file:

- **Metrics**: revenue always goes in, plus the two other metrics whose
  `yoy_pct` diverges most from revenue's own `yoy_pct` (`divergence()` in
  `synthesize.py`). Mode C then trims even that down to revenue-only, for
  scale — a deep dive isn't allowed to see margins or growth rates at all.
- **Mode C topic matching** against `silver_doc_chunks` for industry trends
  is keyword `LIKE` matching (words >4 chars from `requested_topic`, first 8,
  OR'd together) — an explicit placeholder for Vector Search per the comment
  above `topic_filter`, not a real relevance ranking.
- **Mode B** swaps block order: the news block is labelled "THIS IS THE
  SUBJECT" and goes first; the deltas block becomes "BACKGROUND FIGURES - NOT
  TODAY'S SUBJECT". Transcript chunks are also cut to the first 4
  `prepared_remarks` chunks (all modes already filter to `role = 'management'`
  only — analyst questions never reach the prompt as framing).
- **Callback**: only the single highest-importance gap from the latest
  `gold_recall_grades` row is passed to the cold open, not the full gap list
  — handing over the whole JSON array produced a scolding, eight-item open.

## The format guard only warns — it never blocks or retries

After generation, `synthesize.py` regex-scans the script for bullets,
numbered lists, headings, bold/italic, leaked `SPEAKER:`/`SECTION:` tags, and
bracketed stage directions, plus sentence/paragraph figure-density. All of
these `print(...)` a `FORMAT WARNING` / `DENSITY WARNING` to the job's stdout
and let the run finish and write to `gold_briefing` regardless. A green job
run is not proof the script is clean — check the run's stdout for those
strings, or read the script.

## Two extra model calls per episode, parsed by hand

Beyond the script call (`temperature=0.4`, `max_tokens=4000`),
`synthesize.py` makes two more against the same endpoint: `EPISODE_META_PROMPT`
(title + 3 takeaways, `temperature=0.6`, `max_tokens=1200`) and
`QUESTIONS_PROMPT` (3 comprehension questions, `temperature=0.4`,
`max_tokens=1600`). Both expect bare or fenced JSON and are parsed by slicing
between the first `{` and last `}`; a malformed response just logs a
`WARNING could not parse...` and leaves `episode_title`/`takeaways`/
`questions` empty (title falls back to `"{account} briefing"`) — it does not
fail the task.

## `tts.py` calls OpenAI directly, not a Databricks endpoint

Per ARCHITECTURE.md, speech is off-platform — concretely, that means a plain
`urllib` POST to `https://api.openai.com/v1/audio/speech`, model
`gpt-4o-mini-tts`, voice `"ballad"`, both hardcoded module constants. Input is
capped at 4000 characters (the endpoint's hard limit is 4096); a script is
split only on paragraph boundaries (`split_for_speech`), never mid-sentence,
and the resulting MP3s are joined by raw byte concatenation — no ffmpeg, no
crossfade, no re-encode, which is deliberate for serverless. Retries on HTTP
429/500/502/503 up to 4 tries with `5 * attempt` second backoff; any other
HTTP error raises immediately with the response body in the message.

## Gotchas verified in code

- **`regrant_app_access()` in `tts.py` is dead code.** It's defined but never
  called from `main()`, and it references `LAKEBASE_ENDPOINT` and
  `APP_SP_CLIENT_ID` — neither is defined or imported anywhere in this file.
  Calling it as-is raises `NameError`. The manual re-grant it documents (see
  root CLAUDE.md and ARCHITECTURE.md "Known risks") is a real, still-needed
  step; this function is a sketch of the fix, not a working one.
- **`EXPECTED_BRIEFING_ID` is a race guard, not a bug.** If another run's
  `synthesize` step overtook this one, `tts.py` compares
  `gold_briefing_current`'s `briefing_id` against `$EXPECTED_BRIEFING_ID` and
  exits without narrating rather than attaching audio to a briefing that's no
  longer current. A "missing audio" episode can be this guard working as
  intended — check the task log for the "another run produced a newer
  briefing" message before assuming narration failed.
- **`"account_signals_pg"` is a hardcoded literal in `tts.py`**, duplicating
  the `LAKEBASE_CATALOG` constant defined in `src/sync/read_recaps.py`. If the
  Lakehouse Federation catalog name for the Lakebase project ever changes,
  both places need updating by hand — nothing ties them together.
