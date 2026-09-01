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

NEVER NARRATE YOUR OWN STRUCTURE. No "that's the hook", no "let's dive in", no "the focus
today is", no "first up", no "to wrap up". A listener can hear where they are. Announcing
it wastes their attention and sounds like a template.

ONE HEADLINE FIGURE PER PARAGRAPH. THIS IS THE RULE THAT MATTERS MOST.
A paragraph carries one number the listener is meant to remember, then the explanation,
then what it means. Not the metric plus both its growth rates plus a margin plus a
basis-point move - one figure, then prose.

At most two figures in any sentence, and at most three in a paragraph including the
headline one. If you find yourself writing "up X percent quarter-over-quarter and Y
percent year-over-year" you have already spent the paragraph's budget on a single clause.
Pick the one that carries the point and drop the other.

Numbers you were given but did not say are not wasted. They were context for choosing
what to say. A briefing that uses six of the twenty figures it was handed is working
correctly; one that uses all twenty is a table read aloud, which is the single thing this
briefing exists not to be.

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
SAY WHERE ANYTHING OUTSIDE THE FILINGS CAME FROM.
Every news item and industry piece below carries its publication and its headline. When
you use one, name them - "Reuters reported this week that...", "IEEE Spectrum ran a piece
called Inside the Memory Crunch which argues...". Never "a recent report", "one analysis",
or "industry observers say": an unattributed claim is one the listener cannot check, and
in a briefing built on traceable sources that is the one thing that cannot be sloppy.
Figures from the filings need no attribution; they are the company's own numbers.

Each news and industry item is marked KIND: ARTICLE or KIND: HEADLINE ONLY. That
distinction is not decoration and it binds you.
A HEADLINE ONLY item is a headline and at most a truncated teaser. You may report that
the publication ran it, and you may quote its words. You may NOT say what the article
argues, concludes, reports, warns or reveals beyond the words printed in front of you,
and you may not supply a figure, a date, a cause or a consequence that the text does not
contain. If a headline names a number without saying what it measures, you do not know
what it measures - say the headline named it and stop. Two failures came from ignoring
this: a teaser about an IPO investment became an invented 1999 date and an invented
$200,000 figure, and a headline containing "15 gigawatts" became an invented global
ceiling on AI power with a two-step consequence chain built on top of it.
A KIND: ARTICLE item carries real body text, so you may draw on what it actually says.
Never present a number computed from the filings as something a news article found, and
never turn one into a quotation from a named person. The measured numbers are the
company's own; attributing them to a journalist or an executive who did not say them is
a fabrication even when the figure is right.

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

MODE_B_CORE = """This is a news day, not an earnings day. The headlines below are the
subject of this episode. The financial figures are background - reach for one only where
it sizes something a headline claims, and never open with them.

Pick the one or two developments that would actually change a customer conversation this
week. Ignore the rest; most of a news feed is noise. Ranking them is the work.

For each one: what happened, then the mechanism - which part of the business it touches,
how quickly, and how big it is next to the figures you have. Be concrete about how it
works, not just that it occurred.

If the financial impact is not yet visible in the reported numbers, say so plainly rather
than implying it is. A headline is not a result."""

MODE_C_CORE = """This is a deep dive. It is not an earnings episode and it is not a news
episode. Nobody is asking what the quarter did.

TEACH A MECHANISM. The whole episode explains ONE thing about how this business works -
how a bottleneck actually functions, where a moat comes from, why a customer's buying
process is shaped the way it is. The test is whether the rep could explain it back to a
colleague afterwards without notes.

Walk the process in order, step by step, the way you would draw it on a whiteboard. What
goes in, what happens to it, what comes out, and where it jams. Use analogies to everyday
things throughout - a kitchen, a motorway, a queue at a counter. The analogy is not
decoration here; it is how the idea gets across.

NUMBERS ARE NEARLY IRRELEVANT TO THIS EPISODE. You have been given revenue only, for a
sense of scale, and you should use it perhaps once. If you find yourself reciting growth
rates or margins you have drifted back into an earnings recap, which is exactly what this
episode is not. A listener who wanted the numbers would play a different episode.

If the sources genuinely do not explain the mechanism, say so plainly in one sentence and
teach the closest thing they do cover. Never fill the gap from memory - a confident
explanation of something you were not told is the one failure this briefing cannot
survive, because the rep will repeat it to a customer."""


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


# Tickers are for screens. A briefing is spoken, so it needs the spoken name.
COMPANY_NAMES = {"NVDA": "NVIDIA", "GOOG": "Alphabet", "MU": "Micron"}


def build(mode: str, account: str, deltas: str, framing: str, context: str = "",
          macro: str = "", callback: str = "", derived_note: str = "",
          news: str = "", requested_topic: str = "") -> str:
    """Assemble the prompt for one mode."""
    m = MODES[mode]
    company = COMPANY_NAMES.get(account, account)

    callback_instruction = (
        f"THE COMPANY'S NAME MUST APPEAR IN YOUR FIRST SENTENCE. Not the second. "
        f"A rep playing four of these back to back has to know whose episode started. "
        f"Say who this episode is about within that first sentence, using the company's "
        f"name as a person would say it out loud - {company}, not a ticker symbol. The "
        f"rep covers several accounts and may be playing these back to back, so they need "
        f"to know whose episode this is. Work it into a real sentence about what "
        f"happened - \"{company} just posted a quarter that...\" Never as an "
        f"announcement or an appositive: not \"{company}, that's who we're covering "
        f"today\", not \"{company}. Here's today's episode.\" And never address the "
        f"company as if it were the listener.\n\n"
        + (
            f"ONE sentence, then move on: last time they recapped this account, this is "
            f"the single thing that slipped past them, and it comes up again today.\n\n"
            f"    {callback}\n\n"
            f"Say it in plain words, the way you would mention it to a colleague. Do not "
            f"list other things they missed - you have been given the one that matters "
            f"and the rest are deliberately withheld. Do not stack numbers into it. Do "
            f"not say \"you missed\" more than once. Do not scold. It is a nudge before "
            f"the story starts, not a report card.\n"
            f"It has to read as THEIR gap, not our recap: \"that one slipped past you "
            f"last time\", never \"we warned\" or \"you heard us flag\"."
            if callback else
            "No graded recap exists yet, so there is nothing to call back. Go straight "
            "into today's story after naming the company."
        )
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

    # A deep dive opens on the idea, not the quarter. The standard cold open
    # pulls in the company's results, which on a Mode C day is the one thing
    # the episode is not about.
    if mode == "C":
        callback_instruction += (
            "\n\nThis is a deep dive, so open on the SUBJECT, not the results. Name the "
            "company and go straight into the thing you are explaining. Do not open with "
            "revenue, growth or margins - a listener who wanted the quarter would have "
            "played a different episode."
        )

    phases = PHASES.format(
        callback_instruction=callback_instruction,
        core_minutes=m["core_minutes"],
        core_instruction=(
        m["core"] + (
            f"\n\nTHE REP ASKED FOR THIS SUBJECT. Build the deep dive around it:\n\n"
            f"    {requested_topic}\n\n"
            f"They asked because they did not follow it the first time, so assume no "
            f"prior understanding and build from the ground up. If the sources you have "
            f"genuinely do not cover it, say so plainly in one sentence and teach the "
            f"closest thing they do cover - never fill the gap from memory."
            if requested_topic and mode == "C" else ""
        )
    ),
        macro_instruction=macro_instruction,
    )

    # Order matters: whatever comes first reads as the subject. On a news day
    # the filings are background, and leading with them produced an earnings
    # recap wearing a "Today's news" label.
    numbers_block = (
        ("THE MEASURED NUMBERS\nComputed from SEC XBRL filings by arithmetic. Quote them "
         "exactly as given.\n\n" + deltas)
        if mode != "B" else
        ("BACKGROUND FIGURES - NOT TODAY'S SUBJECT\nFrom the most recent filing, for "
         "sizing a claim in the news above. Do not lead with these and do not walk "
         "through them.\n\n" + deltas)
    )
    news_block = (
        ("TODAY'S NEWS - THIS IS THE SUBJECT OF THE EPISODE\nEach item carries its "
         "publication and headline. Name them when you use one.\n\n" + news)
        if mode == "B" else
        ("RECENT NEWS ON THIS ACCOUNT\nEach item carries its publication and headline. "
         "Name them when you use one.\n\n" + news)
    ) if news else ""

    parts = [
        HEADER.format(account=account, mode=mode, mode_name=m["name"], words=m["words"]),
        news_block if mode == "B" else numbers_block,
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
        numbers_block if mode == "B" else news_block,
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

EPISODE_META_PROMPT = """Here is a briefing script about {account}.

Write two things for the page a rep sees before they press play.

TITLE - six to nine words, the way a podcast episode is titled. Name the actual tension or
finding in THIS episode. Not "NVIDIA Q2 Update", which could title any episode; something
you could only write after hearing this one. One clean phrase, no colon splitting it in
two, no question, no clickbait. Never the words briefing, episode, update, or recap.

TAKEAWAYS - three lines. A rep with ninety seconds and no time to listen should still walk
away with the point. Each line is one sentence, states something concrete, and includes the
number that makes it real. Not "margins are under pressure" but "gross margin holds at 75
percent but management guides to 74 next quarter on memory prices". Order them by what
matters most to a customer conversation. Do not repeat the title.

Reply with JSON only, no other text:
{{"title": "...", "takeaways": ["...", "...", "..."]}}

Script:
{script}"""


QUESTIONS_PROMPT = """Here is a briefing script about {account}.

Write three comprehension questions for the rep who just listened to it.

This replaces asking them to recap freely. A free recap is hard to grade fairly and lets a
rep skate by on whatever they happened to remember. Three questions aimed at the things
that actually matter make the gaps visible.

What makes a good question here:
- It targets something that would change a customer conversation, not a trivia detail.
- It can be answered in two or three spoken sentences.
- Someone who understood the episode can answer it; someone who half-listened cannot.
- It asks for the WHY or the SO WHAT, not just recall of a number. Not "what was gross
  margin" but "why is gross margin expected to fall next quarter, and what does that mean
  for how this account buys".

Every question must be answerable from the script alone. Do not reach for anything the
script does not say, and never attribute a figure to a company other than {account} - a
generated question that says "TSMC's 156-basis-point margin expansion" when that figure is
{account}'s own is asking about something that did not happen, and the rep cannot answer
it correctly.

Prefer questions that need no figure at all. "Why is gross margin expected to fall next
quarter, and what does that mean for how this account buys" is a better question than one
built around a number, because it tests understanding rather than recall.

Order them by importance. The first question should be the single thing you would want the
rep to have taken away.

For each, also write what a good answer contains - the two or three points that must be
present. This is what the answer gets graded against, so be concrete and specific to this
episode.

Reply with JSON only, no other text:
{{"questions": [
  {{"question": "...", "expected_points": ["...", "..."], "why_it_matters": "one line"}},
  ...
]}}

Script:
{script}"""
