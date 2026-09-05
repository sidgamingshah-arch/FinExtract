"""What the review queue is allowed to contain.

The product rule: the queue carries exactly three things — a face figure nobody could place, a
validation rule that failed, and a section subtotal that does not match the values under it.
Anything else is either not a defect or not actionable, and every card that is neither costs the
analyst the attention the three real ones need.

This module pins the FIRST category's boundary, which is where the noise was: a row from a
statement served as a MATRIX is not a mapping failure. Measured on the China SCE 2023 filing before
the guard: 39 unmapped cards, 32 of them rows of the statement of changes in equity — a statement
with no mapped rows at all, because the template has no concept for an equity MOVEMENT and the
statement is served through the matrix view instead. The one thing the category exists for was
buried under rows nothing had asked the mapper to place.
"""
from __future__ import annotations

import uuid

import pytest


def _seed(rows: list[dict], filename: str = "queue.pdf") -> str:
    """A document with one stored succeeded run carrying `rows`.

    Seeded rather than extracted: what is under test is which rows the queue builder indicts, and a
    fixture PDF cannot produce a matrix row and a plain unmapped row side by side as cheaply.
    """
    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun

    init_db()
    with SessionLocal() as session:
        doc = Document(filename=filename, fmt="pdf", byte_size=1, page_count=2,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        session.flush()
        session.add(ExtractionRun(document_id=doc.id, status="succeeded", options={},
                                  result={"filename": filename, "rows": rows}))
        session.commit()
        return doc.id


def _value(column_index=None, value="1000"):
    return {"period_label": "current", "basis": "consolidated", "value": value,
            "column_index": column_index,
            "provenance": {"page_index": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}}


def _row(label: str, key=None, column_index=None, printed_in=None):
    return {"id": str(uuid.uuid4()), "source_label": label, "canonical_key": key,
            "values": [_value(column_index)], "flags": [], "mapping_confidence": None,
            "printed_in": printed_in}


def test_a_matrix_row_is_not_reported_as_an_unmapped_face_item(client):
    """The guard. Both rows below are unmapped; only one of them is a mapping failure.

    "At 1 January 2023" is a movement row of the equity matrix — its figures sit in reserve COLUMNS
    (``column_index``), it was never a candidate for a canonical key, and the statement it belongs
    to is served as a grid. "Deferred consideration payable" is a face caption carrying money that
    nothing placed, which is the whole point of the category."""
    doc_id = _seed([
        _row("At 1 January 2023", column_index=0),
        _row("Transfer to statutory surplus reserve", column_index=3),
        _row("Deferred consideration payable"),
    ])
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    unmapped = [c for c in body["checks"] if c["type"] == "unmapped"]
    assert [c["title"] for c in unmapped] == ["Deferred consideration payable"]


def test_the_matrix_rows_are_counted_as_carrying_no_finding(client):
    """Not indicted, and therefore counted the way every other clean row is. A row excluded from the
    queue but still counted as having a finding would make the header tiles disagree with the list
    they head — the defect class this product has been bitten by twice."""
    doc_id = _seed([
        _row("At 1 January 2023", column_index=0),
        _row("Inventories", key="bs_current_assets__inventories"),
        _row("Deferred consideration payable"),
    ], "queue-counts.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert body["summary"]["open"] == 1
    # Three rows, one of them indicted: the other two carry no finding.
    tiles = {t.get("label"): t.get("count") for t in (body.get("tiles") or [])}
    if "No findings" in tiles:                     # the tile is optional in the payload
        assert tiles["No findings"] == 2


def test_a_row_with_no_column_index_is_judged_exactly_as_before(client):
    """The guard keys on ``column_index``, which only the matrix reader sets — not on the page's
    statement. So an ordinary face row on any page is unaffected, which is what stops this from
    quietly excusing a real mapping failure printed on an equity page."""
    doc_id = _seed([_row("A caption nothing placed")], "queue-plain.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert [c["type"] for c in body["checks"]] == ["unmapped"]


def test_the_signal_is_set_in_exactly_one_place():
    """Why ``column_index`` can be trusted as "this row came off a matrix": the matrix path in
    ``row_reconstruct`` is its only writer. If a second writer appears, this guard starts excusing
    rows it was never meant to, so the property is asserted rather than assumed.

    The writer is identified by the code AROUND it rather than by its line number. Pinning the line
    guarded the same property and failed every time an unrelated edit above it shifted the file,
    which trains a reader to re-stamp the number instead of asking what moved — so the check is the
    thing that actually matters: exactly one writer, in ``row_reconstruct``, inside the block that
    labels its provenance ``matrix``.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    writers = [(p.relative_to(root), i, lines)
               for p in root.rglob("*.py")
               for lines in [p.read_text(encoding="utf-8").splitlines()]
               for i, line in enumerate(lines, 1)
               if re.search(r"column_index\s*=(?!=)", line) and "def " not in line]

    assert len(writers) == 1, [(str(f), i) for f, i, _ in writers]
    path, line_no, lines = writers[0]
    assert str(path) == "services/row_reconstruct.py", str(path)
    nearby = "\n".join(lines[max(0, line_no - 12):line_no + 8])
    assert "matrix" in nearby, f"the sole writer at line {line_no} is no longer the matrix path"


# --------------------------------------------------------------------------------------------
# The other half of "on the face of the statements": a note's detail line is not a face figure
# --------------------------------------------------------------------------------------------

def test_an_unplaced_note_row_is_not_reported_as_an_unmapped_face_item(client):
    """The category is "extracted but unmapped items ON THE FACE of statements", and a note detail
    line is not one.

    A note's rows are served through the Notes tab and the note-detail routes; they are not lines of
    the statement spread and were never candidates for a template line, so an unplaced one is not
    the defect this category names. On the real filing every unplaced row is already a face row, so
    this guard changes no count there — it is here so a filing whose notes carry unplaceable captions
    (a maturity table, a segment breakdown, a movement schedule) cannot fill the queue with them.
    """
    doc_id = _seed([
        _row("Within one year", printed_in="notes"),
        _row("Analysis of movements in the year", printed_in="notes"),
        _row("Deferred consideration payable", printed_in="face"),
    ], "queue-notes.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    unmapped = [c for c in body["checks"] if c["type"] == "unmapped"]
    assert [c["title"] for c in unmapped] == ["Deferred consideration payable"]


def test_an_unstamped_row_is_still_reported(client):
    """BOTH guards are POSITIVE signals, and this is why that matters.

    Not every row carries a ``printed_in`` stamp — it is set where the extraction can tell, and a row
    it could not place on either side of the filing has none. Testing for "is it stamped face" rather
    than "is it stamped notes" would drop exactly those rows: the ones the extraction understood
    least, which are the ones most likely to be a real mapping failure. The queue would then get
    quieter the worse the extraction got.
    """
    doc_id = _seed([_row("A caption on a page nothing classified")], "queue-unstamped.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert [c["type"] for c in body["checks"]] == ["unmapped"]


# --------------------------------------------------------------------------------------------
# The SEEDED SAMPLE teaches the same three categories, in the same words
# --------------------------------------------------------------------------------------------

def test_the_sample_speaks_the_real_routes_check_vocabulary(client):
    """The sample is the first thing a new user sees, so a category it shows had better exist.

    It used to speak its own: `subtotal`, `sign` and `note`, of which only `balance` was ever a kind
    the real route serves. Those names came from ``app/services/checks.py``, a parallel checks engine
    with no caller in the app (now deleted). The `note` card was worse than a synonym — note-tie
    findings are not raised at all any more, so the sample advertised a queue category that cannot
    occur, while showing NO card for the category that matters most.

    Derived from the real route's own maps rather than from a list written twice here: a kind added
    to `_ACCOUNTING_TYPES` or a new row-shaped kind is admitted automatically, and a kind the sample
    invents fails.
    """
    from app.api.routes.documents import _ACCOUNTING_TYPES, _ROW_SHAPED_TYPES

    review = client.get("/api/v1/projects/demo/review?locale=en").json()
    served = {c["type"] for c in review["checks"]}
    assert served, "the sample must serve findings at all"
    assert served <= set(_ACCOUNTING_TYPES) | set(_ROW_SHAPED_TYPES), served
    # …and it shows BOTH shapes, so a reader meets the accounting checks and the row-shaped finding.
    assert served & set(_ACCOUNTING_TYPES) and served & set(_ROW_SHAPED_TYPES)
    # …and the same TONE vocabulary, which is a severity and not a colour name. It diverged three
    # ways: the type declared "indigo" (never sent), `toneColors` painted "low" red, the real route
    # sent "high" for every failure and "low" for the row-shaped card, and the sample sent "low" for
    # its blocking balance card. Result on screen: the failures indigo, the mildest finding red.
    tones = {c["type"]: c["tone"] for c in review["checks"]}
    assert set(tones.values()) <= {"high", "med", "low"}, tones
    for kind, tone in tones.items():
        expected = "high" if kind in _ACCOUNTING_TYPES else "med"
        assert tone == expected, f"{kind} is {tone}, not {expected}"

    # The chips partition the list, and every chip selects something — see `_demo_review_tabs`.
    buckets = [t for t in review["tabs"] if t["types"] is not None]
    assert sum(t["count"] for t in buckets) == len(review["checks"])
    for tab in buckets:
        assert tab["count"] == len([c for c in review["checks"] if c["type"] in tab["types"]])
        assert tab["count"] > 0, tab["label"]


def test_a_card_kind_with_no_chip_fails_loudly_instead_of_going_invisible():
    """``_assert_known_kinds`` is the guard rail on the declaration above.

    A card reaches the screen through the chips, and the chips are built from
    ``_ACCOUNTING_TYPES``/``_ROW_SHAPED_TYPES``. So a kind nobody registered is a card no chip
    counts: invisible under every filter, while still inside the "All" total — which then disagrees
    with the sum of the chips beside it. That is the counts-disagree-with-content defect this file's
    module docstring is about, and it is cheaper to raise here than to find it on a screen.
    """
    import pytest

    from app.api.routes.documents import _assert_known_kinds

    # Everything the queue declares passes, asked of the declaration rather than of a copy of it.
    from app.api.routes.documents import _ACCOUNTING_TYPES, _ROW_SHAPED_TYPES
    _assert_known_kinds([{"type": t} for t in _ACCOUNTING_TYPES | _ROW_SHAPED_TYPES])

    # A retired kind is exactly as unregistered as an invented one, which is the point: bringing one
    # back means making the product decision again, not re-adding a literal.
    for kind in ("low_confidence", "off_template", "uncomputed", "note_tie", "sign", "subtotal"):
        with pytest.raises(AssertionError, match=kind):
            _assert_known_kinds([{"type": "balance"}, {"type": kind}])


# --------------------------------------------------------------------------------------------
# A DEMOTED GROSS PARENT: not a mapping failure, and the real defect said in its own words
# --------------------------------------------------------------------------------------------
#
# `map_ontology._enforce_containment` un-files a parent whose components are also on the face —
# clears its `canonical_key`, demotes it to a subtotal, and records what replaced it in
# `contains_mapped_children`. Its money is on the face, through those components.
#
# The stage ALSO wrote `low_mapping_confidence` on it when the components did not add up, to route
# the unexplained part to review; its own comment says why ("without that, unfiling silently removes
# the unexplained part of the figure from the statement and every remaining check ties"). So the
# finding arrived as a LOW-CONFIDENCE card about a row whose mapping was an exact match at 1.0, and
# when that card went it arrived as UNMAPPED, printing "— (no confident match)" about a caption the
# mapper had recognised perfectly. Both labels were wrong about the same real defect.
#
# Measured on the China SCE 2023 filing: 5 of the 12 cards in the unmapped category were these.

def _parent(label, children, value, *, gap_of=None, prior=None):
    """A row shaped as the containment pass leaves a demoted parent."""
    flags = [f"contains_mapped_children:{','.join(children)}"]
    if gap_of:
        flags += [f"containment_unexplained:{gap_of}:1", "low_mapping_confidence"]
    values = [_value(value=str(value))]
    if prior is not None:
        values.append({**_value(value=str(prior)), "period_label": "prior"})
    return {"id": str(uuid.uuid4()), "source_label": label, "canonical_key": None,
            "role": "subtotal", "values": values, "flags": flags,
            "mapping_confidence": 1.0, "mapping_method": "exact", "printed_in": "face"}


def _kid(key, value, prior=None):
    values = [_value(value=str(value))]
    if prior is not None:
        values.append({**_value(value=str(prior)), "period_label": "prior"})
    return {"id": str(uuid.uuid4()), "source_label": key, "canonical_key": key,
            "values": values, "flags": [], "mapping_confidence": 1.0}


def test_a_demoted_gross_parent_is_not_reported_as_an_unmapped_face_item(client):
    """It has no key, and a missing key is the shape of BOTH a mapping failure and a deliberate
    demotion. Only the flag tells them apart, which is why the guard reads the flag."""
    doc_id = _seed([
        _parent("Cash and cash equivalents", ["bs_current_assets__restricted_cash"], 100),
        _kid("bs_current_assets__restricted_cash", 100),
        _row("Deferred consideration payable"),
    ], "queue-demoted.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    unmapped = [c for c in body["checks"] if c["type"] == "unmapped"]
    assert [c["title"] for c in unmapped] == ["Deferred consideration payable"]
    # Its components account for it exactly, so nothing at all is raised about it: that is the
    # outcome the containment pass exists to produce, not a finding.
    assert not [c for c in body["checks"] if c["type"] == "containment_gap"]


def test_a_demoted_parent_its_components_do_not_account_for_is_raised_as_what_it_is(client):
    """THE ASSERTION THAT FAILS WITH THE DEFECT RESTORED, on both halves of it.

    The 400 the components do not explain is on no line of the spread — the parent was removed and
    the children only carry 600 — so it has to be reported, and reported as what it is rather than
    as a caption nobody recognised.
    """
    doc_id = _seed([
        _parent("Cash and cash equivalents",
                ["bs_current_assets__restricted_cash"], 1000,
                gap_of="bs_current_assets__cash_and_cash_equivalents"),
        _kid("bs_current_assets__restricted_cash", 600),
    ], "queue-gap.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    gaps = [c for c in body["checks"] if c["type"] == "containment_gap"]
    assert len(gaps) == 1
    card = gaps[0]
    assert card["target"] == "bs_current_assets__cash_and_cash_equivalents"
    assert card["delta"] == "400"
    printed = {row[0]: row[1] for row in card["calc"]}
    assert printed["Printed in the document"] == "1,000"
    assert printed["Sum of the lines that replaced it"] == "600"
    assert printed["Not on any line"] == "400"
    # It names the CHILDREN, which are the lines an analyst checks against the page, and not the
    # parent — the parent carries no key, so no grid line is it.
    assert card["names"] == ["bs_current_assets__restricted_cash"]
    # …and it is NOT also reported as an unmapped face figure: one defect, one card.
    assert not [c for c in body["checks"] if c["type"] == "unmapped"]


def test_the_containment_card_offers_no_mechanical_fix_and_says_the_fix_in_words(client):
    """Writing the printed total back over the components would close the card and leave the
    defect — the same anti-fix `_calculated_checks` refuses. The card explains instead."""
    doc_id = _seed([
        _parent("Cash and cash equivalents", ["bs_current_assets__restricted_cash"], 1000,
                gap_of="bs_current_assets__cash_and_cash_equivalents"),
        _kid("bs_current_assets__restricted_cash", 600),
    ], "queue-gap-fix.pdf")
    card = next(c for c in client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()["checks"]
                if c["type"] == "containment_gap")
    assert card["fix_action"] is None and card["remap"] is None
    assert "counting the money twice" in card["fix"]


def test_the_prior_column_is_checked_too_like_the_relation_cards_beside_it(client):
    """A break in the prior column is a real break: the figures are extracted, served and exported.
    The relation checks cover both columns (9 and 9 on the filing this was measured against), so
    this does too — deliberately unlike `_calculated_checks`, which is current-only for its own
    reasons."""
    doc_id = _seed([
        _parent("Cash and cash equivalents", ["bs_current_assets__restricted_cash"], 1000,
                gap_of="bs_current_assets__cash_and_cash_equivalents", prior=900),
        _kid("bs_current_assets__restricted_cash", 1000, prior=500),
    ], "queue-gap-prior.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    gaps = {c["subject"]["period"]: c for c in body["checks"] if c["type"] == "containment_gap"}
    # Current ties exactly and raises nothing; prior is out by 400 and is raised.
    assert set(gaps) == {"prior"}
    assert gaps["prior"]["delta"] == "400"


def test_an_edit_that_breaks_a_containment_raises_the_card_the_stage_never_flagged(client):
    """THE ARITHMETIC DECIDES, NOT THE STAGE'S FLAG — the case that made the difference.

    `containment_unexplained` records whether the components accounted for the parent AT EXTRACTION
    TIME. This queue is rebuilt from the current figures on every fetch, so an analyst editing a
    child in this very screen can open a gap the stage never saw. Gated on the flag, that break
    would be on no card at all; and the mirror case — an edit that CLOSES a gap the stage did flag —
    would keep being reported after it was fixed.

    The row below carries no gap flag (the stage found none) and its components no longer add up.
    """
    doc_id = _seed([
        _parent("Cash and cash equivalents", ["bs_current_assets__restricted_cash"], 1000),
        _kid("bs_current_assets__restricted_cash", 600),
    ], "queue-edited-gap.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    gaps = [c for c in body["checks"] if c["type"] == "containment_gap"]
    assert len(gaps) == 1 and gaps[0]["delta"] == "400"
    # With no flag there is no concept to name it by — the key was cleared when it was un-filed — so
    # it is identified by the caption, exactly as the row-shaped card identifies a keyless row.
    assert gaps[0]["target"] == "Cash and cash equivalents"


def test_a_containment_the_stage_flagged_and_an_edit_fixed_is_no_longer_reported(client):
    """The mirror of the case above, and the reason a stale flag may not decide: the components now
    account for the parent exactly, so the card is gone even though the flag is still on the row."""
    doc_id = _seed([
        _parent("Cash and cash equivalents", ["bs_current_assets__restricted_cash"], 1000,
                gap_of="bs_current_assets__cash_and_cash_equivalents"),
        _kid("bs_current_assets__restricted_cash", 1000),
    ], "queue-fixed-gap.pdf")
    body = client.get(f"/api/v1/documents/{doc_id}/review?locale=en").json()

    assert not [c for c in body["checks"] if c["type"] == "containment_gap"]
    # …and it is still not reported as an unmapped face figure either: it is a demotion, and its
    # money is now entirely on the face through the line that replaced it.
    assert not [c for c in body["checks"] if c["type"] == "unmapped"]


def test_the_real_route_paints_a_failed_check_loudly_and_a_placement_gently(client):
    """The same tone rule the sample is held to, on the route that serves real runs.

    Both halves matter and both were wrong: a failed check must be the loud one (it was rendering
    informational-indigo, because the server's "high" was in neither the TS type nor `toneColors`),
    and the row-shaped card must NOT be (it said "low", which that function painted red — the
    loudest colour on the queue's mildest finding).
    """
    import tests.test_review_judgement as tj
    from app.api.routes.documents import _ACCOUNTING_TYPES, _build_review

    figures = {"bs_total_assets": 100, "bs_total_equity_and_liabilities": -90,
               "bs_equity__total_equity": 40, "bs_liabilities__total_liabilities": 60}
    rows = [tj._row(k, v) for k, v in figures.items()]
    rows.append({"source_label": "A caption nothing placed", "canonical_key": None,
                 "values": [{"basis": "consolidated", "period_label": "current", "value": "5"}]})
    checks = _build_review(rows, "d.pdf", "en", [], tj._real_structural_rows(figures),
                           tj._shipped_template())["checks"]
    tones = {c["type"]: c["tone"] for c in checks}
    assert {"balance", "structural", "unmapped"} <= set(tones), tones
    for kind, tone in tones.items():
        assert tone == ("high" if kind in _ACCOUNTING_TYPES else "med"), f"{kind}={tone}"
