"""account_signals — the briefing app.

Reads from Lakebase Postgres, not from Delta. gold_briefing_serving is synced
from Unity Catalog into Postgres, so a page load is a single-row primary-key
lookup rather than a warehouse query. Delta is built for scanning millions of
rows; this is the opposite shape.

Writes go the other way. A recap posted here lands in Postgres and returns to
Unity Catalog for grading, which is the round trip the whole project is
demonstrating.

Connection: Databricks injects PGHOST/PGUSER/PGPASSWORD when a postgres
resource is attached to the app. Running outside that (locally, or before the
resource is attached) it falls back to generating an OAuth token through the
SDK, which is the same path the pipeline jobs use.
"""
import os

import psycopg
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Response
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row

app = FastAPI(title="account_signals")

CATALOG = os.environ.get("CATALOG", "workspace")
SCHEMA = os.environ.get("SCHEMA", "account_signals_dev")
# Differs per workspace; set in app.yaml rather than hardcoded here.
LAKEBASE_ENDPOINT = os.environ.get(
    "LAKEBASE_ENDPOINT",
    "projects/account-signals-dev/branches/production/endpoints/primary",
)
_w = None


def workspace() -> WorkspaceClient:
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w


def pg():
    """Connect to Lakebase.

    Prefers the env vars Databricks injects for an attached postgres resource.
    Falls back to a short-lived OAuth token, which is what the jobs use and is
    what makes this runnable before the resource is wired up.
    """
    if os.environ.get("PGHOST"):
        return psycopg.connect(
            host=os.environ["PGHOST"],
            dbname=os.environ.get("PGDATABASE", "databricks_postgres"),
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            port=os.environ.get("PGPORT", "5432"),
            sslmode="require",
            row_factory=dict_row,
            autocommit=True,
        )

    w = workspace()
    ep = w.postgres.get_endpoint(name=LAKEBASE_ENDPOINT)
    # Postgres identifies a service principal by its client id, which is the
    # role name created with `databricks postgres create-role`. current_user
    # returns a display name for an SP, which is not a Postgres role and fails
    # to authenticate. Databricks injects DATABRICKS_CLIENT_ID into every app.
    user = os.environ.get("DATABRICKS_CLIENT_ID") or w.current_user.me().user_name
    return psycopg.connect(
        host=ep.status.hosts.host,
        dbname="databricks_postgres",
        user=user,
        password=w.postgres.generate_database_credential(
            endpoint=LAKEBASE_ENDPOINT).token,
        sslmode="require",
        row_factory=dict_row,
        autocommit=True,
    )


@app.get("/api/health")
def health():
    """Full diagnostic. App logs need OAuth and this workspace uses a PAT, so
    this endpoint is the only way to see what the app can actually reach."""
    out = {
        "pg_env_injected": bool(os.environ.get("PGHOST")),
        "pghost": os.environ.get("PGHOST", "(not injected)"),
        "pguser": os.environ.get("PGUSER", "(not injected)"),
        "catalog": CATALOG, "schema": SCHEMA,
    }
    try:
        w = workspace()
        me = w.current_user.me()
        out["identity"] = me.user_name
        out["pg_user_used"] = (os.environ.get("DATABRICKS_CLIENT_ID")
                               or me.user_name)
        out["identity_id"] = me.id
    except Exception as e:
        out["identity_error"] = f"{type(e).__name__}: {e}"

    try:
        with pg() as c:
            out["briefings"] = c.execute(
                "SELECT count(*) AS n FROM app.gold_briefing_serving").fetchone()["n"]
        out["postgres"] = "ok"
    except Exception as e:
        out["postgres"] = f"{type(e).__name__}: {str(e)[:300]}"
    return out


@app.get("/api/accounts")
def accounts():
    try:
        return _accounts()
    except Exception as e:
        # Surface the real cause instead of a bare 500 the browser cannot read.
        raise HTTPException(500, f"{type(e).__name__}: {str(e)[:300]}")


def _accounts():
    with pg() as c:
        return c.execute("""
            SELECT account_id, max(period_end) AS latest_period, count(*) AS episodes
            FROM app.gold_briefing_serving GROUP BY account_id ORDER BY account_id
        """).fetchall()


@app.get("/api/briefing/{account_id}")
def briefing(account_id: str):
    with pg() as c:
        row = c.execute("""
            SELECT briefing_id, account_id, period_end, generated_at, mode,
                   mode_reason, episode_title, mode_label, takeaways,
                   questions, lineage,
                   script_text, word_count, audio_path, voice
            FROM app.gold_briefing_serving
            WHERE account_id = %s ORDER BY generated_at DESC LIMIT 1
        """, (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"no briefing for {account_id}")
    return row


@app.get("/api/audio-check/{account_id}")
def audio_check(account_id: str):
    """Why audio is or is not playable. Same reason /api/health exists: the
    app has no readable logs, so it has to be able to explain itself."""
    out = {}
    try:
        with pg() as c:
            row = c.execute("""
                SELECT audio_path, audio_bytes FROM app.gold_briefing_serving
                WHERE account_id = %s ORDER BY generated_at DESC LIMIT 1
            """, (account_id,)).fetchone()
        out["row"] = dict(row) if row else None
    except Exception as e:
        return {"stage": "postgres", "error": f"{type(e).__name__}: {e}"}

    if not out["row"] or not out["row"].get("audio_path"):
        out["stage"] = "no audio_path on the row"
        return out

    path = out["row"]["audio_path"]
    try:
        meta = workspace().files.get_metadata(path)
        out["volume_content_length"] = getattr(meta, "content_length", None)
    except Exception as e:
        out["metadata_error"] = f"{type(e).__name__}: {str(e)[:250]}"
    try:
        resp = workspace().files.download(path)
        first = resp.contents.read(64)
        out["download_ok"] = True
        out["first_bytes"] = first[:16].hex()
    except Exception as e:
        out["download_error"] = f"{type(e).__name__}: {str(e)[:250]}"
    return out


@app.get("/api/audio/{account_id}")
def audio(account_id: str):
    """Stream the MP3 out of the Unity Catalog Volume.

    The path lives in Postgres; the bytes live in the Volume. The app holds
    neither - it reads the path from one and streams the file from the other.
    """
    with pg() as c:
        row = c.execute("""
            SELECT audio_path FROM app.gold_briefing_serving
            WHERE account_id = %s AND audio_path IS NOT NULL
            ORDER BY generated_at DESC LIMIT 1
        """, (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"no audio for {account_id}")

    try:
        resp = workspace().files.download(row["audio_path"])
    except Exception as e:
        raise HTTPException(500, f"volume read failed: {type(e).__name__}: {str(e)[:200]}")

    # Read fully rather than streaming the SDK's file object. The browser needs
    # Content-Length and range support to show a duration and let you scrub;
    # a bare stream plays as 0:00/0:00.
    data = resp.contents.read()
    return Response(
        content=data,
        media_type="audio/mpeg",
        headers={"Content-Length": str(len(data)),
                 "Accept-Ranges": "bytes",
                 "Cache-Control": "no-cache"},
    )


def stt_key() -> str:
    """OpenAI key from the Databricks secret scope. The app service principal
    needs READ on the scope; without it this raises and the endpoint says so."""
    return workspace().secrets.get_secret(
        scope="account_signals", key="stt_api_key").value


def _transcribe(raw: bytes) -> str:
    """Whisper via OpenAI. Multipart is hand-built to avoid another dependency."""
    import json as _json
    import urllib.request

    if not raw:
        raise HTTPException(400, "empty recording")
    boundary = "----accountsignals"
    def part(name, value, filename=None, ctype=None):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            head += f'; filename="{filename}"'
        head += "\r\n"
        if ctype:
            head += f"Content-Type: {ctype}\r\n"
        return head.encode() + b"\r\n" + (value if isinstance(value, bytes)
                                            else value.encode()) + b"\r\n"
    body = (part("model", "whisper-1")
            + part("file", raw, filename="a.webm", ctype="audio/webm")
            + f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {stt_key()}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = _json.loads(r.read()).get("text", "").strip()
    except Exception as e:
        detail = getattr(e, "read", lambda: b"")()[:200].decode("utf-8", "replace")
        raise HTTPException(502, f"transcription failed: {type(e).__name__} {detail}")
    if not text:
        raise HTTPException(422, "nothing was transcribed - try speaking longer")
    return text


@app.post("/api/recap/{account_id}")
async def submit_recap(account_id: str, audio: UploadFile = File(...),
                       rep_id: str = Form("web-user")):
    """Take a spoken recap, transcribe it, write it to Lakebase.

    This INSERT is what starts the write-back: the recap returns to Unity
    Catalog through Lakehouse Federation, gets graded against the briefing it
    was recalling, and the gaps become the callback in the next episode.
    """
    import base64
    import json as _json
    import urllib.request

    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "empty recording")

    # OpenAI's transcription endpoint takes multipart, hand-built here to avoid
    # another dependency in the app runtime.
    boundary = "----accountsignals"
    def part(name, value, filename=None, ctype=None):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            head += f'; filename="{filename}"'
        head += "\r\n"
        if ctype:
            head += f"Content-Type: {ctype}\r\n"
        return head.encode() + b"\r\n" + (value if isinstance(value, bytes)
                                            else value.encode()) + b"\r\n"

    body = (part("model", "whisper-1")
            + part("file", raw, filename="recap.webm", ctype="audio/webm")
            + f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {stt_key()}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            transcript = _json.loads(r.read()).get("text", "").strip()
    except Exception as e:
        detail = getattr(e, "read", lambda: b"")()[:200].decode("utf-8", "replace")
        raise HTTPException(502, f"transcription failed: {type(e).__name__} {detail}")

    if not transcript:
        raise HTTPException(422, "nothing was transcribed - try speaking longer")

    seconds = int(len(raw) / 16000)   # rough, webm/opus is variable bitrate
    try:
        with pg() as c:
            row = c.execute("""
                INSERT INTO app.recall_recaps
                    (account_id, rep_id, briefing_date, transcript, audio_seconds)
                VALUES (%s, %s, current_date, %s, %s)
                RETURNING recap_id
            """, (account_id, rep_id, transcript, seconds)).fetchone()
    except Exception as e:
        raise HTTPException(500, f"could not save recap: {type(e).__name__}: {str(e)[:200]}")

    return {"recap_id": row["recap_id"], "transcript": transcript,
            "seconds": seconds}


@app.get("/api/grade/{account_id}")
def grade(account_id: str):
    """Most recent grade for this account, if grading has run since."""
    try:
        with pg() as c:
            row = c.execute("""
                SELECT recap_id, accuracy, one_line, gaps
                FROM app.gold_recall_grades
                WHERE account_id = %s ORDER BY graded_at DESC LIMIT 1
            """, (account_id,)).fetchone()
        return row or {}
    except Exception:
        # Grades only reach Postgres once that table is synced; absence is normal.
        return {}


GRADE_PROMPT = """A sales rep listened to a briefing about {account} and was asked:

    {question}

A good answer contains: {expected}

They said:

    {answer}

Grade what they actually understood, not their wording. Someone who gets the idea across
in loose language has understood it; someone who repeats a number without the reason has
not.

Reply with JSON only:
{{"score": <0-100>, "verdict": "one sentence said to them directly",
  "missed": "the single most important thing they left out, or empty string"}}"""


@app.post("/api/answer/{account_id}")
async def answer(account_id: str, audio: UploadFile = File(...),
                 briefing_id: str = Form(...), question_index: int = Form(...),
                 question: str = Form(...), expected: str = Form(""),
                 rep_id: str = Form("web-user")):
    """Transcribe one spoken answer, grade it immediately, store it.

    Graded here rather than in the nightly job because feedback that arrives
    tomorrow does not teach anyone anything. Three targeted answers are small
    enough to judge in a couple of seconds, which a free-form recap was not.
    """
    import json as _json

    transcript = _transcribe(await audio.read())

    resp = workspace().serving_endpoints.query(
        name="databricks-gpt-oss-120b",
        messages=[ChatMessage(role=ChatMessageRole.USER,
                              content=GRADE_PROMPT.format(
                                  account=account_id, question=question,
                                  expected=expected or "(not supplied)",
                                  answer=transcript))],
        max_tokens=1200, temperature=0.2,
    )
    raw = resp.choices[0].message.content
    text = raw if isinstance(raw, str) else "\n".join(
        (getattr(pt, "text", None) or (pt.get("text") if isinstance(pt, dict) else "") or "")
        for pt in (raw or []))
    if "```" in text:
        text = text.split("```")[1].removeprefix("json").strip()
    try:
        v = _json.loads(text[text.index("{"):text.rindex("}") + 1])
    except Exception:
        v = {"score": None, "verdict": "Could not grade this answer.", "missed": ""}

    with pg() as c:
        c.execute("""
            INSERT INTO app.recap_answers
                (briefing_id, account_id, rep_id, question_index, question,
                 answer, score, verdict, missed)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (briefing_id, account_id, rep_id, question_index, question,
              transcript, v.get("score"), str(v.get("verdict") or ""),
              str(v.get("missed") or "")))

    return {"transcript": transcript, "score": v.get("score"),
            "verdict": v.get("verdict"), "missed": v.get("missed") or ""}


@app.post("/api/topic/{account_id}")
def add_topic(account_id: str, topic: str = Form(...), origin: str = Form("manual"),
              rep_id: str = Form("web-user")):
    """Queue a subject for a future episode.

    Two ways in: one click from something they just got wrong, or typed. On a
    quiet day this queue is what the episode is about.
    """
    if not topic.strip():
        raise HTTPException(400, "empty topic")
    with pg() as c:
        row = c.execute("""
            INSERT INTO app.topic_requests (account_id, rep_id, topic, origin)
            VALUES (%s,%s,%s,%s) RETURNING request_id
        """, (account_id, rep_id, topic.strip()[:500], origin)).fetchone()
    return {"request_id": row["request_id"], "topic": topic.strip()}


@app.get("/api/topics/{account_id}")
def topics(account_id: str):
    with pg() as c:
        return c.execute("""
            SELECT request_id, topic, origin, requested_at
            FROM app.topic_requests
            WHERE account_id = %s AND status = 'queued'
            ORDER BY requested_at DESC LIMIT 20
        """, (account_id,)).fetchall()


@app.get("/api/episodes/{account_id}")
def episodes(account_id: str):
    """Past episodes, newest first."""
    with pg() as c:
        return c.execute("""
            SELECT briefing_id, episode_title, mode_label, period_end,
                   generated_at, word_count
            FROM app.gold_briefing_serving
            WHERE account_id = %s ORDER BY generated_at DESC LIMIT 30
        """, (account_id,)).fetchall()


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html")) as f:
        return f.read()
