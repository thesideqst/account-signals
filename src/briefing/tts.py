"""Briefing script -> MP3 in the UC Volume.

No speech model exists in this workspace, so this calls OpenAI
(SCOPE.md, 2026-08-29). Model gpt-4o-mini-tts.

TWO THINGS SHAPE THIS FILE

OpenAI's speech endpoint caps input at 4096 characters. A ten minute script is
around 7,000, so it has to go over in pieces. Split on PARAGRAPH boundaries,
never mid-sentence: the engine adds a natural pause at the end of a chunk, and
a pause dropped into the middle of a clause sounds like a stutter.

The pieces come back as separate MP3 files and are joined by concatenating the
bytes. MP3 is a stream of independent frames, so players handle this fine. It
is not a real audio edit - there is no crossfade and no re-encode - but it needs
no ffmpeg on the cluster, which matters on serverless.
"""
import json
import sys
import time
import urllib.error
import urllib.request

SPEECH_URL = "https://api.openai.com/v1/audio/speech"
MODEL = "gpt-4o-mini-tts"
VOICE = "ballad"
# Measured against the browser's own reported duration, not a computed one:
# at speed 1.0 this reads at roughly 153 words per minute, which is normal
# narration pace. An earlier estimate of 264 wpm came from misreading the mp3
# bitrate and led to slowing the voice unnecessarily.
DEFAULT_SPEED = 1.0
MAX_INPUT_CHARS = 4000          # endpoint caps at 4096; leave headroom

# gpt-4o-mini-tts takes delivery instructions alongside the text. Without them
# it reads like a machine working through a document, which is most of what
# made earlier versions unlistenable. This steers pace and emphasis; it does
# not change a word of the script.
DELIVERY = (
    "Speak like a smart friend explaining something over coffee. Conversational "
    "and unhurried, with natural pauses between ideas. Land the important "
    "numbers - slow slightly on them rather than rushing past. Warm, not "
    "newsreaderly. Never sing-song, never breathless."
)
# Keyed by briefing_id, not by period. Keying on the period meant every rerun
# overwrote the same file, so "past episodes" pointed at whatever was generated
# most recently and older audio was already gone.
VOLUME_PATH = "/Volumes/{catalog}/{schema}/audio/{account}/{briefing_id}.mp3"


def split_for_speech(script: str, limit: int = MAX_INPUT_CHARS):
    """Paragraphs packed into chunks under the limit, never splitting a sentence."""
    chunks, current = [], ""
    for para in [p.strip() for p in script.split("\n") if p.strip()]:
        if len(current) + len(para) + 2 <= limit:
            current = f"{current}\n\n{para}" if current else para
            continue
        if current:
            chunks.append(current)
        # A single paragraph over the limit is rare but has to be handled;
        # fall back to sentence boundaries inside it.
        if len(para) > limit:
            sentence, current = "", ""
            for part in para.replace(". ", ".\x00").split("\x00"):
                if len(sentence) + len(part) <= limit:
                    sentence += part
                else:
                    chunks.append(sentence.strip())
                    sentence = part
            current = sentence
        else:
            current = para
    if current:
        chunks.append(current)
    return chunks


def speak(text: str, key: str, speed: float = DEFAULT_SPEED, tries: int = 4) -> bytes:
    body = json.dumps({
        "model": MODEL, "voice": VOICE, "input": text,
        "response_format": "mp3", "speed": speed,
        "instructions": DELIVERY,
    }).encode()
    req = urllib.request.Request(
        SPEECH_URL, data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(
                f"TTS failed {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
            ) from e


def main() -> None:
    import os
    from databricks.sdk.runtime import dbutils
    from pyspark.sql import SparkSession

    catalog, schema = sys.argv[1], sys.argv[2]
    speed = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_SPEED
    spark = SparkSession.builder.getOrCreate()
    key = dbutils.secrets.get(scope="account_signals", key="tts_api_key")

    account = sys.argv[5] if len(sys.argv) > 5 else "NVDA"
    rows = spark.sql(f"""
        SELECT briefing_id, account_id, period_end, script_text, word_count
        FROM {catalog}.{schema}.gold_briefing_current
        WHERE account_id = '{account}'
    """).collect()
    if not rows:
        print(f"no briefing to narrate for {account}")
        return
    row = rows[0]

    # Guard the other direction: the app refuses to start a run while one is in
    # flight, but a scheduled run can still overlap a manual one. If the newest
    # briefing is not the one this task's own synthesize step produced, another
    # run overtook us - narrating now would attach this audio to a briefing that
    # is no longer current and publish the newer episode silent.
    expected = os.environ.get("EXPECTED_BRIEFING_ID", "")
    if expected and row["briefing_id"] != expected:
        print(f"another run produced a newer briefing ({row['briefing_id']}); "
              f"this run wrote {expected}. Stopping before narration so the "
              f"newer episode keeps its own audio.")
        return

    chunks = split_for_speech(row["script_text"])
    print(f"{row['account_id']} {row['period_end']}: {row['word_count']} words "
          f"-> {len(chunks)} speech chunks at speed {speed}")

    audio = b""
    for i, chunk in enumerate(chunks, 1):
        audio += speak(chunk, key, speed)
        print(f"  chunk {i}/{len(chunks)}: {len(chunk):,} chars -> {len(audio):,} bytes total")

    path = VOLUME_PATH.format(catalog=catalog, schema=schema,
                              account=row["account_id"],
                              briefing_id=row["briefing_id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(audio)

    # Estimate from words, not from bytes. Two attempts to derive duration from
    # the file failed: a guessed 240 KB/min, then a parsed bitrate of 224 kbps
    # that was a false frame-header match. The browser reports 691 seconds for
    # an 8.8 MB file, roughly 102 kbps, so byte-based maths was reporting half
    # the real length. Words over a measured rate is less precise and honest.
    WORDS_PER_MINUTE = 153.0
    seconds = row["word_count"] / (WORDS_PER_MINUTE * speed) * 60
    wpm = WORDS_PER_MINUTE * speed
    print(f"wrote {path} ({len(audio) / 1_000_000:.2f} MB, "
          f"~{seconds / 60:.1f} min estimated at {wpm:.0f} words/min)")

    spark.sql(f"""
        CREATE OR REPLACE TABLE {catalog}.{schema}.gold_briefing_audio AS
        SELECT '{row['briefing_id']}' AS briefing_id,
               '{row['account_id']}'  AS account_id,
               '{row['period_end']}'  AS period_end,
               '{path}'               AS audio_path,
               {len(audio)}           AS audio_bytes,
               {seconds:.1f}          AS duration_seconds,
               '{MODEL}'              AS tts_model,
               '{VOICE}'              AS voice,
               {speed}                AS speed,
               current_timestamp()    AS generated_at
    """)
    print(f"wrote {catalog}.{schema}.gold_briefing_audio")

    # Rebuild the table the app reads, then trigger the Lakebase sync so the
    # app sees this episode rather than the previous snapshot.
    spark.sql(f"""
        CREATE OR REPLACE TABLE {catalog}.{schema}.gold_briefing_serving AS
        SELECT b.briefing_id, b.account_id, b.period_end, b.generated_at,
               b.mode, b.mode_reason, b.episode_title, b.mode_label, b.takeaways, b.questions, b.lineage,
               b.script_text, b.word_count,
               a.audio_path, a.audio_bytes, a.voice
        -- Every episode, not just the newest. Built from gold_briefing_current
        -- this table held exactly one row by definition, so "past episodes"
        -- could never show anything but today's.
        FROM {catalog}.{schema}.gold_briefing b
        LEFT JOIN {catalog}.{schema}.gold_briefing_audio a
          ON a.briefing_id = b.briefing_id
        -- Episodes hidden from the app stay hidden. The Unity Catalog record
        -- survives; only the served copy drops them.
        WHERE b.briefing_id NOT IN (
            SELECT briefing_id FROM account_signals_pg.app.hidden_episodes
        )
    """)
    print(f"refreshed {catalog}.{schema}.gold_briefing_serving")

    # The Lakebase copy the app reads uses SNAPSHOT scheduling, which does not
    # refresh on its own. Rebuilding the source table leaves the app showing the
    # previous episode until this pipeline runs, so trigger it here rather than
    # relying on someone remembering.
    # The pipeline id is created by `databricks postgres create-synced-table`,
    # so it differs per workspace. Passed in rather than hardcoded.
    sync_pipeline_id = sys.argv[4] if len(sys.argv) > 4 else ""
    try:
        if not sync_pipeline_id:
            raise RuntimeError("no sync pipeline id supplied")
        from databricks.sdk import WorkspaceClient

        WorkspaceClient().pipelines.start_update(pipeline_id=sync_pipeline_id)
        print(f"triggered Lakebase sync pipeline {sync_pipeline_id}")
    except Exception as e:
        # A stale app is bad but not worth failing the whole briefing over.
        print(f"WARNING could not trigger sync: {type(e).__name__}: {e}")

    # NOTE: re-granting the app's Postgres read access is deliberately NOT done
    # here. Opening a psycopg connection inside this serverless task kills the
    # Python kernel outright ("Fatal error: The Python kernel is unresponsive"),
    # with or without a retry loop. Losing the whole briefing to fix a grant is
    # a bad trade, so the grant is a documented manual step until the cause is
    # understood. See ARCHITECTURE.md, Known risks.





def regrant_app_access() -> None:
    """Re-grant the app read access after a sync.

    A schema change makes the sync drop and recreate the Postgres table. The
    new table is a fresh object owned by the sync's writer role, so grants on
    the old one are gone and the app starts returning nothing.

    The clean fix would be ALTER DEFAULT PRIVILEGES FOR ROLE <writer>, so any
    table that role creates grants automatically. That needs membership in the
    writer role, which this identity does not have - Postgres refuses with
    "permission denied to change default privileges".

    So the grant is reapplied instead. ONE attempt, short timeout, no retry
    loop: an earlier version polled for four minutes and hung the serverless
    Python kernel outright, which is a far worse failure than a stale grant.
    The grant is idempotent, and a schema change is rare, so the usual case is
    a no-op that costs a second.
    """
    import psycopg
    from databricks.sdk import WorkspaceClient

    try:
        w = WorkspaceClient()
        host = w.postgres.get_endpoint(name=LAKEBASE_ENDPOINT).status.hosts.host
        user = os.environ.get("DATABRICKS_CLIENT_ID") or w.current_user.me().user_name
        token = w.postgres.generate_database_credential(
            endpoint=LAKEBASE_ENDPOINT).token
        with psycopg.connect(host=host, dbname="databricks_postgres", user=user,
                             password=token, sslmode="require", autocommit=True,
                             connect_timeout=20) as c:
            c.execute(f'GRANT USAGE ON SCHEMA app TO "{APP_SP_CLIENT_ID}"')
            c.execute('GRANT SELECT ON ALL TABLES IN SCHEMA app TO '
                      f'"{APP_SP_CLIENT_ID}"')
            ok = c.execute(
                "SELECT has_table_privilege(%s,'app.gold_briefing_serving','SELECT')",
                (APP_SP_CLIENT_ID,)).fetchone()[0]
        print(f"app read access re-granted: {ok}")
    except Exception as e:
        # Never fail the briefing over this. The audio is already written.
        print(f"WARNING re-grant skipped: {type(e).__name__}: {str(e)[:120]}")



if __name__ == "__main__":
    main()
