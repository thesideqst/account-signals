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

PERIOD COMPARISONS JOIN ON DATES, NEVER ON POSITION. This file previously used
`lag()` over a window ordered by period_end, which is the same mistake SCOPE.md
records finding and fixing in the deltas on 2026-08-30 - and it was reintroduced
here, one layer down, on the numbers that become the spoken direction words.
Measured before the fix: the `lag(4)` target was not a year back in 121 of 220
rows, and NVDA 2025-10-26 reported gross margin EXPANDED by 38.5 basis points
when it had COMPRESSED by 114.5. A wrong number is bad; a confidently inverted
direction is worse, because the script builds a paragraph of reasoning on it.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Same tolerances as silver_financial_deltas: 13-week quarters and 52/53-week
# fiscal years mean an exact 91 or 365 day gap almost never occurs.
QOQ_DAYS, QOQ_TOL = 91, 20
YOY_DAYS, YOY_TOL = 365, 25

METRICS = ["revenue", "cost_of_revenue", "gross_profit",
           "operating_income", "net_income"]


def _prior(base, offset_days: int, tol: int, suffix: str):
    """Attach each quarter's values from the period ~offset_days earlier.

    A self-join on the date gap, matching how `nearest()` works in
    xbrl_metrics.py. Where no partner exists inside the tolerance the columns
    are NULL and the caller emits nothing - a missing line is correct, an
    invented comparison is not.
    """
    cur, prv = base.alias("c"), base.alias("p")
    gap = F.datediff(F.col("c.period_end"), F.col("p.period_end"))
    joined = cur.join(
        prv,
        (F.col("c.symbol") == F.col("p.symbol"))
        & (F.abs(gap - F.lit(offset_days)) <= F.lit(tol)),
        "left",
    )
    # Several candidates can fall inside the tolerance; take the closest, and
    # break any remaining tie on the date so a full refresh cannot silently
    # change a published number.
    closest = Window.partitionBy("c.symbol", "c.period_end").orderBy(
        F.abs(gap - F.lit(offset_days)), F.col("p.period_end").desc())
    return (
        joined.withColumn("_r", F.row_number().over(closest))
        .filter("_r = 1")
        .select(
            F.col("c.symbol").alias("symbol"),
            F.col("c.period_end").alias("period_end"),
            F.col("p.gross_margin_pct").alias(f"gross_margin_pct_{suffix}"),
            F.col("p.operating_margin_pct").alias(f"operating_margin_pct_{suffix}"),
            F.col("p.revenue_yoy").alias(f"revenue_yoy_{suffix}"),
        )
    )


@dlt.table(comment="Ratios, margins and relationships between metrics per quarter.")
def silver_metric_context():
    d = dlt.read("silver_financial_deltas")

    # One row per quarter with each metric as a column, so ratios are simple.
    #
    # Grouped on (symbol, period_end) ONLY. Including fiscal_year here fanned
    # one quarter out across several rows, because fiscal_year in the deltas is
    # the year of the filing a fact survived dedupe in, not of the period - so
    # it differs between metrics for the same quarter. That produced 220 rows
    # for 186 real periods, blanked gross margin on 5 periods entirely (revenue
    # and gross_profit landed in different groups, so the division saw NULL),
    # and left synthesize.py's unordered `ctx[0]` picking arbitrarily between
    # a populated row and an empty one.
    wide = (
        d.groupBy("symbol", "period_end")
        .pivot("metric", METRICS)
        .agg(F.first("value"))
    )
    growth = (
        d.groupBy("symbol", "period_end")
        .pivot("metric", METRICS)
        .agg(F.first("yoy_pct"))
    )
    for m in METRICS:
        growth = growth.withColumnRenamed(m, f"{m}_yoy")

    # Kept for provenance, but derived per period rather than grouped on, for
    # the reason above. max() picks the latest filing's view of the year.
    fy = d.groupBy("symbol", "period_end").agg(
        F.max("fiscal_year").alias("fiscal_year"))

    base = (
        wide.join(growth, ["symbol", "period_end"])
        .join(fy, ["symbol", "period_end"])
        # Margins, as percentages of revenue. Computed before the period joins
        # so the prior quarter's margin is looked up rather than recomputed.
        .withColumn("gross_margin_pct", F.col("gross_profit") / F.col("revenue") * 100)
        .withColumn("operating_margin_pct",
                    F.col("operating_income") / F.col("revenue") * 100)
        .withColumn("net_margin_pct", F.col("net_income") / F.col("revenue") * 100)
    )

    prev_q = _prior(base, QOQ_DAYS, QOQ_TOL, "prev_q")
    prev_y = _prior(base, YOY_DAYS, YOY_TOL, "prev_y")

    return (
        base.join(prev_q, ["symbol", "period_end"], "left")
        .join(prev_y, ["symbol", "period_end"], "left")
        # Movement in basis points, which is how margin change is actually
        # discussed. One basis point is a hundredth of a percentage point.
        .withColumn("gross_margin_bps_qoq",
                    (F.col("gross_margin_pct")
                     - F.col("gross_margin_pct_prev_q")) * 100)
        .withColumn("gross_margin_bps_yoy",
                    (F.col("gross_margin_pct")
                     - F.col("gross_margin_pct_prev_y")) * 100)
        .withColumn("operating_margin_bps_qoq",
                    (F.col("operating_margin_pct")
                     - F.col("operating_margin_pct_prev_q")) * 100)
        # Did costs grow faster than sales? Both year-over-year, same basis.
        # Positive means costs outran revenue, which compresses margin.
        .withColumn("cost_vs_revenue_growth_gap_pp",
                    (F.col("cost_of_revenue_yoy") - F.col("revenue_yoy")) * 100)
        # Is growth speeding up or slowing? Change in the year-over-year rate
        # from one quarter to the next, in percentage points.
        .withColumn("revenue_growth_accel_pp",
                    (F.col("revenue_yoy") - F.col("revenue_yoy_prev_q")) * 100)
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
