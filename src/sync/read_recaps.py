"""Lakebase Postgres -> Unity Catalog. The recap write-back.

Lakebase CDF (formerly Lakehouse Sync) is the intended mechanism: native CDC,
no compute, an SCD Type 2 history table. It is blocked on Free Edition, whose
only catalog uses Databricks Default Storage:

    Lakebase CDF is not supported for catalogs using Default Storage.

HOW THIS WORKS INSTEAD
The Lakebase project is registered in Unity Catalog as `account_signals_pg`,
which makes its Postgres tables queryable through Lakehouse Federation as
ordinary SQL. So the recaps come back with a plain INSERT ... SELECT. No
Postgres driver is involved.

That detail is not a nicety. An earlier version of this file used psycopg, and
a psycopg connection inside a serverless task kills the Python kernel outright
- verified in isolation, and identical with psycopg2, so it is the platform and
not the driver. Federation goes over the same path as any other UC query and
sidesteps the problem completely.

WHAT THIS DOES NOT DO
No update or delete capture, and no SCD Type 2 history. Recaps are insert-only,
so nothing is lost today, but this is not general-purpose CDC. Latency is the
schedule interval rather than seconds.

THE SEAM
Output lands in bronze_recall_recaps. Grading never reads it directly - it
reads the `recall_recaps_current` view. In phase 2, CDF writes
lb_recall_recaps_history and the view is repointed at that table, filtered to
inserts. Grading code does not change. That indirection is the whole point.
"""
import sys

LAKEBASE_CATALOG = "account_signals_pg"
SOURCE = f"{LAKEBASE_CATALOG}.app.recall_recaps"


def main() -> None:
    from pyspark.sql import SparkSession

    catalog, schema = sys.argv[1], sys.argv[2]
    spark = SparkSession.builder.getOrCreate()
    target = f"{catalog}.{schema}.bronze_recall_recaps"

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {target} (
            recap_id      BIGINT,
            account_id    STRING,
            rep_id        STRING,
            briefing_date DATE,
            transcript    STRING,
            audio_seconds INT,
            created_at    TIMESTAMP,
            _synced_at    TIMESTAMP
        )
    """)

    # Incremental by recap_id, which Postgres assigns monotonically. A re-run
    # costs nothing and can never double-count.
    hwm = spark.sql(
        f"SELECT coalesce(max(recap_id), 0) AS hwm FROM {target}"
    ).collect()[0]["hwm"]

    inserted = spark.sql(f"""
        INSERT INTO {target}
        SELECT recap_id, account_id, rep_id, briefing_date, transcript,
               audio_seconds, created_at, current_timestamp() AS _synced_at
        FROM {SOURCE}
        WHERE recap_id > {hwm}
    """)
    total = spark.sql(f"SELECT count(*) AS n FROM {target}").collect()[0]["n"]
    print(f"watermark was {hwm}; {target} now holds {total} recaps")

    # Grading binds to this view, never to a physical table, so phase 2 is a
    # view swap rather than a rewrite.
    spark.sql(f"""
        CREATE OR REPLACE VIEW {catalog}.{schema}.recall_recaps_current AS
        SELECT recap_id, account_id, rep_id, briefing_date, transcript,
               audio_seconds, created_at
        FROM {target}
    """)
    print(f"refreshed view {catalog}.{schema}.recall_recaps_current")

    # The comprehension answers come back the same way. This is the table the
    # app actually writes to now - it was added after recall_recaps and was not
    # wired into the write-back, so answers were sitting in Postgres and never
    # reaching Unity Catalog.
    answers_target = f"{catalog}.{schema}.bronze_recap_answers"
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {answers_target} (
            answer_id      BIGINT,
            briefing_id    STRING,
            account_id     STRING,
            rep_id         STRING,
            question_index INT,
            question       STRING,
            answer         STRING,
            score          INT,
            verdict        STRING,
            missed         STRING,
            answered_at    TIMESTAMP,
            _synced_at     TIMESTAMP
        )
    """)
    a_hwm = spark.sql(
        f"SELECT coalesce(max(answer_id), 0) AS hwm FROM {answers_target}"
    ).collect()[0]["hwm"]
    spark.sql(f"""
        INSERT INTO {answers_target}
        SELECT answer_id, briefing_id, account_id, rep_id, question_index,
               question, answer, score, verdict, missed, answered_at,
               current_timestamp() AS _synced_at
        FROM {LAKEBASE_CATALOG}.app.recap_answers
        WHERE answer_id > {a_hwm}
    """)
    a_total = spark.sql(f"SELECT count(*) AS n FROM {answers_target}").collect()[0]["n"]
    print(f"answers watermark was {a_hwm}; {answers_target} now holds {a_total}")

    spark.sql(f"""
        CREATE OR REPLACE VIEW {catalog}.{schema}.recap_answers_current AS
        SELECT answer_id, briefing_id, account_id, rep_id, question_index,
               question, answer, score, verdict, missed, answered_at, _synced_at
        FROM {answers_target}
    """)

    # Usage is recorded in Unity Catalog, not in Postgres. Federation reads
    # Postgres but cannot write to it, and psycopg inside a serverless task
    # kills the Python kernel (see the module docstring), so the briefing job
    # has no way to mark a topic used at the source. It writes here instead,
    # which is the direction that works, and the view below subtracts it.
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {catalog}.{schema}.gold_topic_usage (
            request_id BIGINT,
            account_id STRING,
            topic STRING,
            briefing_id STRING,
            used_at TIMESTAMP
        )
    """)

    # The topic queue comes back the same way. On a quiet day it is what the
    # episode is about, so Mode C stops having to invent a subject.
    #
    # "Outstanding" is more than status = 'queued', for two reasons:
    #
    # 1. Nothing marked a topic used, so the scheduled path read
    #    ORDER BY requested_at ASC LIMIT 1 and got request_id 1 every single
    #    time - forever, no matter how many episodes covered it. Subtracting
    #    gold_topic_usage is what makes the queue advance.
    # 2. `generating` was a TERMINAL state. The app sets it when a rep triggers
    #    a run, and nothing ever moves it on, so a failed or cancelled run
    #    stranded that topic outside the queue permanently. A run takes two to
    #    four minutes, so anything still `generating` after thirty is a run
    #    that died, and the topic returns to the queue rather than vanishing.
    spark.sql(f"""
        CREATE OR REPLACE VIEW {catalog}.{schema}.topic_queue_current AS
        SELECT t.request_id, t.account_id, t.rep_id, t.topic, t.origin,
               t.status, t.requested_at, t.used_at
        FROM {LAKEBASE_CATALOG}.app.topic_requests t
        LEFT ANTI JOIN {catalog}.{schema}.gold_topic_usage u
          ON u.request_id = t.request_id
        WHERE t.status = 'queued'
           OR (t.status = 'generating'
               AND t.used_at < current_timestamp() - INTERVAL 30 MINUTES)
    """)
    n = spark.sql(
        f"SELECT count(*) AS n FROM {catalog}.{schema}.topic_queue_current"
    ).collect()[0]["n"]
    print(f"topic queue: {n} request(s) waiting")


if __name__ == "__main__":
    main()
