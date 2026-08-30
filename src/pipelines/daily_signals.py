"""What landed for each account, on each day.

This is what picks the briefing mode, and it is deliberately plain SQL rather
than a model decision. When a briefing comes out wrong you want to point at a
row and see that Mode C was chosen because nothing landed - not re-run a model
and hope it decides the same way twice.

One row per account per day on which anything happened. Days with no signal
produce no row, and a missing row is itself the Mode C trigger.

Filings, earnings calls, news and rating changes all feed this. Macro stays at
zero on purpose: it describes the world rather than the account, so a rate move
is never on its own a reason to brief about one company.

Ratings only count when the rating actually moved. A firm reiterating its view
is not a signal.

The mode is chosen by strict priority, not a score:
  A  an earnings call or filing landed - it outranks everything
  B  real news, no earnings - the news leads, feedback comes later if there is room
  C  neither - the rep's queued topics choose the subject
"""
import dlt
from pyspark.sql import functions as F


@dlt.table(comment="Signals available per account per day. Drives briefing mode.")
@dlt.expect_or_drop("has_account", "symbol IS NOT NULL")
@dlt.expect_or_drop("has_date", "signal_date IS NOT NULL")
def silver_daily_signals():
    # A filing is dated by when it was filed, not the period it covers: a rep
    # cares that something landed today, not which quarter it describes.
    filings = (
        dlt.read("silver_xbrl_facts")
        .filter(F.col("form").isin("10-Q", "10-K"))
        .select("symbol", F.to_date("filed").alias("signal_date"), "accession")
        .distinct()
        .groupBy("symbol", "signal_date")
        .agg(F.count("*").alias("filings"))
    )

    calls = (
        dlt.read("silver_transcript_turns")
        .select("symbol", F.to_date("call_date").alias("signal_date"), "call_id")
        .distinct()
        .groupBy("symbol", "signal_date")
        .agg(F.count("*").alias("calls"))
    )

    news = (
        dlt.read("silver_doc_chunks")
        .filter(F.col("source_type") == "news")
        .select(F.col("account_id").alias("symbol"),
                F.col("published_at").alias("signal_date"), "chunk_id")
        .distinct()
        .groupBy("symbol", "signal_date")
        .agg(F.count("*").alias("news"))
    )

    # Only actual moves count. A firm reiterating its rating is not news, so
    # maintains and unchanged grades do not trigger a mode.
    ratings = (
        dlt.read("silver_rating_changes")
        .filter(F.col("changed") == True)
        .select("symbol", F.col("rating_date").alias("signal_date"), "grading_company")
        .distinct()
        .groupBy("symbol", "signal_date")
        .agg(F.count("*").alias("rating_changes"))
    )

    joined = (
        filings.join(calls, ["symbol", "signal_date"], "full_outer")
        .join(news, ["symbol", "signal_date"], "full_outer")
        .join(ratings, ["symbol", "signal_date"], "full_outer")
        .withColumn("filings", F.coalesce("filings", F.lit(0)))
        .withColumn("calls", F.coalesce("calls", F.lit(0)))
        .withColumn("news", F.coalesce("news", F.lit(0)))
        .withColumn("rating_changes", F.coalesce("rating_changes", F.lit(0)))
        # Macro is not account-specific, so it never triggers a mode on its own.
        .withColumn("macro_events", F.lit(0))
    )

    return (
        joined
        .withColumn(
            "total_signals",
            F.col("filings") + F.col("calls") + F.col("news")
            + F.col("rating_changes") + F.col("macro_events"),
        )
        # Strict priority, highest first. An earnings call or filing is a whole
        # quarter of new information and outranks everything. Real news outranks
        # a quiet day. Only when neither exists does the rep's own queue decide
        # the subject - which is also what stops Mode C inventing one.
        #
        # Deliberately NOT a score. A weighted blend would occasionally let three
        # minor news items outvote an earnings call, and no rep would forgive
        # that. Ranking is the point.
        .withColumn(
            "mode",
            F.when((F.col("calls") > 0) | (F.col("filings") > 0), F.lit("A"))
             .when((F.col("news") > 0) | (F.col("rating_changes") > 0), F.lit("B"))
             .otherwise(F.lit("C")),
        )
        .withColumn(
            "mode_rule",
            F.when((F.col("calls") > 0) | (F.col("filings") > 0),
                   F.lit("earnings outranks everything"))
             .when((F.col("news") > 0) | (F.col("rating_changes") > 0),
                   F.lit("news day; feedback deferred to later in the episode"))
             .otherwise(F.lit("quiet day; the rep's topic queue chooses the subject")),
        )
        .withColumn(
            "mode_reason",
            F.concat_ws(
                ", ",
                F.when(F.col("filings") > 0,
                       F.concat(F.col("filings").cast("string"), F.lit(" filing(s)"))),
                F.when(F.col("calls") > 0,
                       F.concat(F.col("calls").cast("string"), F.lit(" earnings call(s)"))),
                F.when(F.col("news") > 0,
                       F.concat(F.col("news").cast("string"), F.lit(" news item(s)"))),
                F.when(F.col("rating_changes") > 0,
                       F.concat(F.col("rating_changes").cast("string"),
                                F.lit(" rating change(s)"))),
            ),
        )
    )
