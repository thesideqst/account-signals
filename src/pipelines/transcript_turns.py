"""Bronze transcript turns -> silver_transcript_turns.

Chunks follow SPEAKER TURN BOUNDARIES rather than a token count. A turn is a
complete thought by one person; splitting mid-turn produces chunks whose
attribution is ambiguous, and attribution is the point — "the CFO said margins
compressed" is a different signal from "an analyst asked whether margins
compressed."

Roic returns only `speaker` and `text`. Role and section are derived here.

DERIVING SECTION
Prepared remarks run until the Operator hands over to the first analyst.
Every call marks that transition in Operator text ("Your next question comes
from the line of ... with ..."). Everything before the first such turn is
prepared remarks; everything after is Q&A.

That boundary matters for the briefing. Prepared remarks are written in
advance by IR and legal — maximum framing. Q&A is unscripted, where analysts
push and the language slips. A brief that quotes only prepared remarks is
quoting the press release.

DERIVING ROLE
Three kinds of speaker: operator, management, analyst.

The obvious heuristic — "management speaks during prepared remarks, analysts
appear only afterwards" — is WRONG, and the NVDA FY2027 Q2 call proves it.
Only CFO Colette Kress gave prepared remarks; Jensen Huang's first turn is
number 5, inside Q&A. That rule labels the CEO an analyst, which is the single
worst misclassification available here: his framing is the main thing the
briefing exists to contrast against the numbers.

The reliable signal is the handover itself. The Operator names each analyst
immediately before they speak, so the speaker in the turn directly after a
handover IS the analyst. Every other non-operator speaker is on the company's
side. This holds regardless of who happens to deliver prepared remarks.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Operator phrasing that hands over to an analyst. Anchored on the Operator's
# own turns, so an executive saying "question" in passing cannot trip it.
QA_HANDOVER = r"(?i)(next question|question comes from|line of|first question)"


@dlt.table(comment="Transcript turns with derived speaker role and call section.")
@dlt.expect_or_drop("has_text", "text IS NOT NULL AND length(text) > 0")
@dlt.expect_or_drop("has_speaker", "speaker IS NOT NULL")
# A call with no detected Q&A boundary is suspicious, not fatal: it may be a
# prepared-remarks-only call. Warn so it is visible rather than silently
# labelling an entire Q&A section as prepared remarks.
@dlt.expect("qa_boundary_found", "qa_start_index IS NOT NULL")
def silver_transcript_turns():
    # Bronze is append-only, so re-running the ingest lands the same call again.
    # Keep one row per turn, from the most recent ingest.
    newest = Window.partitionBy(
        "symbol", "fiscal_year", "fiscal_quarter", "turn_index"
    ).orderBy(F.col("_ingested_at").desc())
    turns = (
        dlt.read("bronze_transcript_turns")
        .withColumn("_r", F.row_number().over(newest))
        .filter("_r = 1")
        .drop("_r")
    )
    call = Window.partitionBy("symbol", "fiscal_year", "fiscal_quarter")

    is_operator = F.lower(F.col("speaker")) == "operator"
    handover = is_operator & F.col("text").rlike(QA_HANDOVER)

    return (
        turns
        # First operator handover in each call marks where Q&A begins.
        .withColumn(
            "qa_start_index",
            F.min(F.when(handover, F.col("turn_index"))).over(call),
        )
        .withColumn(
            "section",
            F.when(F.col("qa_start_index").isNull(), "prepared_remarks")
             .when(F.col("turn_index") < F.col("qa_start_index"), "prepared_remarks")
             .otherwise("qa"),
        )
        # The speaker immediately after an Operator handover is the analyst
        # being introduced. Collect those names per call; everyone else who is
        # not the Operator is management.
        .withColumn("prev_was_handover",
                    F.lag(handover).over(call.orderBy("turn_index")))
        .withColumn(
            "is_analyst_name",
            F.max(F.when(F.col("prev_was_handover") & ~is_operator, F.lit(True))
                   .otherwise(F.lit(False)))
             .over(Window.partitionBy("symbol", "fiscal_year",
                                      "fiscal_quarter", "speaker")),
        )
        .withColumn(
            "role",
            F.when(is_operator, "operator")
             .when(F.col("is_analyst_name"), "analyst")
             .otherwise("management"),
        )
        .withColumn("char_count", F.length("text"))
        .select(
            "symbol", "identifier", "call_id", "fiscal_year", "fiscal_quarter",
            "call_date", "turn_index", "speaker", "role", "section",
            "text", "char_count",
            # Kept in the output because the qa_boundary_found expectation
            # reads it, and because it is the first thing to check when a
            # call's sections look wrong.
            "qa_start_index",
        )
    )
