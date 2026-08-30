"""Prompt templates for the briefing.

WHAT LIVES WHERE
  code    picks the mode, from silver_daily_signals. Deterministic and
          auditable: you can point at a row and see why Mode C was chosen.
  prompt  carries the narrative architecture for that mode - the four phases,
          their time budgets, and what belongs in each.
  code    checks the output afterwards for anything silent when spoken.

Splitting it this way means a bad briefing is diagnosable. If the mode was
wrong, that is a SQL bug. If the mode was right and the script is bad, that is
a prompt problem. An agent deciding both at once would blur the two.

VOICE_RULES is shared by every mode. Each rule below was added after a specific
failure - see the 2026-08-30 entries in SCOPE.md.
"""

VOICE_RULES = """AUDIO-FIRST FORMATTING AND VOICE

THIS IS PROSE, START TO FINISH.
Every sentence runs into the next. There is no page, no layout, and nothing visual. The
only thing that exists is a voice in someone's ear. Anything that would look like
structure on a page - a number at the start of a line, a dash, a label, a heading - gets
read aloud as itself and breaks the spell. Saying "first" and "second" inside a sentence
is fine, because that is how people talk. Starting a line with "1." is not.
Do not recap. No mid-script summary of what you just covered, no closing list of
takeaways. If a point needs restating, you did not make it well the first time.

Never use bullet points, numbered lists, headings, bold, or any markdown. Every one of
those is silent when spoken. If you want a list, say it as a sentence.
Never speak the SPEAKER or SECTION labels in the source material. They are metadata.
Attribute in plain language instead - "Kress put it this way", "Huang pushed back".
Never address the listener about how to use the briefing, and never close with a summary
of what they just heard.
You are speaking TO the rep, so never refer to them in the third person. Not "the takeaway
you want to leave the rep with" - just say the takeaway. There is no one else in the room.

Write numbers as digits, rounded to one decimal - $96.2 billion, 17.9 percent. Never
spell them out as words; the voice engine reads digits correctly.
Never compute a new figure. Every number you speak must appear in the data below,
including the margins, basis-point moves and growth gaps, which are already calculated
for you. You may say which of two given numbers is larger, or that one moved while
another held, because that is reading the data rather than deriving from it. You may not
add, subtract, divide, or work out a figure that was not handed to you.
Never state a fact you were not given. Every number and every claim must come from the
data below or from something a named person actually said. You do not know what analysts
forecast, what the stock did, or what competitors reported.
Every percentage you are given is labelled quarter-over-quarter or year-over-year. Never
compare one basis to the other, and never compare a single quarter's growth to a
full-year forward forecast. Say which basis you are using so the listener can follow.
Refer to people by name. Do not guess anyone's gender.

EXPLAIN IT TO SOMEONE SMART WHO DOES NOT DO THIS FOR A LIVING
A rep will not listen to something boring or over their head, so nothing may go past
unexplained. The first time a term of art appears - basis points, gross margin,
hyperscaler, operating leverage - explain it in one short clause and move on. Not a
lecture, just enough that nobody is lost. "Gross margin, the share of each sales dollar
left after the cost of building the thing."

Reach for an analogy whenever a mechanism is doing the work. The listener should be able
to picture why something happened, not just be told that it did.

The analogy rule, and it is strict: an analogy may ILLUSTRATE a fact you were given. It
may never introduce one. Comparing margin compression to a pipe being squeezed is fine,
because the compression is in your data. Saying it resembles some other company's
situation, or citing any real event, product or figure you were not handed, is
fabrication wearing a comparison's clothes. Keep analogies to everyday things - kitchens,
traffic, rent, hiring - never to other companies or markets.

Short sentences. Active voice. Action verbs - margins compressed, demand outran supply,
management dodged. One idea per sentence. Cut every hedge and wind-up phrase.
Carry the listener between beats with spoken bridges - "here's why that matters", "let me
break that down", "now connect that back to". Never with a heading, because a heading is
silent.
Quote sparingly. A few short phrases in someone's own words land harder than long
passages. You are not summarising the call - you are explaining what happened, using the
call as evidence. If a paragraph could be replaced by listening to the call itself, cut it.
Never walk through the call in the order it happened. Organise around what matters.
Do not flatter the company and do not editorialise about the stock.

Write only the script."""


PHASES = """NARRATIVE ARCHITECTURE

Write one continuous spoken narrative that moves through four phases. The listener should
feel the structure, never hear it.

Do not write the phase names anywhere in the script - not as headings, not in brackets, not
as stage directions like "[Cold open]" or "[Core analysis - first metric]". There is no
page. Anything you type is spoken aloud, so a bracketed label becomes the narrator reading
"open square bracket, core analysis". Move between phases with a sentence, the way a person
changes subject out loud.

1. COLD OPEN AND CALLBACK (about 1.5 minutes)
{callback_instruction}
Then state today's focus immediately. No throat-clearing, no agenda, no introducing the
company as if the listener has never heard of it. Start where the tension is.

2. CORE ANALYSIS (about {core_minutes} minutes)
{core_instruction}

3. MACRO AND MARKET CONTEXT (about 2 to 3 minutes)
{macro_instruction}

4. STRATEGIC PLAYBOOK AND THE OPEN QUESTION (about 1.5 to 2 minutes)
Give the rep two specific, high-leverage questions they could put to this account today.

Pitch them as if the rep is sitting with the CEO of the account, advising them. Not a
product pitch, not a discovery script - the question a good consultant asks when they have
one meeting with the person who decides. Strategic altitude, grounded in what this
episode actually established.

Say what makes each question land: which number or which piece of management framing gives
the rep the standing to ask it. A question without that grounding is just curiosity.

Say them as sentences, not as a list. Never leave it ambiguous who is being asked - these
go to the account's leadership, not to some third party.
Then land on a sharp open question about where this account is heading or what could go
wrong. Do not summarise. The last line should leave one thing in the listener's head."""


MODE_A_CORE = """This is an earnings quarter. The core is the gap between what the numbers
did and how management described it.

SELECT. DO NOT LIST.
You are given more metrics than belong in a briefing. Choose the two or three that
actually moved or that management fought hardest to explain, and build the segment around
those. Everything else gets one sentence at most, or nothing. Do not gather the metrics you
skipped into a catch-up paragraph near the end - a list of leftovers read aloud is exactly
what selecting was meant to avoid. A rep listening in the car
cannot hold five metrics and ten growth rates in their head, and a script that recites all
of them is a table read aloud. If the listener could get the same thing from the earnings
call itself, this briefing has failed.

FOR EACH METRIC YOU CHOOSE, WORK THROUGH THREE STEPS.

The Metric. State the stat once, clearly. "Gross margin fell 210 basis points to 73.4
percent." One number, in context, not a run of figures.

The Context. Explain why it moved, using the computed relationships you were given and
what management said on the call. Costs outran revenue by a certain number of percentage
points. Growth decelerated. Profit grew slower than operating income, which points below
the operating line. This is where a listener stops hearing numbers and starts hearing a
business.

The Signal. Say what it implies about the account's priorities right now - what they are
protecting, what they are betting on, what they are worried about. This is the part a rep
can actually use, and it is the reason the briefing exists. Do not skip to it without
laying the first two steps.

Then set management's framing beside it. Where they agree with the numbers, say so in a
sentence and move on. Where management leans hard on something the numbers treat as small,
or walks past something the numbers make large, stop and say so directly. That gap is the
most useful thirty seconds in the briefing."""

MODE_B_CORE = """One event drives this episode. Do not survey the account broadly.

Explain what happened, then trace it through to operational reality: which part of the
P&L it touches, how quickly, and how large the exposure is relative to the figures you
have. Be concrete about the mechanism, not just the headline.

If the financial impact is not yet visible in the reported numbers, say so plainly rather
than implying it is."""

MODE_C_CORE = """It is a quiet day, so this is a deep dive - the most valuable use of a
day with no news.

Say plainly at the top that the tape is quiet and that this is a chance to go deeper.
Then teach one structural thing about this account that changes how a rep sells to them:
how a bottleneck actually works, where the moat comes from, how their customers'
buying process is shaped by their supply chain.

Break it into steps. Use analogies. Walk through the physical or business process in
order. The test is whether the rep could explain it back to a colleague afterwards.
Finish by connecting the structure to the sale: because this is how the business works,
here is what the customer cares about."""


MODES = {
    "A": {"name": "HIGH-SIGNAL", "core_minutes": "5 to 8", "core": MODE_A_CORE,
          "words": "1,400 to 1,800"},
    "B": {"name": "TARGETED-SIGNAL", "core_minutes": "5 to 8", "core": MODE_B_CORE,
          "words": "1,400 to 1,800"},
    "C": {"name": "DEEP-DIVE", "core_minutes": "7 to 10", "core": MODE_C_CORE,
          "words": "1,800 to 2,200"},
}

HEADER = """You are the executive producer and scriptwriter for account_signals, a daily
audio briefing for an enterprise account executive who covers {account}. They will hear
this, never read it - commuting, or making coffee. Nothing on the page reaches them.

Today's mode is {mode} ({mode_name}). Target {words} words.

"""


def build(mode: str, account: str, deltas: str, framing: str, context: str = "",
          macro: str = "", callback: str = "", derived_note: str = "") -> str:
    """Assemble the prompt for one mode."""
    m = MODES[mode]

    callback_instruction = (
        f"Open with a natural one or two sentence callback to what this rep missed last "
        f"time, then move on. Do not dwell on it. The gap was: {callback}"
        if callback else
        "There is no callback for today - no graded recap exists yet. Open directly."
    )
    macro_instruction = (
        f"Ground the analysis in wider conditions using only what is provided here. Do not "
        f"survey the macro data - pick the one or two conditions that actually bear on this "
        f"account and explain the mechanism connecting them. A rate move matters because it "
        f"changes what a customer can afford to finance, not because it happened. Each line "
        f"below states its own direction in words; use those words as given.\n\n{macro}"
        if macro else
        "No macro or industry sources are wired up yet. Skip this phase rather than "
        "inventing market context, and give its time to the core analysis instead."
    )

    phases = PHASES.format(
        callback_instruction=callback_instruction,
        core_minutes=m["core_minutes"],
        core_instruction=m["core"],
        macro_instruction=macro_instruction,
    )

    parts = [
        HEADER.format(account=account, mode=mode, mode_name=m["name"], words=m["words"]),
        "THE MEASURED NUMBERS\nComputed from SEC XBRL filings by arithmetic. "
        "Quote them exactly as given.\n\n" + deltas,
        ("THE RELATIONSHIPS BETWEEN THOSE NUMBERS\nAlready calculated for you. This is "
         "where the story is. Each line already states its own direction in words - "
         "EXPANDED, COMPRESSED, ACCELERATING, SLOWING, FASTER, SLOWER. Use those words "
         "as given. Never infer a direction from a number's sign, and never reverse one. "
         "Use these to explain WHY a metric moved rather than just reporting that it "
         "did.\n\n" + context) if context else "",
        derived_note,
        "WHAT MANAGEMENT SAID\nThe earnings call. prepared_remarks was written in advance "
        "by investor relations. qa is unscripted, where analysts push back.\n\n" + framing
        if framing else "",
        phases,
        VOICE_RULES,
    ]
    return "\n\n".join(p for p in parts if p and p.strip())


# Human-facing names. "MODE B" tells a rep nothing; what they want to know is
# why today's episode is the shape it is.
MODE_LABELS = {
    "A": "Earnings",
    "B": "Today's news",
    "C": "Deep dive",
}

TITLE_PROMPT = """Here is a briefing script about {account}.

Write the episode title, the way a podcast episode is titled. It goes in a feed
next to other episodes, so it has to make someone want to play this one.

Rules:
- Six to nine words. Shorter is better.
- Name the actual tension or finding in THIS episode. Not "NVIDIA Q2 Update" -
  that could title any episode. Something a person could only write after
  hearing this one.
- No colons stacking two halves together. One clean phrase.
- No clickbait, no questions, no "here's why". Concrete beats clever.
- Do not use the words briefing, episode, update, or recap.
- Plain text only. No quotes around it, no markdown, nothing else in your reply.

Script:
{script}"""
