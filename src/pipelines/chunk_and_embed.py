"""All prose sources -> silver_doc_chunks, the Vector Search source table.

Everything that is language rather than number: transcripts, news and exec
statements, industry trends, and macro commentary. Analyst ratings are NOT
here — FMP grades carry no rationale text, so that source is purely
quantitative (SCOPE.md, 2026-08-29).

WHY CHUNK AT ALL
The briefing prompt should carry only passages relevant to one account, not
whole documents. Retrieval keeps the prompt small and makes every claim
traceable back to a source row.

TWO CHUNKING STRATEGIES, ONE TABLE
Transcripts already have natural boundaries: a speaker turn is one complete
thought by one person. Splitting mid-turn makes attribution ambiguous, and
attribution is the whole point of this source. So transcript chunks follow
turns, and only a turn that exceeds the embedding window gets split further —
on sentence boundaries, keeping speaker and role on every piece.

News, trends and macro have no such structure, so those fall back to
overlapping windows sized to the embedding model.

The union carries transcript-only columns as NULL for other sources. That is
deliberate: a single table keeps retrieval one query, and a NULL `speaker` on
a news chunk is honest rather than lossy. Filtering by speaker or section
simply selects the transcript rows, which is exactly what a question like
"what did the CFO say about margins" should do.

Change Data Feed is on so Vector Search syncs incrementally rather than
re-indexing the table on every pipeline run.
"""
import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Target chunk size in characters. This is NOT the model's limit.
#
# Measured against databricks-gte-large-en (gte_large_en_v1_5) on 2026-08-30:
# it accepts 8192 tokens, roughly 56,000 characters of this text. Nothing in
# a transcript comes close — the longest NVDA turn is 18,376 characters, or
# 2,670 tokens.
#
# Worth knowing anyway: at 60,000 characters the endpoint returned
# prompt_tokens=8192 and no error. It truncates silently rather than failing,
# so an oversized chunk would be quietly half-embedded.
#
# We split for RETRIEVAL QUALITY, not capacity. One vector for an 18,000
# character turn averages everything the speaker said across the whole of
# prepared remarks; a search for "margins" then matches the entire block with
# the signal diluted by twenty other topics. Chunks near 1,500 characters keep
# each vector about one thing.
MAX_CHUNK_CHARS = 1500


@dlt.table(
    comment="Chunked, embeddable text from all unstructured sources.",
    table_properties={"delta.enableChangeDataFeed": "true"},
)
@dlt.expect_or_drop("has_text", "chunk_text IS NOT NULL AND length(chunk_text) > 0")
@dlt.expect_or_drop("has_account", "account_id IS NOT NULL")
@dlt.expect_or_drop("has_source_type", "source_type IS NOT NULL")
# Every transcript chunk must keep its attribution. A transcript row with a
# NULL speaker means the join or split dropped it, which silently turns an
# attributed quote into an anonymous one.
@dlt.expect("transcript_keeps_speaker",
            "source_type <> 'transcript' OR speaker IS NOT NULL")
def silver_doc_chunks():
    turns = dlt.read("silver_transcript_turns")

    # One chunk per turn, split only when a turn is too long for the embedder.
    # Splitting on sentence ends rather than a hard character cut keeps each
    # piece readable and keeps the speaker's claim intact.
    # Split each turn into parts near MAX_CHUNK_CHARS, breaking on sentence
    # ends so a claim is never cut mid-thought. Every part keeps the speaker,
    # role and section of the turn it came from, so attribution survives.
    sentences = (
        turns
        # Operator turns are call logistics ("your next question comes from"),
        # not content. Dropping them keeps retrieval from surfacing plumbing.
        .filter(F.col("role") != "operator")
        .select(
            "*",
            F.posexplode(F.split(F.col("text"), r"(?<=[.!?])\s+")).alias("sent_pos", "sentence"),
        )
        .filter(F.length("sentence") > 0)
    )

    # Running character count within the turn, in sentence order.
    running = Window.partitionBy("call_id", "turn_index").orderBy("sent_pos").rowsBetween(
        Window.unboundedPreceding, Window.currentRow
    )
    packed = (
        sentences
        .withColumn("cum_chars", F.sum(F.length("sentence") + 1).over(running))
        # Sentences fall into the part their starting offset lands in, so each
        # part fills to about MAX_CHUNK_CHARS before the next one opens. A
        # single sentence longer than the target becomes its own oversized part
        # rather than being cut in half — still far under the model's ceiling.
        .withColumn(
            "part_index",
            F.floor((F.col("cum_chars") - F.length("sentence") - 1) / F.lit(MAX_CHUNK_CHARS)).cast("int"),
        )
    )

    transcript_chunks = (
        packed
        .groupBy(
            "symbol", "identifier", "call_id", "fiscal_year", "fiscal_quarter",
            "call_date", "turn_index", "speaker", "role", "section", "part_index",
        )
        .agg(
            # collect_list has no ordering guarantee, so sort by position
            # explicitly before joining the sentences back together.
            F.concat_ws(
                " ",
                F.transform(
                    F.array_sort(
                        F.collect_list(F.struct(F.col("sent_pos"), F.col("sentence")))
                    ),
                    lambda x: x["sentence"],
                ),
            ).alias("chunk_text"),
            F.count("*").alias("sentence_count"),
        )
        .select(
            F.col("symbol").alias("account_id"),
            F.lit("transcript").alias("source_type"),
            F.to_date("call_date").alias("published_at"),
            F.lit(None).cast("string").alias("url"),
            F.concat_ws(":", F.col("call_id"), F.col("turn_index"),
                        F.col("part_index")).alias("chunk_id"),
            "chunk_text",
            # Transcript-only attribution. NULL for every other source.
            "speaker", "role", "section",
            F.col("turn_index"), F.col("part_index"), "sentence_count",
            F.length("chunk_text").alias("char_count"),
            # Transcripts are attributed by speaker, not publication.
            F.lit(None).cast("string").alias("publisher"),
            F.lit(None).cast("string").alias("headline"),
            F.struct("fiscal_year", "fiscal_quarter").alias("period"),
        )
    )

    # Industry trends have no speaker and no section, so those columns are NULL.
    # A feed item is already short - a headline and a summary paragraph - so it
    # is one chunk, not a split. Title and summary are joined because a summary
    # alone often loses the subject.
    # One row per article. Feeds republish and the ingest appends, so without
    # this a re-run multiplies every item.
    dedupe_url = Window.partitionBy("url").orderBy(F.col("_ingested_at").desc())
    trends = (
        dlt.read("bronze_industry_trends")
        .filter(F.length(F.col("summary")) > 0)
        .withColumn("_r", F.row_number().over(dedupe_url))
        .filter("_r = 1")
        # Feeds publish dates in different formats and Spark 3's parser rejects
        # the RFC-822 weekday token outright ("Fri, 28 Aug 2026 10:00:00 +0000").
        # Pulling the date out with a regex sidesteps the pattern syntax and
        # only needs the day, since the briefing never uses the time.
        .withColumn(
            "published",
            F.coalesce(
                F.to_date(F.regexp_extract("published_at",
                                           r"(\d{1,2} [A-Za-z]{3} \d{4})", 1),
                          "d MMM yyyy"),
                F.to_date(F.regexp_extract("published_at",
                                           r"(\d{4}-\d{2}-\d{2})", 1),
                          "yyyy-MM-dd"),
            ),
        )
        .select(
            # Not account-specific. Relevance is decided at retrieval, so these
            # carry a sentinel rather than a symbol.
            F.lit("_industry").alias("account_id"),
            F.lit("industry_trend").alias("source_type"),
            F.col("published").alias("published_at"),
            F.col("url"),
            F.concat_ws(":", F.lit("trend"), F.col("source"),
                        F.abs(F.hash("url")).cast("string")).alias("chunk_id"),
            F.concat_ws(". ", F.col("title"), F.col("summary")).alias("chunk_text"),
            F.lit(None).cast("string").alias("speaker"),
            F.lit(None).cast("string").alias("role"),
            F.lit(None).cast("string").alias("section"),
            F.lit(None).cast("int").alias("turn_index"),
            F.lit(0).alias("part_index"),
            F.lit(1).alias("sentence_count"),
            F.length(F.concat_ws(". ", F.col("title"), F.col("summary"))).alias("char_count"),
            F.lit(None).cast("struct<fiscal_year:int,fiscal_quarter:int>").alias("period"),
        )
    )

    # News IS account-specific, so it carries the real symbol rather than the
    # _industry sentinel. Headlines are short, so one item is one chunk.
    news = (
        dlt.read("bronze_news")
        .filter(F.length(F.col("title")) > 0)
        .withColumn("_r", F.row_number().over(dedupe_url))
        .filter("_r = 1")
        .withColumn(
            "published",
            F.coalesce(
                F.to_date(F.regexp_extract("published_at",
                                           r"(\d{1,2} [A-Za-z]{3} \d{4})", 1),
                          "d MMM yyyy"),
                F.to_date(F.regexp_extract("published_at",
                                           r"(\d{4}-\d{2}-\d{2})", 1),
                          "yyyy-MM-dd"),
            ),
        )
        .select(
            F.col("symbol").alias("account_id"),
            F.lit("news").alias("source_type"),
            F.col("published").alias("published_at"),
            F.col("url"),
            F.concat_ws(":", F.lit("news"), F.col("source"),
                        F.abs(F.hash("url")).cast("string")).alias("chunk_id"),
            F.concat_ws(". ", F.col("title"), F.col("summary")).alias("chunk_text"),
            F.lit(None).cast("string").alias("speaker"),
            F.lit(None).cast("string").alias("role"),
            F.lit(None).cast("string").alias("section"),
            F.lit(None).cast("int").alias("turn_index"),
            F.lit(0).alias("part_index"),
            F.lit(1).alias("sentence_count"),
            F.length(F.concat_ws(". ", F.col("title"), F.col("summary"))).alias("char_count"),
            # Google News nests the outlet; Yahoo often leaves it blank.
            F.coalesce(F.nullif(F.col("publisher"), F.lit("")),
                       F.col("source")).alias("publisher"),
            F.col("title").alias("headline"),
            F.lit(None).cast("struct<fiscal_year:int,fiscal_quarter:int>").alias("period"),
        )
    )

    return (transcript_chunks
            .unionByName(trends, allowMissingColumns=True)
            .unionByName(news, allowMissingColumns=True))
