"""Shared helpers for Bronze ingestion.

Bronze rule: land the response as close to raw as possible, append-only.
Never parse aggressively here — if a provider changes their schema, you want
to re-parse from Bronze rather than re-download years of history.
"""
from datetime import datetime, timezone

from pyspark.sql import SparkSession


def spark() -> SparkSession:
    return SparkSession.builder.getOrCreate()


def bronze_write(df, catalog: str, schema: str, table: str) -> None:
    """Append to a Bronze table, stamping ingest time for lineage."""
    from pyspark.sql import functions as F

    (
        df.withColumn("_ingested_at", F.lit(datetime.now(timezone.utc)))
        .write.mode("append")
        .saveAsTable(f"{catalog}.{schema}.bronze_{table}")
    )


def secret(scope: str, key: str) -> str:
    """Fetch an API key from a Databricks secret scope. Never hardcode keys."""
    from databricks.sdk.runtime import dbutils

    return dbutils.secrets.get(scope=scope, key=key)
