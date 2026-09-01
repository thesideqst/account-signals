"""Standing subjects for a deep dive when nobody has asked for anything.

FALLBACK_SUBJECTS is lifted out of synthesize.py with ast rather than imported,
because that module needs the Databricks SDK.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "briefing" / "synthesize.py"


def _const(name):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == name:
            return ast.literal_eval(n.value)
    raise AssertionError(f"{name} not found")


SUBJECTS = _const("FALLBACK_SUBJECTS")


def test_all_nine_subjects_the_user_asked_for_are_present():
    """business model, company history, executive stakeholder mapping,
    executive compensation, supply chain, big bets, competitors, SWOT,
    technology partnerships."""
    assert len(SUBJECTS) == 9
    joined = " | ".join(t for t, _ in SUBJECTS).lower()
    for expected in ["money", "history", "executive", "paid", "supply chain",
                     "bets", "compete", "strengths", "partnership"]:
        assert expected in joined, f"no subject covers {expected}"


def test_every_subject_carries_its_own_match_terms():
    """Relying on splitting the phrase into words over four characters is what
    this replaces: "big bets" has none, so the filter would be empty and the
    query would pull whatever was most recent - the Moderna failure."""
    for subject, terms in SUBJECTS:
        assert terms, f"{subject} has no match terms"
        assert all(t == t.lower() for t in terms), f"{subject} has upper case"
        assert all(len(t) >= 3 for t in terms), f"{subject} has a tiny term"


def test_the_word_splitter_really_would_have_failed():
    """The reason the terms exist, asserted rather than claimed."""
    for phrase in ["big bets", "supply chain"]:
        long_words = [w for w in phrase.split() if len(w) > 4]
        assert phrase != "big bets" or long_words == [], \
            "big bets should yield no usable filter words"


def test_subjects_are_unique_so_rotation_cannot_stall():
    names = [t for t, _ in SUBJECTS]
    assert len(names) == len(set(names))


def test_subjects_are_phrased_for_the_ear_not_as_labels():
    """These reach the script, and the voice rules forbid label-speak."""
    for subject, _ in SUBJECTS:
        assert subject == subject.lower() or subject[0].islower(), subject
        assert "_" not in subject
        assert len(subject.split()) >= 3, f"{subject!r} reads as a label"
