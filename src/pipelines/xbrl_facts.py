"""Bronze XBRL facts -> silver_xbrl_facts.

Cleans the raw facts: classifies how long each reporting period is, and drops
duplicates. Everything downstream reads this.

Two things to know about the raw data:

Period length tells you what kind of number you have. A quarter is about 90
days, a year about 365. A fact with no start date is a point-in-time value
like cash on hand. A 10-Q also carries year-to-date figures (six months, nine
months), which look like ordinary facts but would double-count if mixed into
a quarterly series, so they get labelled 'cumulative' and left out.

The same quarter appears more than once. A 10-K repeats the prior year for
comparison, so one quarter arrives under two different fiscal years with the
same value. Dedupe on the period dates and keep the newest filing. Do not
dedupe on fiscal_year, which is exactly what differs between the copies.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

QUARTER_DAYS = (85, 95)    # 85 covers 13-week retail quarters
ANNUAL_DAYS = (360, 372)   # 372 covers 53-week years


@dlt.table(comment="Deduplicated XBRL facts with period length classified.")
@dlt.expect_or_drop("has_value", "value IS NOT NULL")
@dlt.expect_or_drop("has_period_end", "period_end IS NOT NULL")
def silver_xbrl_facts():
    src = dlt.read("bronze_xbrl_facts")
    dur = F.datediff(F.to_date("period_end"), F.to_date("period_start"))

    newest_first = Window.partitionBy(
        "symbol", "concept", "unit", "period_start", "period_end"
    ).orderBy(F.col("filed").desc())

    return (
        src
        .withColumn("duration_days", dur)
        .withColumn(
            "period_kind",
            F.when(F.col("period_start").isNull(), "instant")
             .when(dur.between(*QUARTER_DAYS), "quarter")
             .when(dur.between(*ANNUAL_DAYS), "annual")
             .otherwise("cumulative"),
        )
        .withColumn("_r", F.row_number().over(newest_first))
        .filter("_r = 1")
        .drop("_r")
    )
