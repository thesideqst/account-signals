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
import sys



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


def main() -> None:
    import prompts
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
    from pyspark.sql import SparkSession

    catalog, schema, endpoint = sys.argv[1], sys.argv[2], sys.argv[3]
    spark = SparkSession.builder.getOrCreate()
    w = WorkspaceClient()

    account = "NVDA"

    # Latest quarter with an earnings call attached.
    period = spark.sql(f"""
        SELECT max(period_end) AS p
        FROM {catalog}.{schema}.silver_financial_deltas WHERE symbol = '{account}'
    """).collect()[0]["p"]

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

    # Mode is needed before retrieval, because what gets retrieved depends on it.
    _sig = spark.sql(f"""
        SELECT mode FROM {catalog}.{schema}.silver_daily_signals
        WHERE symbol = '{account}' AND signal_date = (
            SELECT max(signal_date) FROM {catalog}.{schema}.silver_daily_signals
            WHERE symbol = '{account}')
    """).collect()
    mode = _sig[0]["mode"] if _sig else "C"

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
    if ctx:
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
        mode, mode_reason = sig[0]["mode"], sig[0]["mode_reason"] or "signals present"
    else:
        mode, mode_reason = "C", "no signals on record for this account"
    print(f"mode {mode} - {mode_reason}")

    # A graded recap from the recall loop becomes the cold-open callback.
    # The table does not exist until the loop runs, so absence is normal.
    callback = ""
    try:
        g = spark.sql(f"""
            SELECT gaps FROM {catalog}.{schema}.gold_recall_grades
            WHERE account_id = '{account}' ORDER BY graded_at DESC LIMIT 1
        """).collect()
        if g:
            # Pass the single most important gap, phrased as a sentence. Handing
            # over the whole JSON list produced a cold open that recited eight
            # items and scolded three times.
            parsed = json.loads(g[0]["gaps"] or "[]")
            rank = {"high": 0, "medium": 1, "low": 2}
            parsed.sort(key=lambda x: rank.get(str(x.get("importance")).lower(), 3))
            if parsed:
                callback = str(parsed[0].get("point", "")).strip()
    except Exception:
        pass

    # Phase 3 material. Macro first: rates and capex, with direction in words
    # for the same reason the metric context states its own direction.
    # Two series, not five. Phase 3 is meant to connect one macro condition to
    # this account, and five series invites a paragraph of unexplained readings.
    # Rank by how much each actually moved, relative to its own level.
    macro_rows = [m.asDict() for m in spark.sql(f"""
        SELECT series_name, latest_value, latest_date, change_90d, direction_90d
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
        move = ("has not moved" if d == "FLAT" else
                f"is {d.lower()} - {abs(m['change_90d']):.2f} "
                f"{'higher' if d == 'RISING' else 'lower'} than three months ago")
        macro_lines.append(
            f"- {m['series_name']}: {m['latest_value']:.2f} as of {m['latest_date']}, "
            f"and {move}."
        )

    # Then industry context. These are not account-specific, so they are capped
    # at recent items rather than everything the feeds have ever published.
    trend_rows = spark.sql(f"""
        SELECT publisher, headline, chunk_text, published_at
        FROM {catalog}.{schema}.silver_doc_chunks
        WHERE source_type = 'industry_trend'
          AND published_at >= date_sub(current_date(), 45)
        ORDER BY published_at DESC LIMIT 10
    """).collect()
    # Publication and headline travel with every item so the script can say
    # where a claim came from instead of "a recent report".
    trend_lines = [
        f"- {t['publisher']}, \"{t['headline']}\" ({t['published_at']}): "
        f"{(t['chunk_text'] or '')[:340]}"
        for t in trend_rows
    ]

    # News is account-specific and is the SUBJECT of a Mode B episode, not
    # background. It was never passed before, so Mode B fired on news signals
    # and then produced an earnings recap labelled "Today's news".
    news_rows = spark.sql(f"""
        SELECT publisher, headline, chunk_text, published_at
        FROM {catalog}.{schema}.silver_doc_chunks
        WHERE source_type = 'news' AND account_id = '{account}'
          AND published_at >= date_sub(current_date(), 10)
        ORDER BY published_at DESC LIMIT 18
    """).collect()
    news_lines = [
        f"- {n['publisher'] or 'unattributed'}, \"{n['headline']}\" "
        f"({n['published_at']}): {(n['chunk_text'] or '')[:260]}"
        for n in news_rows
    ]
    print(f"news: {len(news_lines)} items")

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

    # On a quiet day the rep's queue picks the subject. Without this Mode C has
    # nothing to deep-dive on and the model chooses from its own knowledge,
    # which is the one thing the grounding rule exists to prevent.
    requested_topic = ""
    if mode == "C":
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

    lineage = {
        "bronze": [
            {"source": "SEC EDGAR XBRL", "table": "bronze_xbrl_facts",
             "rows": scalar(f"SELECT count(*) FROM {catalog}.{schema}.bronze_xbrl_facts", 0),
             "last_ingested": str(scalar(f"SELECT max(_ingested_at) FROM {catalog}.{schema}.bronze_xbrl_facts"))},
            {"source": "Earnings call (Roic AI)", "table": "bronze_transcript_turns",
             "rows": scalar(f"SELECT count(*) FROM {catalog}.{schema}.bronze_transcript_turns", 0),
             "last_ingested": str(scalar(f"SELECT max(_ingested_at) FROM {catalog}.{schema}.bronze_transcript_turns"))},
            {"source": "News (Google, Yahoo)", "table": "bronze_news",
             "rows": scalar(f"SELECT count(*) FROM {catalog}.{schema}.bronze_news", 0),
             "last_ingested": str(scalar(f"SELECT max(_ingested_at) FROM {catalog}.{schema}.bronze_news"))},
            {"source": "Analyst grades (FMP)", "table": "bronze_analyst_ratings",
             "rows": scalar(f"SELECT count(*) FROM {catalog}.{schema}.bronze_analyst_ratings", 0),
             "last_ingested": str(scalar(f"SELECT max(_ingested_at) FROM {catalog}.{schema}.bronze_analyst_ratings"))},
            {"source": "Industry trends (RSS)", "table": "bronze_industry_trends",
             "rows": scalar(f"SELECT count(*) FROM {catalog}.{schema}.bronze_industry_trends", 0),
             "last_ingested": str(scalar(f"SELECT max(_ingested_at) FROM {catalog}.{schema}.bronze_industry_trends"))},
            {"source": "Macro (FRED)", "table": "bronze_macro",
             "rows": scalar(f"SELECT count(*) FROM {catalog}.{schema}.bronze_macro", 0),
             "last_ingested": str(scalar(f"SELECT max(_ingested_at) FROM {catalog}.{schema}.bronze_macro"))},
        ],
        "silver": [
            {"table": "silver_financial_deltas", "used": len(delta_lines),
             "note": "metrics selected for this episode"},
            {"table": "silver_metric_context", "used": len(context_lines),
             "note": "computed relationships"},
            {"table": "silver_doc_chunks (transcript)", "used": len(chunks),
             "note": "management framing"},
            {"table": "silver_doc_chunks (news)", "used": len(news_lines),
             "note": "headlines"},
            {"table": "silver_doc_chunks (trends)", "used": len(trend_lines),
             "note": "industry context"},
            {"table": "silver_macro_context", "used": len(macro_lines),
             "note": "macro series"},
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
