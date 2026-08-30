"""Bronze analyst grades -> silver_rating_changes.

Turns FMP's grade strings into a purely quantitative signal: did the rating
change, in which direction, and by how much (SCOPE.md, 2026-08-29).

Direction is nearly free — FMP's own `action` field already says upgrade,
downgrade, initiate, or maintain. Magnitude is not, because analyst firms do
not share a vocabulary: Morgan Stanley says Overweight / Equal-Weight /
Underweight, others say Buy / Hold / Sell, others Outperform / Market Perform
/ Underperform. "Equal-Weight -> Overweight" and "Hold -> Buy" are the same
one-notch move, and only look different because two firms chose different words.

So the grades are mapped onto a shared 1-5 ordinal scale first, and magnitude
is the difference in notches on that scale.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Shared ordinal scale. Keys are lowercased and stripped of spaces/hyphens
# before lookup, so "Equal-Weight", "equal weight" and "EqualWeight" all match.
GRADE_SCALE = {
    # 5 — strongest conviction
    "strongbuy": 5, "convictionbuy": 5, "toppick": 5,
    # 4 — positive
    "buy": 4, "outperform": 4, "overweight": 4, "accumulate": 4,
    "positive": 4, "add": 4, "marketoutperform": 4, "sectoroutperform": 4,
    # 3 — neutral
    "hold": 3, "neutral": 3, "marketperform": 3, "equalweight": 3,
    "sectorperform": 3, "inline": 3, "peerperform": 3, "sectorweight": 3,
    # Bare "Perform" appeared in real FMP data (2 of 1,138 NVDA actions) and is
    # a shortening of market/peer perform, so it maps to neutral.
    "perform": 3,
    # 2 — negative
    "sell": 2, "underperform": 2, "underweight": 2, "reduce": 2,
    "negative": 2, "marketunderperform": 2, "sectorunderperform": 2,
    # 1 — weakest conviction
    "strongsell": 1,
}


def _ordinal(col):
    """Map a raw grade string to its ordinal, or NULL if the vocabulary is new.

    NULL rather than a default is deliberate. Defaulting an unrecognized grade
    to 3 (neutral) would silently invent a move: an unmapped "Buy" becomes a
    one-notch downgrade that no analyst made. A NULL magnitude is visibly
    missing; a wrong magnitude is not.
    """
    key = F.regexp_replace(F.lower(F.trim(col)), r"[\s\-_/]", "")
    return F.create_map(*[x for k, v in GRADE_SCALE.items()
                          for x in (F.lit(k), F.lit(v))])[key]


@dlt.table(comment="Analyst rating moves as direction and magnitude in notches.")
@dlt.expect_or_drop("has_symbol", "symbol IS NOT NULL")
@dlt.expect_or_drop("has_date", "rating_date IS NOT NULL")
# Warn rather than drop: an unmapped grade is a vocabulary gap to fix in
# GRADE_SCALE, not a bad row. Dropping it would hide the gap.
@dlt.expect("grade_vocabulary_known", "new_ordinal IS NOT NULL")
def silver_rating_changes():
    # Same append-only problem: one row per firm action per date.
    newest = Window.partitionBy(
        "symbol", "rating_date", "grading_company", "new_grade", "previous_grade"
    ).orderBy(F.col("_ingested_at").desc())
    raw = (
        dlt.read("bronze_analyst_ratings")
        .withColumn("_r", F.row_number().over(newest))
        .filter("_r = 1")
        .drop("_r")
    )
    return (
        raw
        .withColumn("prev_ordinal", _ordinal(F.col("previous_grade")))
        .withColumn("new_ordinal", _ordinal(F.col("new_grade")))
        # An initiation has no prior grade, so its magnitude is unknown, not
        # zero. Zero would read as "a firm looked and held" — the opposite of
        # new coverage starting.
        .withColumn(
            "notch_delta",
            F.when(F.lower("action") == "initiate", F.lit(None).cast("int"))
             .otherwise(F.col("new_ordinal") - F.col("prev_ordinal")),
        )
        .withColumn("direction", F.signum("notch_delta").cast("int"))
        .withColumn("changed", F.coalesce(F.col("notch_delta") != 0, F.lit(False)))

        .select(
            "symbol", "rating_date", "grading_company", "action",
            "previous_grade", "new_grade",
            "prev_ordinal", "new_ordinal",
            "notch_delta",   # signed magnitude: -4..+4, NULL on initiate
            "direction",     # -1 down, 0 flat, +1 up, NULL on initiate
            "changed",       # did the rating actually move
        )
    )
