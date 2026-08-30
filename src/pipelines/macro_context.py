"""Bronze macro -> silver_macro_context.

A briefing does not want 30,000 observations. It wants where a rate sits now
and which way it has moved, so the script can say "the ten-year is at 4.67,
about forty basis points higher than three months ago" and connect that to how
an account budgets.

Same discipline as the financial metrics: the movement is computed here, with
its direction, so the model explains rather than derives.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window


@dlt.table(comment="Latest value and recent movement for each macro series.")
# Expectations are evaluated against the RETURNED dataframe, so they can only
# name columns that survive to the output. This referenced "value", which is
# renamed to latest_value before the return - the second time that has bitten.
@dlt.expect_or_drop("has_value", "latest_value IS NOT NULL")
def silver_macro_context():
    src = (
        dlt.read("bronze_macro")
        .withColumn("obs_date", F.to_date("obs_date"))
        .filter(F.col("obs_date") >= F.add_months(F.current_date(), -18))
    )

    # Series report at different frequencies - daily for Treasury yields,
    # monthly for CPI - so "the value 90 days ago" has to be found by date
    # rather than by counting rows back.
    latest = (
        src.withColumn("_r", F.row_number().over(
            Window.partitionBy("series_id").orderBy(F.col("obs_date").desc())))
        .filter("_r = 1")
        .select("series_id", "series_name",
                F.col("obs_date").alias("latest_date"),
                F.col("value").alias("latest_value"))
    )

    def value_near(days: int, alias: str):
        w = Window.partitionBy("series_id").orderBy(F.col("obs_date").desc())
        return (
            src.join(latest.select("series_id", "latest_date"), "series_id")
            .filter(F.col("obs_date") <= F.date_sub(F.col("latest_date"), days))
            .withColumn("_r", F.row_number().over(w))
            .filter("_r = 1")
            .select("series_id", F.col("value").alias(alias),
                    F.col("obs_date").alias(f"{alias}_date"))
        )

    return (
        latest
        .join(value_near(30, "value_30d"), "series_id", "left")
        .join(value_near(90, "value_90d"), "series_id", "left")
        .withColumn("change_30d", F.col("latest_value") - F.col("value_30d"))
        .withColumn("change_90d", F.col("latest_value") - F.col("value_90d"))
        .withColumn(
            "direction_90d",
            F.when(F.col("change_90d") > 0, "RISING")
             .when(F.col("change_90d") < 0, "FALLING")
             .otherwise("FLAT"),
        )
        .select("series_id", "series_name", "latest_date", "latest_value",
                "value_30d", "change_30d", "value_90d", "change_90d",
                "direction_90d")
    )
