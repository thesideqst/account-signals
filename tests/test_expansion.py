"""The length guard's acceptance rule: an expansion may never add a figure.

Loaded with ast for the same reason as test_grounding.py - synthesize.py
imports the Databricks SDK at module level, and these functions are pure.

The fabrication case is real: the first expansion ever run, on Micron on
2026-10-26, turned a supplied basis-point move into a dollar figure that was
both model arithmetic and wrong by a factor of 100.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "briefing" / "synthesize.py"


def _load(*names):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fns = [n for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"re": __import__("re")}
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(SRC), "exec"), ns)
    return ns


_ns = _load("figures", "unsupported_figures", "accept_expansion")
accept_expansion = _ns["accept_expansion"]

SOURCE = ("- Gross margin EXPANDED by 219 basis points versus last quarter.\n"
          "- Operating margin EXPANDED by 31 basis points versus last quarter.")
DRAFT = ("Gross margin expanded by 219 basis points versus last quarter. "
         "Is that enough to offset price pressure from the plant issue?")


def test_rejects_an_expansion_that_derives_a_new_figure():
    """The Micron case: 219 basis points became '$2.19 per sales dollar'."""
    expanded = (DRAFT + " Gross margin expanded by 219 basis points, meaning each "
                "sales dollar left $2.19 more after cost of goods. Operating "
                "margin moved too, by 31 basis points versus last quarter.")
    assert not accept_expansion(DRAFT, expanded, SOURCE)


def test_accepts_a_longer_expansion_that_stays_grounded():
    expanded = (DRAFT + " Operating margin expanded by 31 basis points as well, "
                "so the gain carried through below the gross line. The question "
                "for the rep is whether that holds once the new plant is late.")
    assert accept_expansion(DRAFT, expanded, SOURCE)


def test_rejects_an_expansion_that_is_not_longer():
    assert not accept_expansion(DRAFT, "Gross margin expanded.", SOURCE)


def test_an_ungrounded_figure_already_in_the_draft_does_not_block_it():
    """The guard compares against the draft, so the expansion is judged only
    on what it ADDS. A figure the draft already carried is the existing
    GROUNDING WARNING's job, not a reason to discard extra length."""
    draft = DRAFT + " Shipments rose 7 percent."
    expanded = draft + (" Operating margin expanded by 31 basis points versus "
                        "last quarter, so the improvement reached the operating line.")
    assert accept_expansion(draft, expanded, SOURCE)
