"""Bronze XBRL facts -> annual, quarterly, and delta tables.

Three tables:
  silver_annual_metrics     the full-year figure the 10-K reports
  silver_quarterly_metrics  reported quarters plus a derived Q4
  silver_financial_deltas   QoQ and YoY

The tricky parts, all found by looking at real NVDA filings on 2026-08-30:

1. Companies change concept tags. NVDA used
   RevenueFromContractWithCustomerExcludingAssessedTax until 2020, then
   switched to Revenues. So each metric lists several acceptable tags in
   priority order and we take the best one available for each period.

2. The same quarter shows up more than once, because a 10-K repeats last
   year's numbers for comparison. Dedupe on the period dates, keeping the
   newest filing. For NVDA revenue this cut 164 facts down to 63 quarters.

3. Usually there is no Q4 filing. The year ends and the 10-K reports the whole
   year, so the quarterly series normally has a hole at each fiscal year end.
   We fill it with annual minus the three reported quarters and mark it
   is_derived.

   But not always. NVDA's older filings (through about 2011) DO report Q4 as
   its own quarterly fact — 34 of them in this dataset. So we check first and
   only derive when Q4 is genuinely missing.

4. Because of that hole, lag() is wrong. It would compare a quarter to one
   five quarters back and call it year-over-year. We join on actual dates
   instead, so a missing quarter gives NULL rather than a wrong number.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Acceptable tags per metric, best first.
CONCEPTS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "cost_of_revenue":  ["CostOfRevenue"],
    "gross_profit":     ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income":       ["NetIncomeLoss"],
}

QOQ_DAYS, QOQ_TOL = 91, 20     # tolerance covers 13-week quarters
YOY_DAYS, YOY_TOL = 365, 25    # and 52/53-week fiscal years


def _metric_col():
    """concept -> metric name."""
    e = F
    col = F.lit(None).cast("string")
    for metric, concepts in CONCEPTS.items():
        col = F.when(F.col("concept").isin(concepts), F.lit(metric)).otherwise(col)
    return col


def _priority_col():
    """concept -> its rank within its metric. Lower wins."""
    col = F.lit(None).cast("int")
    for concepts in CONCEPTS.values():
        for rank, concept in enumerate(concepts):
            col = F.when(F.col("concept") == concept, F.lit(rank)).otherwise(col)
    return col


def _resolved(period_kind: str):
    """Facts for one period length, one row per (symbol, metric, period)."""
    facts = (
        dlt.read("silver_xbrl_facts")
        .filter((F.col("period_kind") == period_kind) & (F.col("unit") == "USD"))
        .withColumn("metric", _metric_col())
        .withColumn("_priority", _priority_col())
        .filter(F.col("metric").isNotNull())
    )
    # If both an old and a new tag report the same period, keep the preferred one.
    best = Window.partitionBy(
        "symbol", "metric", "period_start", "period_end"
    ).orderBy("_priority")
    one_per_period = (
        facts.withColumn("_r", F.row_number().over(best))
        .filter("_r = 1")
        .drop("_r", "_priority")
    )

    # Same quarter, filed twice with period ends a day or two apart. NVDA's
    # 2010 Q2 appears as both 2010-05-03..2010-07-31 and 2010-05-03..2010-08-01
    # with identical values. The dates differ, so the join above keeps both.
    # Left alone these inflate a quarter count to 3 in a year that only has 2
    # real quarters, and the derived Q4 comes out wrong.
    # Collapse on the start date, keeping the newest filing.
    newest = Window.partitionBy("symbol", "metric", "period_start").orderBy(
        F.col("filed").desc(), F.col("period_end").desc()
    )
    return (
        one_per_period.withColumn("_d", F.row_number().over(newest))
        .filter("_d = 1")
        .drop("_d")
    )


@dlt.table(comment="Reported full-year figures from the 10-K.")
@dlt.expect_or_drop("has_value", "value IS NOT NULL")
def silver_annual_metrics():
    return _resolved("annual").select(
        "symbol", "metric", "concept",
        F.to_date("period_start").alias("period_start"),
        F.to_date("period_end").alias("period_end"),
        "value", "fiscal_year", "form", "filed", "accession",
    )


@dlt.table(comment="Quarterly figures. Q4 is derived, not reported.")
@dlt.expect_or_drop("has_value", "value IS NOT NULL")
# A derived Q4 that comes out negative means the annual figure and the
# quarters disagree, usually a restatement. Worth seeing, not worth dropping.
@dlt.expect("derived_q4_positive", "is_derived = false OR value > 0")
def silver_quarterly_metrics():
    reported = _resolved("quarter").select(
        "symbol", "metric", "concept",
        F.to_date("period_start").alias("period_start"),
        F.to_date("period_end").alias("period_end"),
        "value", "fiscal_year", "form", "filed", "accession",
        F.lit(False).alias("is_derived"),
    )

    annual = dlt.read("silver_annual_metrics").alias("a")
    q = reported.alias("q")

    # Quarters that fall inside each annual window.
    inside = (
        annual.join(
            q,
            (F.col("a.symbol") == F.col("q.symbol"))
            & (F.col("a.metric") == F.col("q.metric"))
            & (F.col("q.period_start") >= F.col("a.period_start"))
            & (F.col("q.period_end") <= F.col("a.period_end")),
            "inner",
        )
        .groupBy(
            F.col("a.symbol").alias("symbol"),
            F.col("a.metric").alias("metric"),
            F.col("a.concept").alias("concept"),
            F.col("a.period_start").alias("a_start"),
            F.col("a.period_end").alias("a_end"),
            F.col("a.value").alias("annual_value"),
            F.col("a.fiscal_year").alias("fiscal_year"),
            F.col("a.form").alias("form"),
            F.col("a.filed").alias("filed"),
            F.col("a.accession").alias("accession"),
        )
        .agg(
            F.count("*").alias("n_quarters"),
            F.sum(F.col("q.value")).alias("sum_quarters"),
            F.max(F.col("q.period_end")).alias("last_q_end"),
            # Older filings DO report Q4 as its own quarterly fact — NVDA did
            # until about 2011. If a quarter already ends on the annual end
            # date, Q4 exists and must not be invented a second time.
            F.max(
                F.when(F.col("q.period_end") == F.col("a.period_end"), True)
                 .otherwise(False)
            ).alias("q4_already_filed"),
        )
    )

    # Only derive when exactly three quarters are present. Anything else means
    # an incomplete year or overlapping windows, and a wrong Q4 would poison
    # two deltas and a briefing.
    derived = (
        inside.filter((F.col("n_quarters") == 3) & (~F.col("q4_already_filed")))
        .select(
            "symbol", "metric", "concept",
            F.date_add(F.col("last_q_end"), 1).alias("period_start"),
            F.col("a_end").alias("period_end"),
            (F.col("annual_value") - F.col("sum_quarters")).alias("value"),
            "fiscal_year", "form", "filed", "accession",
            F.lit(True).alias("is_derived"),
        )
    )
    return reported.unionByName(derived)


@dlt.table(comment="QoQ and YoY change, joined on dates rather than row order.")
def silver_financial_deltas():
    cur = dlt.read("silver_quarterly_metrics").alias("c")
    prior = dlt.read("silver_quarterly_metrics").alias("p")

    def nearest(offset_days: int, tol: int, suffix: str):
        """Match each quarter to the one ~offset_days earlier, if it exists."""
        gap = F.datediff(F.col("c.period_end"), F.col("p.period_end"))
        joined = cur.join(
            prior,
            (F.col("c.symbol") == F.col("p.symbol"))
            & (F.col("c.metric") == F.col("p.metric"))
            & (F.abs(gap - F.lit(offset_days)) <= F.lit(tol)),
            "left",
        )
        # More than one candidate can fall inside the tolerance; take the closest.
        closest = Window.partitionBy(
            "c.symbol", "c.metric", "c.period_end"
        ).orderBy(F.abs(gap - F.lit(offset_days)))
        return (
            joined.withColumn("_r", F.row_number().over(closest))
            .filter("_r = 1")
            .select(
                F.col("c.symbol").alias("symbol"),
                F.col("c.metric").alias("metric"),
                F.col("c.period_end").alias("period_end"),
                F.col("p.value").alias(f"prev_{suffix}"),
                F.col("p.period_end").alias(f"prev_{suffix}_end"),
                F.col("p.is_derived").alias(f"prev_{suffix}_derived"),
            )
        )

    base = cur.select(
        F.col("c.symbol").alias("symbol"),
        F.col("c.metric").alias("metric"),
        F.col("c.period_start").alias("period_start"),
        F.col("c.period_end").alias("period_end"),
        F.col("c.value").alias("value"),
        F.col("c.is_derived").alias("is_derived"),
        F.col("c.fiscal_year").alias("fiscal_year"),
    )

    out = (
        base
        .join(nearest(QOQ_DAYS, QOQ_TOL, "q"), ["symbol", "metric", "period_end"], "left")
        .join(nearest(YOY_DAYS, YOY_TOL, "y"), ["symbol", "metric", "period_end"], "left")
    )

    def pct(prev):
        # Guard the divide: a zero or missing base gives NULL, not infinity.
        return F.when(
            F.col(prev).isNotNull() & (F.col(prev) != 0),
            (F.col("value") - F.col(prev)) / F.abs(F.col(prev)),
        )

    return (
        out
        .withColumn("qoq_abs", F.col("value") - F.col("prev_q"))
        .withColumn("qoq_pct", pct("prev_q"))
        .withColumn("yoy_abs", F.col("value") - F.col("prev_y"))
        .withColumn("yoy_pct", pct("prev_y"))
        # True if any number in the row was computed rather than filed.
        .withColumn(
            "any_derived",
            F.col("is_derived")
            | F.coalesce(F.col("prev_q_derived"), F.lit(False))
            | F.coalesce(F.col("prev_y_derived"), F.lit(False)),
        )
    )
