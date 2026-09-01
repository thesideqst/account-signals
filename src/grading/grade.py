"""Spoken recap -> grade and gaps. Closes the loop.

Input arrives the long way round: the rep speaks a recap into the app, it lands
in Lakebase Postgres, and read_recaps.py brings it back into Unity Catalog
through Lakehouse Federation. That round trip - UC to Lakebase for reads,
Lakebase to UC for writes - is the architectural claim this project makes.

Read from the recall_recaps_current VIEW, never a physical table. The view is
the seam between this code and however recaps happen to arrive:

    now      view -> bronze_recall_recaps, filled by src/sync/read_recaps.py
    phase 2  view -> lb_recall_recaps_history, filtered to inserts

WHAT GRADING IS FOR
Not a score for its own sake. The gaps become the callback in the next
briefing's cold open, so they have to be specific enough to say out loud:
"you missed the margin guidance again" is useful, "comprehension 72%" is not.
Structure the output so each gap is individually addressable - free text would
not survive the trip back into a prompt.
"""
import json
import sys

JUDGE_PROMPT = """A sales rep listened to a briefing about {account} and then said back
what they remembered, from memory. Grade the recall.

THE BRIEFING THEY HEARD
{script}

WHAT THEY SAID BACK
{recap}

Judge only what the briefing actually covered. If they mention something true that the
briefing never said, that is neither credit nor a mistake - ignore it.

Weight by importance, not word count. Missing the margin guidance matters more than
missing a partnership announcement, because the guidance is what changes a customer
conversation.

A gap must be specific enough to say out loud at the start of tomorrow's episode. "Missed
the memory pricing pressure on next quarter's margins" is usable. "Needs more detail on
financials" is not.

Reply with JSON only, no other text, in exactly this shape:
{{"accuracy": <0-100 integer>,
  "covered": ["short phrase", ...],
  "gaps": [{{"point": "specific thing they missed", "importance": "high|medium|low"}}],
  "wrong": ["anything they said that the briefing contradicts"],
  "one_line": "one sentence a person would actually say to them"}}"""


def extract_text(content) -> str:
    """gpt-oss returns content as reasoning parts plus text, not a plain string."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        t = getattr(part, "text", None)
        if t is None and isinstance(part, dict):
            t = part.get("text") or part.get("content")
        if t:
            parts.append(t)
    return "\n".join(parts)


def main() -> None:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
    from pyspark.sql import SparkSession

    catalog, schema, endpoint = sys.argv[1], sys.argv[2], sys.argv[3]
    spark = SparkSession.builder.getOrCreate()
    w = WorkspaceClient()

    # Latest recap per rep per account that has not been graded yet.
    todo = spark.sql(f"""
        SELECT r.recap_id, r.account_id, r.rep_id, r.transcript, r.created_at
        FROM {catalog}.{schema}.recall_recaps_current r
        LEFT JOIN (
            SELECT recap_id FROM {catalog}.{schema}.gold_recall_grades
        ) g ON g.recap_id = r.recap_id
        WHERE g.recap_id IS NULL
    """).collect() if spark.catalog.tableExists(
        f"{catalog}.{schema}.gold_recall_grades") else spark.sql(f"""
        SELECT recap_id, account_id, rep_id, transcript, created_at
        FROM {catalog}.{schema}.recall_recaps_current
    """).collect()

    if not todo:
        print("no ungraded recaps")
        return
    print(f"{len(todo)} recap(s) to grade")

    rows = []
    for r in todo:
        # The episode the rep ACTUALLY HEARD: the most recent one that existed
        # when they recorded. This used to take the newest briefing outright,
        # which graded people against a script that did not exist yet - recap 1
        # was scored against an episode generated about twenty hours after the
        # rep spoke. That does not just produce a wrong score: the gaps it
        # invents become the callback in the next episode.
        brief = spark.sql(f"""
            SELECT briefing_id, script_text FROM {catalog}.{schema}.gold_briefing
            WHERE account_id = '{r['account_id']}'
              AND generated_at <= TIMESTAMP '{r['created_at']}'
            ORDER BY generated_at DESC LIMIT 1
        """).collect()
        if not brief:
            # Grading against an episode they demonstrably did not hear is
            # worse than not grading: the score is meaningless and the gap
            # feeds the next cold open. Leave it ungraded and say why.
            print(f"  recap {r['recap_id']}: no {r['account_id']} episode existed "
                  f"at {r['created_at']}, skipping rather than grading against "
                  f"a script the rep never heard")
            continue

        resp = w.serving_endpoints.query(
            name=endpoint,
            messages=[ChatMessage(role=ChatMessageRole.USER,
                                  content=JUDGE_PROMPT.format(
                                      account=r["account_id"],
                                      script=brief[0]["script_text"],
                                      recap=r["transcript"]))],
            # Reasoning model: the budget has to cover thinking plus the JSON.
            max_tokens=2000, temperature=0.2,
        )
        text = extract_text(resp.choices[0].message.content).strip()
        # Models wrap JSON in fences even when told not to.
        if "```" in text:
            text = text.split("```")[1].removeprefix("json").strip()
        try:
            verdict = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except Exception as e:
            print(f"  recap {r['recap_id']}: unparseable verdict ({e}); skipping")
            continue

        gaps = verdict.get("gaps") or []
        rows.append((
            r["recap_id"], r["account_id"], r["rep_id"], brief[0]["briefing_id"],
            int(verdict.get("accuracy") or 0),
            json.dumps(verdict.get("covered") or []),
            json.dumps(gaps),
            json.dumps(verdict.get("wrong") or []),
            str(verdict.get("one_line") or ""),
        ))
        top = gaps[0]["point"] if gaps else "none"
        print(f"  recap {r['recap_id']}: {verdict.get('accuracy')}% | top gap: {top[:70]}")

    if not rows:
        print("nothing graded")
        return

    spark.createDataFrame(rows,
        "recap_id bigint, account_id string, rep_id string, briefing_id string, "
        "accuracy int, covered string, gaps string, wrong string, one_line string"
    ).withColumn("graded_at", __import__("pyspark").sql.functions.current_timestamp()) \
     .write.mode("append").saveAsTable(f"{catalog}.{schema}.gold_recall_grades")
    print(f"wrote {len(rows)} grade(s) to {catalog}.{schema}.gold_recall_grades")


if __name__ == "__main__":
    main()
