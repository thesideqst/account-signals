"""Deltas -> silver_metric_context. The "so what", computed rather than inferred.

A briefing that lists five metrics and their growth rates is a table read
aloud. What makes it worth listening to is the relationship between the
numbers: margins compressed, costs outran revenue, growth decelerated.

Those relationships are arithmetic, so they belong here rather than in the
prompt. Same discipline as the deltas themselves: if the model has to work out
that margins fell, it can get it wrong. If it is handed "gross margin fell 210
basis points", it only has to explain why - which is what the transcript is for.

This table is what lets the script follow Metric -> Context -> Signal:
  metric   the stat, from silver_financial_deltas
  context  why it moved, from here plus what management said
  signal   what it implies, which is the model's actual job
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window


@dlt.table(comment="Ratios, margins and relationships between metrics per quarter.")
def silver_metric_context():
    d = dlt.read("silver_financial_deltas")

    # One row per quarter with each metric as a column, so ratios are simple.
    wide = (
        d.groupBy("symbol", "period_end", "fiscal_year")
        .pivot("metric", ["revenue", "cost_of_revenue", "gross_profit",
                          "operating_income", "net_income"])
        .agg(F.first("value"))
    )
    growth = (
        d.groupBy("symbol", "period_end")
        .pivot("metric", ["revenue", "cost_of_revenue", "gross_profit",
                          "operating_income", "net_income"])
        .agg(F.first("yoy_pct"))
    )
    for m in ("revenue", "cost_of_revenue", "gross_profit",
              "operating_income", "net_income"):
        growth = growth.withColumnRenamed(m, f"{m}_yoy")

    q = Window.partitionBy("symbol").orderBy("period_end")
    joined = wide.join(growth, ["symbol", "period_end"])

    return (
        joined
        # Margins, as percentages of revenue.
        .withColumn("gross_margin_pct", F.col("gross_profit") / F.col("revenue") * 100)
        .withColumn("operating_margin_pct",
                    F.col("operating_income") / F.col("revenue") * 100)
        .withColumn("net_margin_pct", F.col("net_income") / F.col("revenue") * 100)
        # Movement in basis points, which is how margin change is actually
        # discussed. One basis point is a hundredth of a percentage point.
        .withColumn("gross_margin_bps_qoq",
                    (F.col("gross_margin_pct")
                     - F.lag("gross_margin_pct").over(q)) * 100)
        .withColumn("gross_margin_bps_yoy",
                    (F.col("gross_margin_pct")
                     - F.lag("gross_margin_pct", 4).over(q)) * 100)
        .withColumn("operating_margin_bps_qoq",
                    (F.col("operating_margin_pct")
                     - F.lag("operating_margin_pct").over(q)) * 100)
        # Did costs grow faster than sales? Both year-over-year, same basis.
        # Positive means costs outran revenue, which compresses margin.
        .withColumn("cost_vs_revenue_growth_gap_pp",
                    (F.col("cost_of_revenue_yoy") - F.col("revenue_yoy")) * 100)
        # Is growth speeding up or slowing? Change in the year-over-year rate
        # from one quarter to the next, in percentage points.
        .withColumn("revenue_growth_accel_pp",
                    (F.col("revenue_yoy") - F.lag("revenue_yoy").over(q)) * 100)
        # Profit growing slower than operating income points to something below
        # the operating line - tax, interest, or one-offs.
        .withColumn("net_vs_operating_growth_gap_pp",
                    (F.col("net_income_yoy") - F.col("operating_income_yoy")) * 100)
        .select(
            "symbol", "period_end", "fiscal_year",
            "gross_margin_pct", "operating_margin_pct", "net_margin_pct",
            "gross_margin_bps_qoq", "gross_margin_bps_yoy",
            "operating_margin_bps_qoq",
            "cost_vs_revenue_growth_gap_pp",
            "revenue_growth_accel_pp",
            "net_vs_operating_growth_gap_pp",
        )
    )
