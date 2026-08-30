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

    delta_lines = []
    for r in deltas:
        f = lambda x: "not available" if x is None else f"{x * 100:+.1f}%"
        note = (" (DERIVED: the company did not file this figure; it was computed "
                "as the annual total minus the three reported quarters)"
                if r["is_derived"] else " (as filed)")
        delta_lines.append(
            f"- {r['metric']}: ${r['value'] / 1e9:,.3f} billion{note}. "
            f"Quarter over quarter {f(r['qoq_pct'])} (vs {r['prev_q_end']}). "
            f"Year over year {f(r['yoy_pct'])} (vs {r['prev_y_end']})."
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
            callback = str(g[0]["gaps"])
    except Exception:
        pass

    # Phase 3 material. Macro first: rates and capex, with direction in words
    # for the same reason the metric context states its own direction.
    macro_lines = []
    for m in spark.sql(f"""
        SELECT series_name, latest_value, latest_date, change_90d, direction_90d
        FROM {catalog}.{schema}.silver_macro_context ORDER BY series_id
    """).collect():
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
        SELECT chunk_text, published_at
        FROM {catalog}.{schema}.silver_doc_chunks
        WHERE source_type = 'industry_trend'
          AND published_at >= date_sub(current_date(), 45)
        ORDER BY published_at DESC LIMIT 14
    """).collect()
    trend_lines = [f"- ({t['published_at']}) {t['chunk_text'][:400]}" for t in trend_rows]

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

    words = len(script.split())
    print(f"script: {words:,} words, ~{words / 150:.1f} minutes read aloud")

    # Append, never overwrite. Prompt iteration is the one workflow that most
    # needs old versions side by side, and an early build of this table used
    # overwrite - four earlier scripts survived only in Delta time travel.
    # Keeping every generation makes comparing prompt changes a plain query.
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    briefing_id = f"{account}-{period}-{now:%Y%m%dT%H%M%S}"

    # Title as a second, cheap call rather than asking for it inside the script.
    # Folded in, the model kept writing the title as the opening line and the
    # narrator read it aloud.
    title_resp = w.serving_endpoints.query(
        name=endpoint,
        messages=[ChatMessage(role=ChatMessageRole.USER,
                              content=prompts.TITLE_PROMPT.format(
                                  account=account, script=script))],
        # This is a reasoning model: it spends tokens thinking before it writes.
        # At max_tokens=60 the whole budget went to reasoning and the title came
        # back empty, so the ceiling has to cover both.
        max_tokens=800, temperature=0.7,
    )
    title_text = extract_text(title_resp.choices[0].message.content)
    # Take the last non-empty line: any preamble comes before the answer.
    lines = [ln.strip().strip('"').strip("'")
             for ln in title_text.splitlines() if ln.strip()]
    episode_title = (lines[-1][:120] if lines else "")
    if not episode_title:
        episode_title = f"{account} briefing"   # never leave the card blank
    mode_label = prompts.MODE_LABELS.get(mode, mode)
    print(f"title: {episode_title!r}")

    spark.createDataFrame(
        [(briefing_id, account, str(period), now, mode, mode_reason,
          episode_title, mode_label, script, words,
          endpoint, json.dumps([r["metric"] for r in deltas]), len(chunks), len(prompt))],
        "briefing_id string, account_id string, period_end string, "
        "generated_at timestamp, mode string, mode_reason string, "
        "episode_title string, mode_label string, script_text string, word_count int, "
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
