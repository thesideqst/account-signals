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
from fastapi import FastAPI, HTTPException
from fastapi import Response
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row

app = FastAPI(title="account_signals")

CATALOG = os.environ.get("CATALOG", "workspace")
SCHEMA = os.environ.get("SCHEMA", "account_signals_dev")
LAKEBASE_ENDPOINT = (
    "projects/account-signals-dev/branches/production/endpoints/primary"
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
                   mode_reason, episode_title, mode_label,
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


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html")) as f:
        return f.read()
