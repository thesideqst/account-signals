"""silver_metric_context -> gold_cross_account_themes.

With three accounts live, a rep playing several episodes back to back hears
the same explanation more than once - memory pricing squeezing margins at
every semiconductor account, say. SCOPE.md's "Cross-account trends" backlog
item argues this is worth closing: not a portfolio insight, just letting one
episode's explanation be acknowledged by another in a sentence instead of
re-derived from scratch.

TWO SIGNALS WERE CONSIDERED. Only one is built here.

  1. Metric correlation (built here): silver_metric_context already computes
     margin direction, growth acceleration, and cost-vs-revenue gaps per
     account per quarter, entirely in SQL. A same-direction move on the same
     metric and the same basis (QoQ or YoY - never mixed, per VOICE_RULES) at
     2+ accounts is a real, checkable fact. No new inference is needed; this
     file only regroups numbers that already exist.

  2. Retrieved-source overlap (NOT built here): the backlog calls this "the
     cleanest signal" - if the same news/industry-trend chunk feeds 2+
     accounts' gold_briefing.lineage on the same day, that is overlap by
     construction. It was left out of this first pass because all three live
     accounts (NVDA, GOOG, MU) are semiconductor/tech-adjacent, and
     industry_trend chunks carry the sentinel account_id '_industry' SHARED
     ACROSS EVERY ACCOUNT BY CONSTRUCTION (see chunk_and_embed.py). A naive
     overlap detector would fire on that sentinel constantly - "all
     semiconductor companies face memory pricing" is a tautology, not an
     insight - and the backlog explicitly flags that telling a real
     cross-cutting theme from plain sector membership is an open problem, not
     one to solve algorithmically here. Metric correlation carries a milder
     version of the same risk (three chip-adjacent companies will sometimes
     move together for boring sector reasons), but it is at least a specific,
     checkable numeric fact rather than "the same generic chunk got
     retrieved" - so it is the one implemented, per the task's instruction to
     weight it as primary. If source-overlap detection is built later, treat
     the _industry sentinel as a reason to weight it DOWN, not as a shortcut.

WHY EACH ACCOUNT'S OWN LATEST QUARTER, NOT A CALENDAR WINDOW. NVDA, GOOG and
MU report on different fiscal calendars (see ARCHITECTURE.md's "Known risks"
on NVDA-shaped fiscal-calendar assumptions), so "the same calendar day" or
even "the same week" almost never lines up across all three. What matters for
today's episode is each account's OWN most recent reported quarter - exactly
the row synthesize.py already queries via `max(period_end)` for its
account - so this table keys off the same grain. Two accounts flagged here as
sharing a theme may have reported weeks or months apart; that is fine, because
the claim is "this direction is ALSO true right now at the other account",
not "on the same day".
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Column in silver_metric_context -> (theme key, word for >= 0, word for < 0).
#
# Basis (QoQ vs YoY) is folded into the theme key on purpose. VOICE_RULES
# forbids ever comparing a QoQ figure to a YoY one ("Never compare one basis
# to the other"), so a QoQ move at one account must never be treated as "the
# same" as a YoY move at another.
#
# The words themselves are copied verbatim from synthesize.py's context_lines
# block (~line 453-497), not reinvented. Per SCOPE.md 2026-08-30/2026-09-01,
# a signed number handed to the model with a sign-reading rule gets read
# backwards; the fix was stating direction in words. Reusing exactly those
# words means a theme flagged here always matches what the model would say
# about its OWN number, so there is nothing for the model to reconcile.
DIRECTED_METRICS = {
    "gross_margin_bps_qoq": (
        "gross_margin_qoq", "EXPANDED", "COMPRESSED"),
    "gross_margin_bps_yoy": (
        "gross_margin_yoy", "EXPANDED", "COMPRESSED"),
    "operating_margin_bps_qoq": (
        "operating_margin_qoq", "EXPANDED", "COMPRESSED"),
    "cost_vs_revenue_growth_gap_pp": (
        "cost_vs_revenue_gap",
        "COSTS GREW FASTER THAN REVENUE", "COSTS GREW SLOWER THAN REVENUE"),
    "revenue_growth_accel_pp": (
        "revenue_growth", "ACCELERATING", "SLOWING"),
    "net_vs_operating_growth_gap_pp": (
        "net_vs_operating_gap",
        "NET INCOME GREW FASTER THAN OPERATING INCOME",
        "NET INCOME GREW SLOWER THAN OPERATING INCOME"),
}


@dlt.table(
    comment="Same-direction metric moves shared by 2+ accounts, each in its "
            "own most recent reported quarter. Lets one episode acknowledge "
            "another instead of re-deriving the same explanation."
)
@dlt.expect_or_drop("has_account", "account_id IS NOT NULL")
@dlt.expect_or_drop("has_metric", "metric IS NOT NULL")
def gold_cross_account_themes():
    ctx = dlt.read("silver_metric_context")

    # Each account's own most recent quarter - see module docstring for why
    # this is the right grain instead of a shared calendar window. Ties on
    # period_end are broken deterministically so a refresh cannot silently
    # change which quarter is "latest" for an account with duplicate rows.
    latest_w = Window.partitionBy("symbol").orderBy(
        F.col("period_end").desc())
    latest = (
        ctx.withColumn("_r", F.row_number().over(latest_w))
        .filter("_r = 1")
        .drop("_r")
    )

    # Long format: one row per (symbol, metric, direction). Metrics with no
    # value for this account/quarter are dropped rather than guessing a
    # direction for NULL - same discipline as _prior() in metric_context.py,
    # where a missing partner produces no row rather than an invented one.
    long_rows = None
    for col, (metric, pos_word, neg_word) in DIRECTED_METRICS.items():
        piece = (
            latest.filter(F.col(col).isNotNull())
            .select(
                F.col("symbol"),
                F.col("period_end"),
                F.lit(metric).alias("metric"),
                F.when(F.col(col) >= 0, F.lit(pos_word))
                 .otherwise(F.lit(neg_word)).alias("direction"),
                F.abs(F.col(col)).alias("magnitude"),
            )
        )
        long_rows = piece if long_rows is None else long_rows.unionByName(piece)

    # Self-join on (metric, direction): which OTHER accounts are moving the
    # same way, on the same basis, in their own latest quarter right now.
    # a != b keeps an account off its own "other accounts" list.
    a, b = long_rows.alias("a"), long_rows.alias("b")
    paired = a.join(
        b,
        (F.col("a.metric") == F.col("b.metric"))
        & (F.col("a.direction") == F.col("b.direction"))
        & (F.col("a.symbol") != F.col("b.symbol")),
        "inner",
    )

    # collect_set of a STRUCT, not two parallel arrays. Two independent
    # collect_set() calls over "symbol" and "period_end" are not guaranteed
    # to return same-indexed elements in the same order, which would let a
    # reader zip() an account with the wrong quarter. A struct array carries
    # each pair correctly no matter what order Spark returns them in.
    return (
        paired.groupBy(
            "a.symbol", "a.period_end", "a.metric", "a.direction", "a.magnitude")
        .agg(
            F.collect_set(
                F.struct(F.col("b.symbol").alias("account_id"),
                          F.col("b.period_end").alias("period_end"))
            ).alias("other_accounts")
        )
        .select(
            F.col("a.symbol").alias("account_id"),
            F.col("a.period_end").alias("period_end"),
            F.col("a.metric").alias("metric"),
            F.col("a.direction").alias("direction"),
            F.col("a.magnitude").alias("magnitude"),
            "other_accounts",
            F.current_timestamp().alias("computed_at"),
        )
    )
