"""Silver -> gold_briefing. Picks a mode, then writes the script.

The whole project points here. Two inputs arrive in deliberately different
shapes and the contrast between them is the product:

  measured   silver_financial_deltas, already computed. The model is told
             these numbers and narrates them. It does not calculate.
  framed     silver_doc_chunks, what management said on the call, attributed
             to a named speaker and tagged prepared_remarks or qa.

The mode comes from silver_daily_signals, not from the model. Code decides
which mode; the prompt in prompts.py carries the narrative architecture for
that mode. A wrong mode is then a SQL bug and a bad script is a prompt bug,
which keeps the two diagnosable separately.

Retrieval is a plain SQL read for now. With one account and 40 chunks the
whole call fits in the prompt, so Vector Search would add a moving part
without changing the output. It earns its place when the account list and
source count grow.
"""
import json
import re
import sys



# FRED returns bare numbers; the SERIES is what makes them mean something.
# Without a unit the prompt carried "Private nonresidential fixed investment:
# 4623.36" and the script said "climbing to 4 623" - a listener hears "four,
# six twenty three", and the grounding guard flagged 623.36 because the space
# split the number in two. A figure with no unit is not a fact, it is a digit
# string, and the model will invent a unit or mangle the number rather than
# leave a gap.
#
# `change` differs from `unit` on purpose: a rate that moves from 4.5 to 4.7
# has risen by 0.2 PERCENTAGE POINTS, not 0.2 percent. That distinction is
# exactly the kind a briefing gets wrong and sounds confident about.
MACRO_UNITS = {
    "DGS10":    {"unit": "percent", "change": "percentage points"},
    "FEDFUNDS": {"unit": "percent", "change": "percentage points"},
    "T10Y2Y":   {"unit": "percentage points", "change": "percentage points"},
    "CPIAUCSL": {"unit": "index points, where 1982 to 1984 equals 100",
                 "change": "index points"},
    # Reported in billions at a seasonally adjusted annual rate. Said aloud,
    # "4,623 billion" is worse than "$4.6 trillion", so large values scale.
    "PNFI":     {"dollars_billions": True},
}


def macro_value(series_id: str, value) -> str:
    """A FRED observation with its unit attached."""
    if value is None:
        return "unavailable"
    spec = MACRO_UNITS.get(series_id)
    if not spec:
        return f"{value:.2f}"
    if spec.get("dollars_billions"):
        return (f"${value / 1000:.2f} trillion at an annual rate"
                if abs(value) >= 1000 else f"${value:.1f} billion at an annual rate")
    return f"{value:.2f} {spec['unit']}"


def macro_change(series_id: str, value) -> str:
    """How far a series moved, in the unit a MOVE is measured in."""
    if value is None:
        return "unavailable"
    spec = MACRO_UNITS.get(series_id)
    if not spec:
        return f"{value:.2f}"
    if spec.get("dollars_billions"):
        return f"${abs(value):.1f} billion"
    return f"{abs(value):.2f} {spec['change']}"


def figures(text: str) -> set:
    """Every numeric value mentioned in a piece of text, as floats.

    Used by the grounding guard to compare what the script says against what
    the model was handed. Commas are stripped so "96,221" and "96221" compare
    equal; anything that will not parse is skipped rather than raising, because
    a guard that crashes the run is worse than one that misses a token.
    """
    vals = set()
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            vals.add(float(tok.replace(",", "")))
        except ValueError:
            pass
    return vals


def extract_text(content) -> str:
    """Pull the spoken text out of a chat response.

    gpt-oss returns content as a list of parts (reasoning, then text) rather
    than a plain string, so a naive read gets a list. Reasoning parts are
    skipped; only text is kept.
    """
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        t = getattr(part, "text", None)
        if t is None and isinstance(part, dict):
            t = part.get("text") or part.get("content")
        if t:
            parts.append(t)
    return "\n".join(parts)


def first_point(text: str, limit: int = 240) -> str:
    """Reduce a recorded gap to the one point the cold open can carry.

    The two sources write `missed` in different shapes. Comprehension grading
    got more verbose as the prompt grew: early answers stored a single short
    sentence (about 110 characters), later ones store the gap, a blank line,
    and then a paragraph explaining it - up to 760 characters. Handed the whole
    thing, the cold open recites an essay before it reaches the news, which is
    the same failure that made the recap path pass ONE ranked gap rather than
    the whole JSON list.

    So: take the text before the first blank line, flatten any remaining line
    breaks, and cut at a sentence boundary if it is still long. Verified
    against every recorded gap - both shapes survive it intact.
    """
    if not text:
        return ""
    head = text.strip().split("\n\n", 1)[0]
    head = " ".join(head.split())
    if len(head) <= limit:
        return head
    # Prefer the last sentence end inside the limit over a mid-word chop.
    cut = max(head.rfind(". ", 0, limit), head.rfind("; ", 0, limit))
    return head[:cut + 1].strip() if cut > 60 else head[:limit].rstrip() + "..."


def main() -> None:
    import prompts
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
    from pyspark.sql import SparkSession

    catalog, schema, endpoint = sys.argv[1], sys.argv[2], sys.argv[3]
    # Optional overrides so a queued topic can be turned into an episode on
    # demand instead of waiting for a day with no earnings and no news.
    force_mode = sys.argv[4] if len(sys.argv) > 4 else ""
    force_topic = sys.argv[5] if len(sys.argv) > 5 else ""
    spark = SparkSession.builder.getOrCreate()
    w = WorkspaceClient()

    # Which account this run is for. The job fans out over the list.
    account = sys.argv[6] if len(sys.argv) > 6 else "NVDA"

    # Latest quarter with an earnings call attached.
    period = spark.sql(f"""
        SELECT max(period_end) AS p
        FROM {catalog}.{schema}.silver_financial_deltas WHERE symbol = '{account}'
    """).collect()[0]["p"]
    if period is None:
        # No Silver for this account yet, usually because the pipeline has not
        # rebuilt since its Bronze landed. Say so plainly rather than letting a
        # NULL period reach SQL as the string 'None' and surface as a
        # DateTimeException that says nothing about the real cause.
        raise SystemExit(
            f"no financial deltas for {account}. Run the signals pipeline "
            f"after ingesting, then retry.")

    deltas = spark.sql(f"""
        SELECT metric, value, qoq_pct, yoy_pct, is_derived, prev_q_end, prev_y_end
        FROM {catalog}.{schema}.silver_financial_deltas
        WHERE symbol = '{account}' AND period_end = '{period}'
        ORDER BY metric
    """).collect()

    # Hand over three metrics, not five. Told to "select two or three", the model
    # narrated all five and produced a paragraph carrying twelve figures - it was
    # reading the input back. Selecting here is the same fix as the callback:
    # give it the choice already made.
    #
    # Revenue always goes in; it is the spine. The other two are whichever
    # diverge most from revenue's own growth rate, because divergence is where
    # the story is - a cost line growing faster than sales, or profit growing
    # slower than operating income.
    # Spark Row has no .get(), so convert before treating these as dicts.
    by_metric = {r["metric"]: r.asDict() for r in deltas}
    deltas = [r.asDict() for r in deltas]
    rev_yoy = by_metric.get("revenue", {}).get("yoy_pct")
    def divergence(r):
        if r["metric"] == "revenue" or r.get("yoy_pct") is None or rev_yoy is None:
            return -1.0
        return abs(r["yoy_pct"] - rev_yoy)
    ranked = sorted((r for r in deltas if r["metric"] != "revenue"),
                    key=divergence, reverse=True)[:2]
    deltas = ([by_metric["revenue"]] if "revenue" in by_metric else []) + ranked
    print("metrics selected: " + ", ".join(r["metric"] for r in deltas))

    # Mode is needed before retrieval, because what gets retrieved depends on it.
    _sig = spark.sql(f"""
        SELECT mode FROM {catalog}.{schema}.silver_daily_signals
        WHERE symbol = '{account}' AND signal_date = (
            SELECT max(signal_date) FROM {catalog}.{schema}.silver_daily_signals
            WHERE symbol = '{account}')
    """).collect()
    mode = _sig[0]["mode"] if _sig else "C"
    if force_mode:
        mode = force_mode
        print(f"mode forced to {mode} by request")

    # On a quiet day the rep's queue picks the subject. Without this Mode C has
    # nothing to deep-dive on and the model chooses from its own knowledge,
    # which is the one thing the grounding rule exists to prevent.
    requested_topic = force_topic
    if mode == "C" and not requested_topic:
        try:
            q = spark.sql(f"""
                SELECT topic FROM {catalog}.{schema}.topic_queue_current
                WHERE account_id = '{account}'
                ORDER BY requested_at ASC LIMIT 1
            """).collect()
            if q:
                requested_topic = q[0]["topic"]
                print(f"mode C subject from the queue: {requested_topic[:80]}")
            else:
                print("mode C with an empty queue - no requested subject")
        except Exception as e:
            print(f"  topic queue unavailable: {type(e).__name__}")

    # A deep dive is about how something works, not about the quarter. Handing
    # it five metrics and a page of margin arithmetic is why it kept turning
    # into an earnings recap: the model uses what it is given.
    if mode == "C":
        deltas = [r for r in deltas if r["metric"] == "revenue"]
        print("mode C: financials trimmed to revenue only, for scale")

    delta_lines = []
    for r in deltas:
        f = lambda x: "not available" if x is None else f"{x * 100:+.1f}%"
        note = (" (DERIVED: the company did not file this figure; it was computed "
                "as the annual total minus the three reported quarters)"
                if r["is_derived"] else " (as filed)")
        # Revenue carries both rates because it anchors the episode. The two
        # supporting metrics get year-over-year only: handing over six growth
        # figures invites six growth figures back.
        if r["metric"] == "revenue":
            delta_lines.append(
                f"- {r['metric']}: ${r['value'] / 1e9:,.1f} billion{note}. "
                f"Quarter over quarter {f(r['qoq_pct'])}. "
                f"Year over year {f(r['yoy_pct'])}."
            )
        else:
            delta_lines.append(
                f"- {r['metric']}: ${r['value'] / 1e9:,.1f} billion{note}, "
                f"year over year {f(r['yoy_pct'])}."
            )

    # The computed "so what": margins, basis-point moves, growth relationships.
    # Handed to the model already calculated so it explains rather than derives.
    ctx = spark.sql(f"""
        SELECT * FROM {catalog}.{schema}.silver_metric_context
        WHERE symbol = '{account}' AND period_end = '{period}'
    """).collect()
    # Signed numbers with a legend are dangerous: an earlier run was handed
    # "+20.6 percentage points (negative means growth is slowing)" and reported
    # that growth SLOWED by 20.6 points, when it had accelerated by that much.
    # It read the magnitude and guessed the direction. So the direction is
    # stated in words here and the model is never asked to apply a sign rule.
    context_lines = []
    if ctx and mode != "C":
        c = ctx[0].asDict()

        def line(key, template_pos, template_neg, fmt=".1f"):
            v = c.get(key)
            if v is None:
                return
            t = template_pos if v >= 0 else template_neg
            context_lines.append("- " + t.format(v=format(abs(v), fmt)))

        if c.get("gross_margin_pct") is not None:
            context_lines.append(
                f"- Gross margin is {c['gross_margin_pct']:.1f} percent of revenue.")
        line("gross_margin_bps_qoq",
             "Gross margin EXPANDED by {v} basis points versus last quarter.",
             "Gross margin COMPRESSED by {v} basis points versus last quarter.", ".0f")
        line("gross_margin_bps_yoy",
             "Gross margin EXPANDED by {v} basis points versus a year ago.",
             "Gross margin COMPRESSED by {v} basis points versus a year ago.", ".0f")
        if c.get("operating_margin_pct") is not None:
            context_lines.append(
                f"- Operating margin is {c['operating_margin_pct']:.1f} percent of revenue.")
        line("operating_margin_bps_qoq",
             "Operating margin EXPANDED by {v} basis points versus last quarter.",
             "Operating margin COMPRESSED by {v} basis points versus last quarter.", ".0f")
        if c.get("net_margin_pct") is not None:
            context_lines.append(
                f"- Net margin is {c['net_margin_pct']:.1f} percent of revenue.")
        line("cost_vs_revenue_growth_gap_pp",
             "Costs grew FASTER than revenue by {v} percentage points year-over-year, "
             "which squeezes margin.",
             "Costs grew SLOWER than revenue by {v} percentage points year-over-year, "
             "which widens margin.")
        line("revenue_growth_accel_pp",
             "Revenue growth is ACCELERATING: the year-over-year growth rate is {v} "
             "percentage points HIGHER than it was last quarter.",
             "Revenue growth is SLOWING: the year-over-year growth rate is {v} "
             "percentage points LOWER than it was last quarter.")
        line("net_vs_operating_growth_gap_pp",
             "Net income grew FASTER than operating income by {v} percentage points "
             "year-over-year, which points to something below the operating line helping.",
             "Net income grew SLOWER than operating income by {v} percentage points "
             "year-over-year, which points to something below the operating line - tax, "
             "interest or a one-off - taking a bite.")

    # Management only. Analyst questions set up the answers but are not framing.
    chunks = spark.sql(f"""
        SELECT speaker, role, section, chunk_text
        FROM {catalog}.{schema}.silver_doc_chunks
        WHERE account_id = '{account}' AND source_type = 'transcript'
          AND role = 'management'
        ORDER BY turn_index, part_index
    """).collect()

    # The bracketed labels are metadata for you, not text to speak. An earlier
    # run read "[Toshiya Hari, prepared_remarks]" aloud verbatim.
    # On a news day the earnings call is not the subject. Passing all 32 chunks
    # of it buried 18 headlines by sheer volume and produced an earnings recap
    # labelled "Today's news", so Mode B gets a short excerpt for context only.
    if mode == "B":
        chunks = [c for c in chunks if c["section"] == "prepared_remarks"][:4]
        print(f"mode B: transcript trimmed to {len(chunks)} chunks for context")

    framing = "\n\n".join(
        f"SPEAKER: {c['speaker']} | SECTION: {c['section']}\nSAID: {c['chunk_text']}"
        for c in chunks
    )

    # Only raise derivation when something actually is derived. Mentioning it
    # unconditionally made the model announce that every figure was derived
    # when all five were filed as reported.
    any_derived = any(r["is_derived"] for r in deltas)
    derived_note = (
        "One or more figures above is marked DERIVED. For those, say in passing "
        "that the company did not file the number and it was computed. Do not "
        "say this about any figure marked 'as filed'.\n"
        if any_derived
        else "Every figure above is as filed by the company. Do not describe any "
             "of them as derived, computed, estimated, or inferred.\n"
    )

    # Mode for the day this briefing covers. No row means nothing landed,
    # which is itself the Mode C signal.
    sig = spark.sql(f"""
        SELECT mode, mode_reason, total_signals
        FROM {catalog}.{schema}.silver_daily_signals
        WHERE symbol = '{account}' AND signal_date = (
            SELECT max(signal_date) FROM {catalog}.{schema}.silver_daily_signals
            WHERE symbol = '{account}')
    """).collect()
    if sig:
        # mode was already resolved above, including any forced override.
        # Reassigning it here is what made "generate now" silently produce an
        # ordinary Mode B episode: the force was applied and then thrown away.
        mode_reason = sig[0]["mode_reason"] or "signals present"
    else:
        mode_reason = "no signals on record for this account"
    if force_topic:
        mode_reason = f"requested: {force_topic[:120]}"
    print(f"mode {mode} - {mode_reason}")

    # The most recent thing this rep got wrong becomes the cold-open callback.
    #
    # TWO sources feed it, because the rep can be measured two ways and only
    # one of them is currently reachable in the app. Comprehension questions
    # (bronze_recap_answers) are what people actually answer; a free-form
    # spoken recap (gold_recall_grades) measures UNAIDED recall, which is the
    # harder and more realistic test, but has no UI yet. Reading only the recap
    # table meant every gap a real person produced was ignored - the loop
    # closed on the path nobody used.
    #
    # The rule is plain recency: whichever gap is newer wins. No source
    # precedence to reason about, and it keeps working unchanged if the recap
    # ever gets a UI. Neither table exists until its loop runs, so absence of
    # either is normal and must not fail the briefing.
    candidates = []

    try:
        # Comprehension questions. `missed` is only written when something was
        # actually missed, so a non-empty value IS the gap - no parsing needed.
        # Ties on the timestamp are broken by answer_id so a re-run of the same
        # briefing picks the same gap rather than an arbitrary one.
        a = spark.sql(f"""
            SELECT missed, answered_at
            FROM {catalog}.{schema}.bronze_recap_answers
            WHERE account_id = '{account}'
              AND missed IS NOT NULL AND trim(missed) <> ''
            ORDER BY answered_at DESC, answer_id DESC
            LIMIT 1
        """).collect()
        if a:
            point = first_point(a[0]["missed"])
            if point:
                candidates.append((a[0]["answered_at"], point))
    except Exception:
        pass

    try:
        g = spark.sql(f"""
            SELECT gaps, graded_at FROM {catalog}.{schema}.gold_recall_grades
            WHERE account_id = '{account}'
            ORDER BY graded_at DESC, recap_id DESC LIMIT 1
        """).collect()
        if g:
            # Pass the single most important gap, phrased as a sentence. Handing
            # over the whole JSON list produced a cold open that recited eight
            # items and scolded three times.
            parsed = json.loads(g[0]["gaps"] or "[]")
            rank = {"high": 0, "medium": 1, "low": 2}
            parsed.sort(key=lambda x: rank.get(str(x.get("importance")).lower(), 3))
            if parsed:
                point = first_point(parsed[0].get("point", ""))
                if point:
                    candidates.append((g[0]["graded_at"], point))
    except Exception:
        pass

    # max() on the timestamp, and only non-empty text ever made it into the list.
    callback = max(candidates)[1] if candidates else ""
    if callback:
        print(f"callback: {callback[:90]}")

    # Phase 3 material. Macro first: rates and capex, with direction in words
    # for the same reason the metric context states its own direction.
    # Two series, not five. Phase 3 is meant to connect one macro condition to
    # this account, and five series invites a paragraph of unexplained readings.
    # Rank by how much each actually moved, relative to its own level.
    macro_rows = [m.asDict() for m in spark.sql(f"""
        SELECT series_id, series_name, latest_value, latest_date, change_90d, direction_90d
        FROM {catalog}.{schema}.silver_macro_context
    """).collect()]
    def moved(m):
        if not m.get("change_90d") or not m.get("latest_value"):
            return 0.0
        return abs(m["change_90d"] / m["latest_value"])
    macro_rows = sorted(macro_rows, key=moved, reverse=True)[:2]

    macro_lines = []
    for m in macro_rows:
        d = m["direction_90d"]
        sid = m["series_id"]
        move = ("has not moved" if d == "FLAT" else
                f"is {d.lower()} - {macro_change(sid, m['change_90d'])} "
                f"{'higher' if d == 'RISING' else 'lower'} than three months ago")
        macro_lines.append(
            f"- {m['series_name']}: {macro_value(sid, m['latest_value'])} "
            f"as of {m['latest_date']}, and {move}."
        )

    # Then industry context. These are not account-specific, so they are capped
    # at recent items rather than everything the feeds have ever published.
    # A retrieved item is either an ARTICLE or a HEADLINE. 410 of 430 news
    # chunks are 300 characters or fewer - a headline plus a truncated teaser -
    # because the RSS feeds carry summaries, not article bodies. A stub cannot
    # support a claim beyond the words it contains, and every fabricated figure
    # found in the audit sat on one: a 325-character Yahoo teaser became "a
    # $1,000 stake at the 1999 IPO would be worth over $200,000", with both
    # numbers invented and both credited to the article.
    #
    # So the model is told which is which. Labelled with a KIND: field rather
    # than a bracket, because the model reads brackets aloud - the same reason
    # transcript chunks use SPEAKER:/SECTION:/SAID: instead of [Speaker, section].
    STUB_CHARS = 300

    def source_line(row, limit):
        text = (row["chunk_text"] or "").strip()
        kind = "HEADLINE ONLY" if len(text) <= STUB_CHARS else "ARTICLE"
        return (f"- KIND: {kind} | PUBLICATION: {row['publisher'] or 'unattributed'}"
                f" | HEADLINE: {row['headline']} | DATE: {row['published_at']}"
                f" | TEXT: {text[:limit]}")

    # For a requested deep dive, pull what is ABOUT the subject rather than
    # whatever happens to be recent. Keyword matching is crude - this is the
    # job Vector Search exists for, and the endpoint is already provisioned -
    # but "most recent 10" gave a CoWoS episode whatever McKinsey published
    # last week, which is worse than crude.
    topic_filter = ""
    if requested_topic:
        words = [w.lower().strip(".,()") for w in requested_topic.split()
                 if len(w) > 4][:8]
        if words:
            likes = " OR ".join(
                f"lower(chunk_text) LIKE '%{w}%'" for w in words)
            topic_filter = f"AND ({likes})"

    # Only pull industry material when it is actually about something. Without a
    # subject to match against, "most recent industry items" put a McKinsey piece
    # on Moderna into an NVIDIA episode - and because it was passed to the prompt,
    # it then counted as a source the episode used.
    if requested_topic:
        trend_rows = spark.sql(f"""
            SELECT publisher, headline, url, chunk_text, published_at
            FROM {catalog}.{schema}.silver_doc_chunks
            WHERE source_type IN ('industry_trend', 'news')
              -- SCOPED TO THIS ACCOUNT. Industry chunks carry the sentinel
              -- account_id '_industry' because they describe a sector rather
              -- than a company; news carries a real ticker. Without this
              -- clause the 'news' half of the IN swept up every OTHER
              -- account's articles: one Micron episode recorded 28 sources of
              -- which 10 belonged to NVDA or GOOG - Venezuelan oil, a
              -- congressman selling Alphabet stock - and /api/sources showed
              -- them to the rep as the sources Micron's episode used.
              AND account_id IN ('_industry', '{account}')
              AND published_at >= date_sub(current_date(), 120)
              {topic_filter}
            ORDER BY published_at DESC LIMIT 14
        """).collect()
        if not trend_rows:
            print("mode C: nothing in the sources matches the requested subject")
    else:
        trend_rows = []
        print("no requested subject: industry trends left out rather than "
              "padding the episode with unrelated coverage")
    if requested_topic and not trend_rows:
        print("mode C: nothing in the sources matches the requested subject")
    # Publication and headline travel with every item so the script can say
    # where a claim came from instead of "a recent report".
    trend_lines = [source_line(t, 340) for t in trend_rows]

    # News is account-specific and is the SUBJECT of a Mode B episode, not
    # background. It was never passed before, so Mode B fired on news signals
    # and then produced an earnings recap labelled "Today's news".
    news_rows = spark.sql(f"""
        SELECT publisher, headline, url, chunk_text, published_at
        FROM {catalog}.{schema}.silver_doc_chunks
        WHERE source_type = 'news' AND account_id = '{account}'
          AND published_at >= date_sub(current_date(), 10)
        ORDER BY published_at DESC LIMIT 18
    """).collect()
    news_lines = [source_line(n, 260) for n in news_rows]
    stubs = sum(1 for n in news_rows
                if len((n["chunk_text"] or "").strip()) <= STUB_CHARS)
    print(f"news: {len(news_lines)} items, {stubs} of them headline-only")

    macro_block = ""
    if macro_lines or trend_lines:
        macro_block = (
            "Interest rates, capital spending and inflation:\n"
            + "\n".join(macro_lines)
            + ("\n\nRecent industry coverage. These are about the industry, not "
               "this account specifically, so use them only where they genuinely "
               "bear on it:\n" + "\n".join(trend_lines) if trend_lines else "")
        )
    print(f"macro: {len(macro_lines)} series, {len(trend_lines)} trend items")

    prompt = prompts.build(
        mode=mode,
        requested_topic=requested_topic,
        news="\n".join(news_lines),
        account=account,
        deltas="\n".join(delta_lines),
        context="\n".join(context_lines),
        macro=macro_block,
        framing=framing,
        callback=callback,
        derived_note=derived_note,
    )
    print(f"prompt: {len(prompt):,} chars | {len(deltas)} metrics | {len(chunks)} chunks")

    resp = w.serving_endpoints.query(
        name=endpoint,
        # The SDK wants typed ChatMessage objects; plain dicts fail with
        # "'dict' object has no attribute 'as_dict'".
        messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
        max_tokens=4000,
        temperature=0.4,
    )
    # gpt-oss models return content as a list of parts (reasoning plus text)
    # rather than a plain string, so pull the text out either way.
    raw = resp.choices[0].message.content
    script = extract_text(raw)
    if not script.strip():
        raise RuntimeError(f"empty script; raw content type {type(raw)}: {str(raw)[:300]}")
    # The prompt forbids anything silent when spoken. Check rather than trust:
    # the model dropped eight bullet points into the previous version.
    import re as _re
    offenders = {
        "markdown bullet": _re.compile(r"^\s*[-*\u2022]\s+", _re.M),
        "numbered list": _re.compile(r"^\s*\d+[.)]\s+", _re.M),
        "heading": _re.compile(r"^\s*#{1,6}\s+", _re.M),
        "bold or italic": _re.compile(r"\*\*|__"),
        "leaked speaker tag": _re.compile(r"\[[^\]]{0,40}(prepared_remarks|qa)\]"),
        "leaked metadata label": _re.compile(r"^\s*(SPEAKER|SECTION|SAID):", _re.M),
        # Any bracketed aside is a stage direction, and the voice engine reads
        # it out. Adding named phases to the prompt made the model start
        # labelling its own sections: "[Core analysis - first metric]".
        "bracketed stage direction": _re.compile(r"\[[^\]]{2,80}\]"),
    }
    found = {k: len(rx.findall(script)) for k, rx in offenders.items() if rx.search(script)}
    if found:
        print(f"FORMAT WARNING - script contains print-only formatting: {found}")

    # Reciting numbers is the failure this briefing keeps regressing into, so
    # count them rather than trusting the instruction. A sentence carrying four
    # or more figures is a table being read aloud.
    num = _re.compile(r"\$?\d[\d,.]*\s?(?:percent|billion|million|basis points)?")
    dense = [sent.strip() for sent in _re.split(r"(?<=[.!?])\s+", script)
             if len(num.findall(sent)) >= 4]
    if dense:
        print(f"DENSITY WARNING - {len(dense)} sentence(s) carry 4+ figures:")
        for sent in dense[:3]:
            print(f"    {sent[:110]}")

    # The dump shows up per paragraph, not per sentence: a run of short
    # sentences with two figures each slips a per-sentence check entirely.
    for i, para in enumerate([p for p in script.split("\n") if p.strip()], 1):
        n = len(num.findall(para))
        if n >= 8:
            print(f"DENSITY WARNING - paragraph {i} carries {n} figures: {para[:110]}")

    # GROUNDING GUARD. The rule "never state a fact you were not given" is the
    # one the whole project rests on, so it is checked rather than trusted -
    # same reasoning as the format guard above. Every figure spoken must appear
    # somewhere in the material the model was handed; anything else was
    # invented, however plausible it sounds. Real cases this catches: "$200,000"
    # and "1999" attributed to a 325-character teaser containing neither, and
    # "81 cents on the dollar" derived from a growth-rate gap when the actual
    # cost ratio was 25 cents.
    # Rounding is expected: the prompt asks for one decimal place, so 74.98 may
    # legitimately be spoken as 75. Outside 1% - or 0.05 for small values - the
    # number was not handed over.
    supplied = figures(prompt)
    unsupported = sorted(
        v for v in figures(script)
        if not any(abs(v - t) <= max(0.05, abs(v) * 0.01) for t in supplied))
    if unsupported:
        print(f"GROUNDING WARNING - {len(unsupported)} figure(s) appear in no "
              f"supplied source: "
              + ", ".join(f"{v:g}" for v in unsupported[:12]))

    # Spelled-out figures escape the digit scan entirely - the original
    # "ninety-nine gigawatts" fabrication survived every numeric check because
    # it contains no digits. Reported for a human to read rather than judged,
    # since most are legitimate prose.
    spelled = [m.group(0) for m in _re.finditer(
        r"\b(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|one|two|"
        r"three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
        r"[ -](?:hundred|thousand|million|billion|trillion|percent|gigawatt)\w*",
        script, _re.I)]
    if spelled:
        print(f"SPELLED-OUT FIGURES for review ({len(spelled)}): "
              + ", ".join(sorted({x.lower() for x in spelled})[:10]))

    words = len(script.split())
    print(f"script: {words:,} words, ~{words / 150:.1f} minutes read aloud")

    # Append, never overwrite. Prompt iteration is the one workflow that most
    # needs old versions side by side, and an early build of this table used
    # overwrite - four earlier scripts survived only in Delta time travel.
    # Keeping every generation makes comparing prompt changes a plain query.
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    briefing_id = f"{account}-{period}-{now:%Y%m%dT%H%M%S}"

    # Provenance for this specific episode: what each layer contributed, and how
    # stale it was. Captured here because the job is the only thing that knows
    # what it actually read - reconstructing it later would be guesswork.
    def scalar(sql, default=None):
        try:
            return spark.sql(sql).collect()[0][0]
        except Exception:
            return default

    # Counts are SCOPED TO THIS ACCOUNT wherever the source has an account.
    # They were table-wide, so a GOOG episode's provenance panel reported
    # "6,828 analyst rows, last ingested today" when zero of them were GOOG -
    # FMP's free tier is exhausted by NVDA's backfill before GOOG is reached,
    # so that account has no ratings at all. The one panel whose entire job is
    # to be honest about sources was quoting another company's numbers.
    #
    # Industry trends and macro have no account: they describe a sector and a
    # world. That is stated rather than left to look like an omission.
    def bronze_row(label, table, scoped=True):
        where = f" WHERE symbol = '{account}'" if scoped else ""
        return {
            "source": label,
            "table": table,
            "scope": account if scoped else "all accounts (not account-specific)",
            "rows": scalar(
                f"SELECT count(*) FROM {catalog}.{schema}.{table}{where}", 0),
            "last_ingested": str(scalar(
                f"SELECT max(_ingested_at) FROM {catalog}.{schema}.{table}{where}")),
        }

    lineage = {
        "bronze": [
            bronze_row("SEC EDGAR XBRL", "bronze_xbrl_facts"),
            bronze_row("Earnings call (Roic AI)", "bronze_transcript_turns"),
            bronze_row("News (Google, Yahoo)", "bronze_news"),
            bronze_row("Analyst grades (FMP)", "bronze_analyst_ratings"),
            bronze_row("Industry trends (RSS)", "bronze_industry_trends",
                       scoped=False),
            bronze_row("Macro (FRED)", "bronze_macro", scoped=False),
        ],
        # The actual items, not just counts. This is the same JSON already
        # travelling with the episode, so showing the detail costs nothing
        # architecturally - no extra query path, no new table, and it stays
        # correct because it is written by the run that used it.
        "silver": [
            {"table": "silver_financial_deltas", "used": len(delta_lines),
             "note": "metrics selected for this episode",
             "items": [d.lstrip("- ") for d in delta_lines]},
            {"table": "silver_metric_context", "used": len(context_lines),
             "note": "relationships computed in SQL",
             "items": [c.lstrip("- ") for c in context_lines]},
            {"table": "silver_doc_chunks (transcript)", "used": len(chunks),
             "note": "management framing",
             "items": [f"{c['speaker']} ({c['section']}): "
                       f"{(c['chunk_text'] or '')[:150]}" for c in chunks[:12]]},
            {"table": "silver_doc_chunks (news)", "used": len(news_lines),
             "note": "headlines",
             "items": [f"{n['publisher']}: {n['headline']}" for n in news_rows[:14]]},
            {"table": "silver_doc_chunks (trends)", "used": len(trend_lines),
             "note": "industry context",
             "items": [f"{t['publisher']}: {t['headline']}" for t in trend_rows[:14]]},
            {"table": "silver_macro_context", "used": len(macro_lines),
             "note": "macro series",
             "items": [m.lstrip("- ") for m in macro_lines]},
        ],
        # The exact items that went into this prompt. Querying for "recent
        # sources" afterwards surfaced whatever the feeds happened to carry -
        # a McKinsey piece about Moderna alongside an NVIDIA episode - because
        # nothing tied the list to what was actually used.
        "sources": [
            {"publisher": n["publisher"], "headline": n["headline"],
             "url": n["url"], "kind": "news",
             "published_at": str(n["published_at"])}
            for n in news_rows if n["url"]
        ] + [
            {"publisher": t["publisher"], "headline": t["headline"],
             "url": t["url"], "kind": "industry",
             "published_at": str(t["published_at"])}
            for t in trend_rows if t["url"]
        ],
        "gold": {"mode": mode, "mode_reason": mode_reason,
                 "prompt_chars": len(prompt), "model": endpoint},
    }

    # Title and takeaways in one call, kept separate from the script. Folded
    # into the script prompt, the model wrote the title as the opening line and
    # the narrator read it aloud.
    meta_resp = w.serving_endpoints.query(
        name=endpoint,
        messages=[ChatMessage(role=ChatMessageRole.USER,
                              content=prompts.EPISODE_META_PROMPT.format(
                                  account=account, script=script))],
        # Reasoning model: the budget covers thinking plus the JSON. At 60 the
        # whole budget went to reasoning and the answer came back empty.
        max_tokens=1200, temperature=0.6,
    )
    meta_text = extract_text(meta_resp.choices[0].message.content).strip()
    if "```" in meta_text:
        meta_text = meta_text.split("```")[1].removeprefix("json").strip()
    episode_title, takeaways = "", []
    try:
        meta = json.loads(meta_text[meta_text.index("{"):meta_text.rindex("}") + 1])
        episode_title = str(meta.get("title") or "").strip().strip('"')[:120]
        takeaways = [str(t).strip() for t in (meta.get("takeaways") or []) if str(t).strip()]
    except Exception as e:
        print(f"  WARNING could not parse episode metadata ({e})")
    if not episode_title:
        episode_title = f"{account} briefing"     # never leave the card blank
    mode_label = prompts.MODE_LABELS.get(mode, mode)

    # Three comprehension questions, generated with the episode and stored on it.
    # Grading three targeted answers is fairer than grading a free recap, and it
    # is fast enough to run the moment the rep finishes speaking.
    q_resp = w.serving_endpoints.query(
        name=endpoint,
        messages=[ChatMessage(role=ChatMessageRole.USER,
                              content=prompts.QUESTIONS_PROMPT.format(
                                  account=account, script=script))],
        max_tokens=1600, temperature=0.4,
    )
    q_text = extract_text(q_resp.choices[0].message.content).strip()
    if "```" in q_text:
        q_text = q_text.split("```")[1].removeprefix("json").strip()
    questions = []
    try:
        questions = json.loads(
            q_text[q_text.index("{"):q_text.rindex("}") + 1]).get("questions", [])[:3]
    except Exception as e:
        print(f"  WARNING could not parse questions ({e})")
    for i, q in enumerate(questions, 1):
        print(f"  Q{i}: {str(q.get('question',''))[:88]}")
    print(f"title: {episode_title!r}")
    for t in takeaways:
        print(f"  takeaway: {t[:95]}")

    spark.createDataFrame(
        [(briefing_id, account, str(period), now, mode, mode_reason,
          episode_title, mode_label, json.dumps(takeaways),
          json.dumps(questions), json.dumps(lineage), script, words,
          endpoint, json.dumps([r["metric"] for r in deltas]), len(chunks), len(prompt))],
        "briefing_id string, account_id string, period_end string, "
        "generated_at timestamp, mode string, mode_reason string, "
        "episode_title string, mode_label string, takeaways string, questions string, "
        "lineage string, "
        "script_text string, word_count int, "
        "model string, metrics string, chunk_count int, prompt_chars int",
    ).write.mode("append").saveAsTable(f"{catalog}.{schema}.gold_briefing")

    # Readers want the newest script per account and should not have to know
    # that older generations sit underneath it.
    spark.sql(f"""
        CREATE OR REPLACE VIEW {catalog}.{schema}.gold_briefing_current AS
        SELECT * FROM (
            SELECT *, row_number() OVER (
                PARTITION BY account_id ORDER BY generated_at DESC) AS _r
            FROM {catalog}.{schema}.gold_briefing
        ) WHERE _r = 1
    """)
    print(f"appended {briefing_id} to {catalog}.{schema}.gold_briefing")


if __name__ == "__main__":
    main()
