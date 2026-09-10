"""The two-host dialogue: parsing the model's JSON turns, and concatenating
them back into the flat string every guard and every downstream consumer
(TTS, grading, the meta/questions calls) expects.

Lifted out of src/briefing/synthesize.py with ast rather than imported,
because that module imports the Databricks SDK and builds a Spark session at
import time. Both functions are pure, so extracting the definitions keeps
this runnable with no Databricks dependencies - which is what lets it run in
CI, same as test_grounding.py and test_macro_units.py.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "briefing" / "synthesize.py"


def _load(*names):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fns = [n for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"json": __import__("json")}
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(SRC), "exec"), ns)
    return [ns[n] for n in names]


parse_turns, concat_turns = _load("parse_turns", "concat_turns")


def test_parses_a_well_formed_turn_array():
    turns, err = parse_turns(
        '[{"speaker": "host_a", "text": "NVIDIA just posted a quarter."}, '
        '{"speaker": "host_b", "text": "Wait, margins held at 75?"}]'
    )
    assert err is None
    assert turns == [
        {"speaker": "host_a", "text": "NVIDIA just posted a quarter."},
        {"speaker": "host_b", "text": "Wait, margins held at 75?"},
    ]


def test_strips_a_fenced_code_block():
    """Same fence-stripping behaviour as the EPISODE_META_PROMPT and
    QUESTIONS_PROMPT parsers in main(): ```json ... ``` or a bare ``` fence
    around the array."""
    turns, err = parse_turns(
        '```json\n[{"speaker": "host_a", "text": "hello"}]\n```'
    )
    assert err is None
    assert turns == [{"speaker": "host_a", "text": "hello"}]


def test_ignores_text_outside_the_brackets():
    turns, err = parse_turns(
        'Sure, here is the conversation:\n'
        '[{"speaker": "host_a", "text": "hello"}]\n'
        'Let me know if you need changes.'
    )
    assert err is None
    assert turns == [{"speaker": "host_a", "text": "hello"}]


def test_drops_a_turn_with_an_invalid_speaker():
    """A hallucinated third speaker - or a typo - is dropped, not renamed or
    coerced, so a malformed turn never reaches the script under a guessed
    label."""
    turns, err = parse_turns(
        '[{"speaker": "host_a", "text": "real"}, '
        '{"speaker": "narrator", "text": "not a real host"}]'
    )
    assert err is None
    assert turns == [{"speaker": "host_a", "text": "real"}]


def test_drops_a_turn_with_empty_text():
    turns, err = parse_turns(
        '[{"speaker": "host_a", "text": "  "}, '
        '{"speaker": "host_b", "text": "real"}]'
    )
    assert err is None
    assert turns == [{"speaker": "host_b", "text": "real"}]


def test_speaker_is_normalized_to_lowercase():
    turns, err = parse_turns('[{"speaker": "HOST_A", "text": "hi"}]')
    assert err is None
    assert turns == [{"speaker": "host_a", "text": "hi"}]


def test_malformed_json_returns_an_error_not_a_crash():
    turns, err = parse_turns("not json at all, no brackets")
    assert turns == []
    assert err is not None


def test_valid_json_with_no_usable_turns_is_not_an_error():
    """An empty array, or one where every item fails validation, is 'nothing
    usable', not a parse failure - main() prints a different message for the
    two cases, but neither should look like a crash."""
    turns, err = parse_turns("[]")
    assert turns == []
    assert err is None

    turns, err = parse_turns('[{"speaker": "narrator", "text": "x"}]')
    assert turns == []
    assert err is None


def test_empty_and_missing_text_are_safe():
    assert parse_turns("") == ([], None)
    assert parse_turns(None) == ([], None)


def test_concat_joins_turns_with_a_blank_line():
    """A blank line between turns, not a space - so the density guard's
    per-paragraph scan (which splits the script on a bare newline) still
    treats each turn as its own paragraph, the same granularity a monologue's
    own paragraph breaks gave it."""
    turns = [
        {"speaker": "host_a", "text": "NVIDIA just posted a quarter."},
        {"speaker": "host_b", "text": "Wait, what changed?"},
    ]
    assert concat_turns(turns) == (
        "NVIDIA just posted a quarter.\n\nWait, what changed?"
    )


def test_concat_of_empty_turns_is_empty_string():
    assert concat_turns([]) == ""


def test_concat_skips_turns_with_no_text():
    turns = [{"speaker": "host_a", "text": "real"}, {"speaker": "host_b", "text": ""}]
    assert concat_turns(turns) == "real"
