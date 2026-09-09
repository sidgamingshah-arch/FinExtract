"""Where the shipped line-item set is read from, and what it installs into the process.

ONE PLACE THAT KNOWS THE SEED'S PATH. Before this, three callers each built their own path to
`output_csv_hk_line_items.json` — the API route, the builder script and the parity harness — which
is how a fourth caller ends up reading a different file and nobody notices. The route still owns
serving it; this owns the process-wide side effect.

WHAT GETS INSTALLED, and why it is a side effect rather than a parameter. `normalize_label` folds a
printed caption into the form the alias index is keyed on, and it builds its eight patterns from a
character inventory: bracket widths, the quote marks that mark a coined abbreviation, the CAS
enumerators and sign-note words, the Han code-point ranges. That inventory is now declared in the
set (`vocabulary.caption_characters`), and the fold must be SYMMETRIC — the same one applied to
every alias when the index is built and to every caption matched against it. An alias folded one
way can never meet a caption folded another. So it is process-wide, installed once, and a
conflicting second install raises rather than silently taking the last one.

WHAT NO LONGER HAPPENS AT LOAD, so nobody reinstates it. This module used to hold the shipped
rulebook's path (`ONTOLOGY = output_csv_hk_ontology.json`) and refuse the set —
`ResidualFrameworkDrift` — when the set's carried `residual_framework` diverged from the
rulebook's. That check existed because the set's copy governed NOTHING: every real read was
`getattr(ontology, "residual_framework")` on a stored rulebook, so the honest guarantee available
then was only "the two cannot silently disagree". Line items is now the single configuration
engine: the sweep reads the set's block, through `services.working_view`, and there is no second
copy left to check it against. A comparison against a JSON file nothing else reads would be
pinning the set to an artefact a user cannot see or edit, which is the opposite of the point — so
it is gone, and this was the last read of an ontology JSON in app code.
"""
from __future__ import annotations

import json
import pathlib
from typing import Callable

from app.schemas.line_items import LineItemSet, load_line_item_set
from app.services.mapping import install_caption_inventory

SEED = (pathlib.Path(__file__).resolve().parents[1]
        / "sample" / "templates" / "output_csv_hk_line_items.json")


def load_shipped_set(*, resolve: bool = True) -> LineItemSet:
    """The shipped set, section layer folded in unless asked otherwise."""
    return load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")), resolve=resolve)


def install_shipped_caption_inventory(*, log: Callable[[str], None] | None = None) -> bool:
    """Install the shipped set's caption inventory as this process's fold. Returns True if it moved.

    FAILING SOFT IS DELIBERATE HERE, and it is the opposite of the choice made for a conflicting
    install. A missing or malformed seed must not stop the application booting: every other route
    works without it, the built-in inventory is a complete and correct fold, and a hard failure at
    startup would take down document upload and review over a configuration file that only affects
    how finely captions are recognised. A CONFLICT is different — two inventories in one process
    means the alias index and the lookup disagree, which produces silently wrong matching — so
    that one is left to raise.

    Both outcomes are logged, because the thing this codebase keeps getting wrong is configuration
    that reads like a control and controls nothing. "Installed, fold unchanged" and "not installed,
    using the built-in" have to be distinguishable from the run record.
    """
    say = log or (lambda _msg: None)
    try:
        declared = load_shipped_set().vocabulary.caption_characters
    except Exception as exc:                       # noqa: BLE001 - see the docstring
        say(f"caption inventory: NOT installed, using the built-in fold ({type(exc).__name__}: "
            f"{exc}). Captions still fold correctly; a declared inventory would be ignored.")
        return False

    if not declared:
        say("caption inventory: the shipped set declares none, using the built-in fold")
        return False

    changed = install_caption_inventory(declared, source=str(SEED.name))
    say(f"caption inventory: installed {len(declared)} entries from {SEED.name}; "
        f"the fold {'CHANGED' if changed else 'is unchanged'}")
    return changed
