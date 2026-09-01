# src/grading/CLAUDE.md

Scoped to this directory. Root [CLAUDE.md](../../CLAUDE.md) covers the
sync -> grade pipeline path and why an LLM is allowed to score here; this is
what's specific to `grade.py` itself. See also [ARCHITECTURE.md](../../ARCHITECTURE.md)
("The callback loop is broken by time") and [resources/grading.job.yml](../../resources/grading.job.yml).

## What "ungraded" means here

The `todo` query in `main()` grades **every** recap missing from
`gold_recall_grades`, not just the latest one per account — if three recaps
piled up ungraded, all three get graded and all three get a row. The "single
most recent graded recap, applied once" rule from SCOPE.md is enforced
downstream, in `src/briefing/synthesize.py` (`ORDER BY graded_at DESC LIMIT
1`), not here. Don't add most-recent filtering to the `todo` query — that
would just mean older recaps never get graded at all.

On first run, `gold_recall_grades` doesn't exist yet, so the query falls back
to grading every row currently in `recall_recaps_current`.

## What gets judged

A recap is graded against **the latest `gold_briefing` row for that
`account_id`** (`ORDER BY generated_at DESC LIMIT 1`) — not necessarily the
briefing the rep actually listened to. If a second briefing ran between the
listen and the recap, grading silently targets the newer one.

`JUDGE_PROMPT` instructs the model to: weight gaps by importance, not word
count; give neither credit nor penalty for true statements the briefing never
made (only judge against what was actually said); and phrase each gap so it's
sayable as a cold-open line, not a rubric ("missed the memory pricing
pressure on next quarter's margins", not "needs more detail on financials").

## Response shape and parsing

Expected JSON:
```json
{"accuracy": 0-100, "covered": ["..."],
 "gaps": [{"point": "...", "importance": "high|medium|low"}],
 "wrong": ["..."], "one_line": "..."}
```
`importance` must stay one of `high`/`medium`/`low` (case-insensitive) —
`synthesize.py` ranks gaps by that exact string to pick the one it surfaces
(`rank = {"high": 0, "medium": 1, "low": 2}`); anything else sorts last.

Parsing is defensive, not strict: strips a code fence if present, then slices
from the first `{` to the last `}` before `json.loads`. A recap whose verdict
fails to parse is logged and skipped (`continue`) — nothing is written for
it, so it isn't lost, it's just picked up again by the `todo` query on the
next scheduled run (20:00 ET weekdays, per `grading.job.yml`).

`extract_text()` exists because `gpt-oss` returns `content` as reasoning
parts, not a plain string — the 2000 `max_tokens` budget has to cover that
reasoning plus the JSON.

## Write target

Appends to `{catalog}.{schema}.gold_recall_grades`: one row per graded
recap (`recap_id, account_id, rep_id, briefing_id, accuracy, covered, gaps,
wrong, one_line, graded_at`), with `covered`/`gaps`/`wrong` stored as JSON
strings, not structs. Append-only — never updated or deduped after write.
