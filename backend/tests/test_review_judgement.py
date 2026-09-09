"""The review-item judgement layer: a finding can be ACCEPTED by a named person, and that
acceptance must never move onto a different finding.

The single most important test in this file is
``test_reordering_the_rows_does_not_move_an_acceptance_to_another_finding``. The row-shaped check
builder keys its id on the row INDEX (``chk-unmapped-{i}``), so an id-keyed acceptance would
silently follow the index onto whatever line item now sits there — marking a real problem as
vouched for by someone who never saw it, which is strictly worse than having no acceptance
mechanism at all. Identity is therefore the hash of the finding's SUBJECT, and this file holds that
to it.

(This paragraph used to name a second builder, ``chk-lowconf-{i}``, which no longer exists. Naming
dead code in a rationale is not free: readers took the description for a live defect and re-filed
it. ``_ROW_SHAPED_TYPES`` is where the current answer lives.)
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fitz")

from tests.fixtures.generate import make_unmapped_row_pdf


def _row(key, cur, **extra):
    r = {"canonical_key": key,
         "values": [{"basis": "consolidated", "period_label": "current", "value": str(cur)}]}
    r.update(extra)
    return r


def _prov(page=0, y=None, sheet=None, cell=None):
    """A provenance dict with the VALUE box only, or a spreadsheet cell.

    This is the fallback shape, not what a native-PDF row carries: the extractor records the label's
    geometry too, and ``_pdf_prov`` below is the full shape. Kept for the cases that really do arrive
    without label geometry — an adapter reporting a page and a figure, and a run stored before the
    serializer carried ``label_bbox``.

    ``y`` places the line down the page; None means "a paginated source that reported no geometry",
    which is the sentinel case ``_prov_anchor`` cannot discriminate.
    """
    if sheet:
        return {"source_kind": "spreadsheet", "page_index": 0, "sheet": sheet, "cell": cell}
    prov = {"source_kind": "native", "page_index": page}
    if y is not None:
        prov["bbox"] = {"x0": 0.1, "y0": y, "x1": 0.9, "y1": y + 0.01}
    return prov


def _pdf_prov(y: float, digits: int, page: int = 0) -> dict:
    """Provenance as ``routes/extractions.py::_prov_dict`` serializes a native-PDF row.

    Both boxes, because the extractor records both (``services/row_reconstruct.py``): ``bbox`` IS the
    value word's box and ``label_bbox`` is the caption's. The value box is right-aligned — a filing
    prints its figures flush right — so one more digit moves x0 LEFT and leaves the label box
    untouched. That asymmetry is the whole of finding 4.
    """
    right = 0.840
    return {"source_kind": "native", "page_index": page,
            "label_bbox": {"x0": 0.101, "y0": y, "x1": 0.402, "y1": y + 0.017},
            "bbox": {"x0": right - 0.009 * digits, "y0": y, "x1": right, "y1": y + 0.017}}


def _unmapped(label, value, prov=None):
    return {"source_label": label, "canonical_key": None,
            "values": [{"basis": "consolidated", "period_label": "current",
                        "value": str(value), "provenance": prov}]}


def _lowconf(label, key, value, conf=0.2, method="fuzzy"):
    return {"source_label": label, "canonical_key": key, "mapping_confidence": conf,
            "flags": ["low_mapping_confidence"], "mapping_method": method,
            "values": [{"basis": "consolidated", "period_label": "current",
                        "value": str(value)}]}


def _accepted(check, actor="rev", role="reviewer", reason="Checked against p.42; it stands."):
    """The in-force judgement row `_build_review` would read back for `check`."""
    return {"subject_key": check["subject_key"], "subject": check["subject"],
            "evidence": check["evidence"], "reason": reason, "actor": actor,
            "actor_role": role, "at": "2026-08-12T09:00:00", "run_id": "run-1"}


def _by_key(review, subject_key):
    return next(c for c in review["checks"] if c["subject_key"] == subject_key)


# --------------------------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------------------------

def test_reordering_the_rows_does_not_move_an_acceptance_to_another_finding():
    """The defect this whole layer is built around.

    Every ``chk-unmapped-{i}`` id changes when the rows are reordered, because ``i`` is a row
    index. The subject_key does not, and — crucially — the check that now OCCUPIES the old id
    reads "open": the acceptance stayed with the line item it was made about.
    """
    from app.api.routes.documents import _build_review

    rows = [_unmapped("Trade receivables", 3410), _unmapped("Inventories", 2150),
            _unmapped("Cash and cash equivalents", 1204)]
    first = _build_review(rows, "doc.pdf", "en")
    judged = next(c for c in first["checks"] if c["subject"]["label"] == "inventories")
    judged_id, judged_key = judged["id"], judged["subject_key"]

    # A re-run whose extraction composition changed: the same three lines, rotated, exactly as one
    # more reconstructed row or one fewer suppressed heading would shift them.
    shuffled = _build_review(rows[1:] + rows[:1], "doc.pdf", "en",
                             judgements=[_accepted(judged)])
    same = _by_key(shuffled, judged_key)

    assert same["subject"]["label"] == "inventories"
    assert same["status"] == "accepted"
    # The id moved…
    assert same["id"] != judged_id
    # …and whatever now carries the old id is a DIFFERENT finding, still open.
    at_old_id = next(c for c in shuffled["checks"] if c["id"] == judged_id)
    assert at_old_id["subject_key"] != judged_key
    assert at_old_id["status"] == "open" and at_old_id["judgement"] is None
    # Exactly one finding is accepted, not "whatever sits at index 1".
    assert [c["status"] for c in shuffled["checks"]].count("accepted") == 1


def test_identity_is_byte_identical_in_every_locale():
    """One judgement has to hold in all four locales, so no localized string may reach a hash."""
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90),
            _unmapped("Something unplaceable", 5),
            _lowconf("A shaky match", "bs_cash", 7)]
    recon = [{"note_number": "9", "basis": "consolidated", "period_label": "current",
              "raw_face": 1000, "residual": 20, "within_tolerance": False}]

    per_locale = {}
    for locale in ("en", "zh", "ar", "fr"):
        review = _build_review(rows, "doc.pdf", locale, recon)
        per_locale[locale] = [(c["subject_key"], c["evidence_digest"]) for c in review["checks"]]
    assert per_locale["en"] == per_locale["zh"] == per_locale["ar"] == per_locale["fr"]
    assert per_locale["en"]                                   # and there were findings to compare


def test_an_acceptance_does_not_survive_a_figure_moving():
    """The evidence half of the identity. A changed figure is a different claim, so the card
    comes back STALE — the withdrawal made visible instead of silent."""
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90)]
    bal = _build_review(rows, "doc.pdf", "en")["checks"][0]
    judgement = _accepted(bal)

    # Same subject (the consolidated current balance identity), different figures.
    moved = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 80)]
    after = _build_review(moved, "doc.pdf", "en", judgements=[judgement])
    card = _by_key(after, bal["subject_key"])
    assert card["status"] == "stale"
    assert card["judgement"]["changed"] == ["diff", "eqliab"]
    assert card["judgement"]["changed_label"]
    # The figures AS ACCEPTED travel with it, so the reader sees what was vouched for.
    assert ["Total equity and liabilities", "90"] in card["judgement"]["accepted_rows"]
    assert after["summary"] == {"open": 1, "accepted": 0, "stale": 1, "conflict": 0,
                               "passed": after["summary"]["passed"]}


def test_a_structural_finding_whose_component_moved_reads_stale_never_accepted():
    """A structural card's figures come from the STORED relation row, so what makes it stale is
    that row changing — which is what the next extraction does after a component is corrected.

    An edit alone does not restate the relation (nothing recomputes ``run.result["structural"]``),
    and the card says so through ``inputs_edited_note`` rather than silently looking unchanged —
    see tests/test_review_fix_actions.py.
    """
    from app.api.routes.documents import _build_review

    def structural(inventories: int):
        return [{"rule_id": "rollup:bs_current_assets__total_current_assets", "kind": "rollup",
                 "status": "fail",
                 "scope_key": "consolidated/current", "expected": 3510, "actual": 1210,
                 "details": {"target": "bs_current_assets__total_current_assets",
                             "components": ["bs_current_assets__inventories",
                                            "bs_current_assets__trade_receivables"],
                             "statement": "balance_sheet", "basis": "consolidated",
                             "period_label": "current",
                             "component_values": {"bs_current_assets__inventories":
                                                  str(inventories),
                                                  "bs_current_assets__trade_receivables": "1310"},
                             "sign_suspect": "bs_current_assets__inventories"}}]

    rows = [_row("bs_current_assets__inventories", 2200),
            _row("bs_current_assets__trade_receivables", 1310)]
    card = _build_review(rows, "d.pdf", "en", [], structural(2200))["checks"][0]
    assert card["type"] == "structural"

    after = _build_review(rows, "d.pdf", "en", [], structural(2201),
                          judgements=[_accepted(card)])
    same = _by_key(after, card["subject_key"])
    assert same["status"] == "stale" and same["status"] != "accepted"
    assert "components" in same["judgement"]["changed"]


def test_the_source_anchor_keeps_two_printed_lines_on_one_page_apart():
    """PREVENTION. Two lines both captioned "Others" on p.1 are two findings, and the subject has
    to say so.

    The subject used to carry `_prov_label` — "p.1" for every line on the page — so a filing
    printing "Others 1,234" and "Others 5,678" one row apart produced ONE subject_key with two
    different evidence digests, and accepting either stamped that reviewer's name, time and reason
    onto the other. It now carries `_prov_anchor`: page plus the normalized bbox the extractor
    already recorded, quantized. The two lines are 1% of a page apart here — ten anchor buckets —
    and a printed line is never thinner than that.
    """
    from app.api.routes.documents import _build_review

    rows = [_unmapped("Others", 1234, _prov(page=0, y=0.40)),
            _unmapped("Others", 5678, _prov(page=0, y=0.41))]
    review = _build_review(rows, "doc.pdf", "en")

    assert len({c["subject_key"] for c in review["checks"]}) == 2
    # Both still PRINT "p.1" — the human-facing label is unchanged; only identity got precise.
    assert all(c["where"].endswith("p.1") for c in review["checks"])
    assert not any(c["ambiguous"] or c["conflict"] for c in review["checks"])

    judged = next(c for c in review["checks"] if c["evidence"]["value"] == "1234")
    after = _build_review(rows, "doc.pdf", "en", judgements=[_accepted(judged)])
    assert _by_key(after, judged["subject_key"])["status"] == "accepted"
    other = next(c for c in after["checks"] if c["subject_key"] != judged["subject_key"])
    # The line nobody looked at carries no name, no timestamp and no reason.
    assert other["status"] == "open" and other["judgement"] is None
    assert after["summary"] == {"open": 1, "accepted": 1, "stale": 0, "conflict": 0,
                                "passed": after["summary"]["passed"]}


def test_the_anchor_quantizes_toward_re_opening_a_finding_never_toward_merging_two():
    """The quantization choice, as an assertion rather than only a comment.

    The two failure directions are not symmetric: merging two printed lines fabricates a
    judgement, while a moved anchor merely re-opens a finding someone already looked at. The grid
    is therefore fine (thousandths of the page), so jitter well inside a bucket is absorbed and
    jitter that crosses one shows up as a NEW finding — the direction we chose.
    """
    from app.api.routes.documents import _prov_anchor

    line = _prov_anchor(_prov(page=0, y=0.400))
    # Sub-bucket jitter — far smaller than anything the reader could see — is absorbed.
    assert _prov_anchor(_prov(page=0, y=0.4001)) == line
    # A separate printed line, and the same box on another page, are separate anchors.
    assert _prov_anchor(_prov(page=0, y=0.410)) != line
    assert _prov_anchor(_prov(page=1, y=0.400)) != line
    # A spreadsheet cell is already exact, so it is used as it stands.
    assert _prov_anchor(_prov(sheet="P&L", cell="C14")) \
        != _prov_anchor(_prov(sheet="P&L", cell="C15"))
    # The two documented sentinels: they give up discrimination rather than fake it, which is
    # exactly the case `apply_judgements` refuses to attribute a judgement to.
    assert _prov_anchor(None) == "#noprov"
    assert _prov_anchor(_prov(page=0)) == _prov_anchor(_prov(page=0)) == "p0#nobox"


def test_the_anchor_is_the_labels_geometry_and_does_not_move_with_the_figure():
    """I3 for the two check types this whole layer was built around.

    The anchor used to be quantized from ``Provenance.bbox``, which IS the value word's box: "Cash
    and cash equivalents" printed 1,204 anchored at p0#b798/101/840/118 and the same line printed
    12,048 anchored at p0#b789/…, because right-aligned figures grow leftwards. So the SUBJECT moved
    when the figure did, and an acceptance ORPHANED — reported on screen as "the finding was
    corrected, or is no longer raised" — instead of going stale. The label's own box does not move
    when the amount beside it does.
    """
    from app.api.routes.documents import _prov_anchor

    four = _prov_anchor(_pdf_prov(0.101, digits=4))
    five = _prov_anchor(_pdf_prov(0.101, digits=5))
    assert four == five, "one more digit must not move the anchor"
    # It really is the label box that is being used, and the value box really did move.
    assert four.startswith("p0#l")
    assert _pdf_prov(0.101, 4)["bbox"]["x0"] != _pdf_prov(0.101, 5)["bbox"]["x0"]
    # …and it still tells two printed lines apart, which is the property it must not lose.
    assert _prov_anchor(_pdf_prov(0.140, digits=4)) != four
    assert _prov_anchor(_pdf_prov(0.101, digits=4, page=1)) != four


def test_without_label_geometry_the_anchor_falls_back_to_the_row_band_not_the_figure():
    """An adapter that reports a page and a value box only still must not anchor on x, which moves
    with the digit count. The printed line's VERTICAL band is what right-alignment cannot touch. Two
    sub-tables sharing a baseline then share an anchor — and `apply_judgements` refuses to attribute
    a judgement to either, which is the honest outcome; an anchor that silently follows a figure is
    not."""
    from app.api.routes.documents import _prov_anchor

    def value_only(y, digits):
        p = _pdf_prov(y, digits)
        p.pop("label_bbox")
        return p

    band = _prov_anchor(value_only(0.101, 4))
    assert band == _prov_anchor(value_only(0.101, 9))       # the figure grew; the row did not move
    assert band != _prov_anchor(value_only(0.140, 4))       # a different printed line
    assert "y" in band and band.startswith("p0#")


def test_a_re_priced_line_reads_stale_and_never_orphans_the_acceptance():
    """End to end over the two check types keyed on the anchor: the figure changes, so the card must
    come back STALE — the reviewer is sent to look again — and the stored judgement must not be
    reported as belonging to a finding that was corrected."""
    from app.api.routes.documents import _build_review

    def rows_of(value, digits):
        return [_unmapped("Cash and cash equivalents", value, _pdf_prov(0.101, digits)),
                _lowconf("Trade and other receivables", "bs_ca__trade_receivables", 500)]

    first = _build_review(rows_of(1204, 4), "d.pdf", "en")
    card = next(c for c in first["checks"] if c["type"] == "unmapped")
    served = _build_review(rows_of(12048, 5), "d.pdf", "en", judgements=[_accepted(card)])
    same = _by_key(served, card["subject_key"])

    assert same["status"] == "stale"
    assert same["judgement"]["changed"] == ["value"]
    assert served["judgements"]["orphaned"] == []
    assert served["summary"]["accepted"] == 0 and served["summary"]["stale"] == 1


def test_the_serializer_sends_the_label_geometry_the_anchor_documents(client):
    """The docstring half of finding 4: ``_prov_anchor`` described geometry the serializer never
    sent, so the one box that is independent of the figure could not reach it however carefully the
    anchor was written. Asserted against a REAL extraction, not a hand-built provenance dict."""
    doc_id = _extracted_with_findings(client)
    rows = client.get(f"/api/v1/documents/{doc_id}/run").json()["result"]["rows"]
    provs = [v["provenance"] for r in rows for v in r["values"] if v.get("provenance")]
    assert provs, "the fixture PDF produced no provenance, so this test proved nothing"

    from app.api.routes.documents import _prov_anchor

    paginated = [p for p in provs if not p.get("sheet")]
    assert paginated
    assert all(p.get("label_bbox") for p in paginated)
    # The label box and the value box are two different boxes — if they were the same object the
    # anchor would be value-dependent again without anything in the payload showing it.
    assert any(p["label_bbox"] != p["bbox"] for p in paginated)
    assert all(_prov_anchor(p).startswith(f"p{p['page_index']}#l") for p in paginated)


def test_two_indistinguishable_findings_share_a_subject_and_one_judgement_covers_both():
    """The BENIGN half of sharing a subject: identical values, and no geometry to tell them apart.

    A human looking at these two cards could not have said which was which — same caption, same
    figure, same page — so one acceptance legitimately covers both and the `ambiguous` caption
    ("accepting one accepts them all") is TRUE here. This is the case the old single test covered,
    and covering only this one is what hid the case below for three reviewers to find.
    """
    from app.api.routes.documents import _build_review

    rows = [_unmapped("Others", 1234), _unmapped("Others", 1234)]
    review = _build_review(rows, "doc.pdf", "en")
    assert len({c["subject_key"] for c in review["checks"]}) == 1
    assert all(c["ambiguous"] and c["ambiguous_count"] == 2 for c in review["checks"])
    assert not any(c["conflict"] for c in review["checks"])

    accepted = _build_review(rows, "doc.pdf", "en",
                             judgements=[_accepted(review["checks"][0])])
    assert [c["status"] for c in accepted["checks"]] == ["accepted", "accepted"]
    assert accepted["summary"]["accepted"] == 2 and accepted["summary"]["open"] == 0


def test_one_subject_over_differing_figures_is_a_conflict_and_no_judgement_is_attributed():
    """The half the old test hid, and the reason `_prov_anchor` alone is not the whole fix.

    Two findings on one subject carrying DIFFERENT evidence are demonstrably different claims that
    identity cannot tell apart — here two "Others" lines whose provenance reports a page and no
    geometry at all, so the anchor has nothing to discriminate on. Attributing a stored acceptance
    to either would be a fabricated human judgement, so:

    * neither card carries a judgement, and neither reads `accepted` or `stale`;
    * the group is served as `conflict`, counted, and sorted to the front;
    * `ambiguous` is FALSE — "accepting one accepts them all" is not true of differing figures;
    * the note says the queue cannot tell them apart, and says the stored acceptance is withheld.
    """
    from app.api.routes.documents import _build_review

    rows = [_unmapped("Others", 1234, _prov(page=0)), _unmapped("Others", 5678, _prov(page=0))]
    review = _build_review(rows, "doc.pdf", "en")
    assert len({c["subject_key"] for c in review["checks"]}) == 1
    assert len({c["evidence_digest"] for c in review["checks"]}) == 2
    for c in review["checks"]:
        assert c["status"] == "conflict" and c["judgement"] is None
        assert c["conflict"] is True and c["conflict_count"] == 2
        assert c["ambiguous"] is False and c["ambiguous_count"] == 0
        assert c["judgement_withheld"] is False and c["conflict_note"]
    assert review["summary"] == {"open": 2, "accepted": 0, "stale": 0, "conflict": 2,
                                 "passed": review["summary"]["passed"]}

    # Now with an acceptance recorded against that one subject_key — the situation the reviewers
    # reproduced end to end. It is attributed to NOTHING.
    judged = _accepted(review["checks"][0], actor="admin")
    after = _build_review(rows, "doc.pdf", "en", judgements=[judged])
    for c in after["checks"]:
        assert c["status"] == "conflict" and c["judgement"] is None
        assert c["judgement_withheld"] is True
        # The actor's name appears on no card, and no card claims figures changed.
        assert "admin" not in c["conflict_note"]
    assert after["summary"]["accepted"] == 0 and after["summary"]["stale"] == 0
    assert after["summary"]["conflict"] == 2
    # And it is not reported as orphaned either: the finding IS still being raised.
    assert after["judgements"]["orphaned"] == []


def _untied(residual, *, face, face_key, note="12"):
    """One untied reconciliation entry as the stage stores it, for one face line of one note."""
    return {"note_number": note, "basis": "consolidated", "period_label": "current",
            "face_key": face_key, "raw_face": face, "residual": residual,
            "within_tolerance": False, "tie_status": "untied"}


def test_a_judgement_whose_finding_vanished_is_orphaned_and_counted_nowhere():
    """A corrected finding stops being emitted. The judgement is never auto-deleted — deleting the
    record of who accepted a break is not something an audit trail should permit — so it is listed
    as orphaned instead, and it inflates no counter."""
    from app.api.routes.documents import _build_review

    rows = [_unmapped("Trade receivables", 3410)]
    gone = _accepted(_build_review(rows, "doc.pdf", "en")["checks"][0])

    after = _build_review([], "doc.pdf", "en", judgements=[gone])
    assert after["checks"] == []
    assert after["summary"] == {"open": 0, "accepted": 0, "stale": 0, "conflict": 0,
                               "passed": 0}
    orphan = after["judgements"]["orphaned"][0]
    assert orphan["subject_key"] == gone["subject_key"]
    assert orphan["actor"] == "rev" and orphan["reason"] == gone["reason"]
    assert orphan["subject_label"]                         # named in prose, not by hash


# --------------------------------------------------------------------------------------------
# the counters, and the tabs that must keep counting what they select
# --------------------------------------------------------------------------------------------

def test_the_counters_are_derived_and_partition_the_queue():
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90),
            _unmapped("Unplaceable A", 5), _unmapped("Unplaceable B", 6),
            _lowconf("A shaky match", "bs_cash", 7)]
    plain = _build_review(rows, "doc.pdf", "en")
    judged = _accepted(next(c for c in plain["checks"] if c["type"] == "unmapped"))
    review = _build_review(rows, "doc.pdf", "en", judgements=[judged])

    s = review["summary"]
    assert s["accepted"] == 1
    assert s["open"] + s["accepted"] == len(review["checks"])
    assert s["stale"] <= s["open"]
    assert s["open"] + s["accepted"] == review["tabs"][0]["count"]
    # Accepted is EXCLUDED from open and the card is still on screen, still in its tab.
    assert all(c["status"] == "accepted" or c["judgement"] is None for c in review["checks"])
    assert s["open"] == sum(1 for c in review["checks"] if c["status"] in ("open", "stale"))


def test_every_tab_still_counts_what_it_selects_with_accepted_findings_present():
    """The invariant tests/test_review_checks.py pins, re-checked once judgements exist: no
    `statuses` dimension was added, so the counts cannot drift from the rows a click produces."""
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90),
            _unmapped("Unplaceable", 5), _lowconf("A shaky match", "bs_cash", 7)]
    plain = _build_review(rows, "doc.pdf", "en")
    judgements = [_accepted(c) for c in plain["checks"][:2]]
    review = _build_review(rows, "doc.pdf", "en", judgements=judgements)

    assert sum(1 for c in review["checks"] if c["status"] == "accepted") == 2
    for tab in review["tabs"]:
        selected = (review["checks"] if tab["types"] is None
                    else [c for c in review["checks"] if c["type"] in tab["types"]])
        assert tab["count"] == len(selected), tab["label"]
    buckets = [t for t in review["tabs"] if t["types"] is not None]
    assert sum(t["count"] for t in buckets) == len(review["checks"])


def test_the_queue_is_served_stale_first_then_open_then_accepted():
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90),
            _unmapped("Unplaceable A", 5), _unmapped("Unplaceable B", 6)]
    plain = _build_review(rows, "doc.pdf", "en")
    accept = _accepted(next(c for c in plain["checks"] if c["subject"].get("label")
                            == "unplaceable a"))
    stale = _accepted(plain["checks"][0])
    stale["evidence"] = {**stale["evidence"], "diff": 999_999}

    review = _build_review(rows, "doc.pdf", "en", judgements=[accept, stale])
    order = [c["status"] for c in review["checks"]]
    assert order == sorted(order, key=lambda st: {"stale": 0, "open": 1, "accepted": 2}[st])
    assert order[0] == "stale" and order[-1] == "accepted"


# --------------------------------------------------------------------------------------------
# guards: a check class whose evidence used to be five constants
# --------------------------------------------------------------------------------------------

def _guard_row(predicate: str, violations: list[dict], keys: list[str],
               rule_text: str = "") -> dict:
    """A failed guard as ``_guard_slot`` REALLY writes it (structural_checks.py::_guard_slot).

    ``keys`` is the operand set the rulebook SENTENCE names — ``details.guard_keys`` — and
    ``target``/``components`` are derived from it and from the violations exactly the way the
    producer derives them, per predicate. That distinction is the whole point of the fixture:
    ``target`` is ``violations[0]["key"]`` under sign_expectation and the loaded component subset
    under mutually_exclusive, so a fixture that hardcoded ``target=keys[0]`` was building a shape
    the evaluator never emits — and the parametrized assertion below then ran against nothing.
    ``violations_keys`` comes from the producer's own ``_violation_keys`` so the fixture cannot
    drift from it either.

    ``test_the_guard_fixture_is_the_shape_the_evaluator_emits`` holds this to the real evaluator.
    """
    from app.services.structural_checks import _violation_keys

    keys = list(keys)
    if predicate == "sign_expectation":
        # The sentence names no concept: the guard scans every concept with a declared sign
        # convention, so the primary is whichever violation sorted first.
        target = violations[0]["key"] if violations else ""
        others = [v["key"] for v in violations[1:]]
    elif predicate == "consolidation_eliminated":
        target, others = keys[0], []
    elif predicate == "mutually_exclusive":
        target = keys[0]
        others = list(violations[0].get("components") or []) if violations else []
    elif predicate == "equal_while_third_non_zero":
        target, others = keys[0], [keys[1], keys[2]]
    else:                                                            # equal_values
        target, others = keys[0], [keys[1]]
    return {"rule_id": f"guard:{predicate}" + (f":{keys[0]}" if keys else ""),
            "kind": "guard", "status": "fail",
            "scope_key": "consolidated/current", "expected": None, "actual": None,
            "difference": None,
            "details": {"target": target, "components": others, "op": predicate,
                        "statement": "balance_sheet", "basis": "consolidated",
                        "period_label": "current", "guard": predicate, "severity": "blocking",
                        "guard_keys": keys,
                        "precondition": "always",
                        "rule_text": rule_text or f"{predicate} must hold",
                        "violations": violations,
                        "violations_keys": sorted({k for v in violations
                                                   for k in _violation_keys(v)}),
                        "sign_suspect": None}}


# One case per guard predicate: the operands the sentence names, the violation set as run 1 found
# it, and as a mapping regression left it. Every one of these used to fingerprint
# {actual:0, expected:0, diff:0, components:{}, sign_suspect:null} — five constants no guard writes
# — so an acceptance could never go stale for ANY of them.
_GUARD_CASES = {
    # The sign guard's sentence names no concept at all (it quotes the sign conventions), which is
    # why its `keys` is empty in the real rulebook and why its `target` can only come from the
    # violations.
    "sign_expectation": (
        [],
        [{"key": "pl_finance_costs", "expected": "negative_expected", "value": "1200"}],
        [{"key": "pl_finance_costs", "expected": "negative_expected", "value": "1200"},
         {"key": "pl_tax_expense", "expected": "negative_expected", "value": "300"}],
    ),
    "consolidation_eliminated": (
        ["bs_intra_group_receivable"],
        [{"key": "bs_intra_group_receivable", "value": "5000", "basis": "consolidated"}],
        [{"key": "bs_intra_group_receivable", "value": "900000000",
          "basis": "consolidated"}],
    ),
    "mutually_exclusive": (
        ["bs_ca__others", "bs_ca__prepayments", "bs_ca__deposits"],
        [{"aggregate": "bs_ca__others", "components": ["bs_ca__prepayments"],
          "aggregate_value": "700"}],
        [{"aggregate": "bs_ca__others",
          "components": ["bs_ca__prepayments", "bs_ca__deposits"], "aggregate_value": "700"}],
    ),
    "equal_values": (
        ["bs_ca__cash", "bs_ca__bank"],
        [{"equal": ["bs_ca__cash", "bs_ca__bank"], "value": "4000"}],
        [{"equal": ["bs_ca__cash", "bs_ca__bank"], "value": "9000"}],
    ),
    "equal_while_third_non_zero": (
        ["bs_a", "bs_b", "bs_c"],
        [{"equal": ["bs_a", "bs_b"], "value": "100", "non_zero": "bs_c",
          "non_zero_value": "10"}],
        [{"equal": ["bs_a", "bs_b"], "value": "100", "non_zero": "bs_c",
          "non_zero_value": "40"}],
    ),
}


@pytest.mark.parametrize("predicate", sorted(_GUARD_CASES))
def test_an_acceptance_on_a_guard_goes_stale_when_the_violation_set_moves(predicate):
    """The stale mechanism has to be reachable for guards too.

    A guard sets no expected/actual/difference, so fingerprinting those three (plus
    component_values and sign_suspect) fingerprinted five constants: the same BLOCKING guard
    failing on nine keys instead of one came back 'accepted' by a person who examined one, and the
    eight they never saw dropped out of summary.open and out of the red counter. The digest is now
    taken over the violation set the card prints, so it moves when the set moves.
    """
    from app.api.routes.documents import _build_review

    keys, before, after_rows = _GUARD_CASES[predicate]
    first = _build_review([], "d.pdf", "en", [], [_guard_row(predicate, before, keys)])
    card = first["checks"][0]
    assert card["type"] == "structural"
    # Nothing on the card is a number the guard did not derive: no computed zero difference.
    assert card["delta"] == "—"
    assert card["evidence"]["violation_count"] == len(before)
    assert not {"actual", "expected", "diff"} & set(card["evidence"])

    grown = _build_review([], "d.pdf", "en", [], [_guard_row(predicate, after_rows, keys)],
                          judgements=[_accepted(card)])
    same = _by_key(grown, card["subject_key"])
    assert same["status"] == "stale" and same["status"] != "accepted"
    assert same["judgement"]["changed"]                 # and it names what moved
    assert grown["summary"]["open"] == 1 and grown["summary"]["accepted"] == 0
    # STALE, NOT ORPHANED. The subject must not move when the violation set does, or this same
    # regression would be reported as "the finding was corrected, or is no longer raised" while a
    # BLOCKING guard fails on more lines than when it was accepted.
    assert grown["judgements"]["orphaned"] == []


def test_a_guard_card_prints_the_violation_set_it_is_fingerprinted_on():
    """Everything in a guard's evidence is on its card, and everything on its card is in its
    evidence — the rule this codebase keeps: display it first, then fingerprint it."""
    from app.api.routes.documents import _build_review

    violations = [{"key": "pl_finance_costs", "expected": "negative_expected", "value": "1200"},
                  {"key": "pl_tax_expense", "expected": "negative_expected", "value": "300"}]
    # `[]` because the shipped sign guard's sentence names no concept — see `_guard_row`.
    card = _build_review([], "d.pdf", "en", [],
                         [_guard_row("sign_expectation", violations, [])])["checks"][0]
    printed = {row[0]: row[1] for row in card["calc"]}
    assert printed["Lines in violation"] == "pl_finance_costs, pl_tax_expense"
    assert printed["Violations"] == "2"
    assert printed["pl_finance_costs (negative_expected)"] == "1,200"
    assert card["evidence"]["violations"] == {
        "pl_finance_costs (negative_expected)": "1,200",
        "pl_tax_expense (negative_expected)": "300"}
    assert card["evidence"]["violations_keys"] == "pl_finance_costs, pl_tax_expense"


def test_two_rulebook_identities_sharing_an_authored_id_are_two_findings():
    """The arithmetic relation held the same rule as the guard, one field along.

    A ``validation.identities`` id is free text and the loader accepts a repeat (verified here), so
    two entries asserting different decompositions of one target produced ONE rule_id — and with a
    subject of {rule_id, scope, target} that made them one identity. Run 1 fails entry A, a reviewer
    accepts; run 2 A holds and B fails, and B was served 'stale' under A's reviewer and A's reason.
    The subject carries the operands the relation asserts, so the two cannot be conflated; the id is
    disambiguated too, because the card's DOM id and coverage's per-rule alarms have only the id.
    """
    from app.api.routes.documents import _build_review

    dup = [{"id": "dup", "severity": "blocking",
            "expr": "bs_total_assets = bs_current_assets__total_current_assets"},
           {"id": "dup", "severity": "blocking",
            "expr": "bs_total_assets = bs_non_current_assets__total_non_current_assets"}]
    both = {"bs_total_assets": 100,
            "bs_current_assets__total_current_assets": 40,
            "bs_non_current_assets__total_non_current_assets": 60}
    rows = [r for r in _real_structural_rows(both, identities=dup)
            if r["status"] == "fail" and r["kind"] == "ontology_identity"]
    assert len(rows) == 2
    assert len({r["rule_id"] for r in rows}) == 2                    # ids no longer collide

    cards = _build_review([], "d.pdf", "en", [], rows)["checks"]
    assert len({c["subject_key"] for c in cards}) == 2
    assert not any(c["conflict"] or c["ambiguous"] for c in cards)
    # …and what tells them apart is what each one asserts.
    assert {tuple(c["subject"]["components"]) for c in cards} == {
        ("bs_current_assets__total_current_assets",),
        ("bs_non_current_assets__total_non_current_assets",)}

    accept_a = _accepted(next(c for c in cards if c["subject"]["components"]
                              == ["bs_current_assets__total_current_assets"]), actor="rev-a")
    run_2 = [r for r in _real_structural_rows({**both,
                                               "bs_current_assets__total_current_assets": 100},
                                              identities=dup)
             if r["status"] == "fail" and r["kind"] == "ontology_identity"]
    assert len(run_2) == 1
    served = _build_review([], "d.pdf", "en", [], run_2, judgements=[accept_a])
    only = served["checks"][0]
    assert only["subject"]["components"] == ["bs_non_current_assets__total_non_current_assets"]
    assert only["status"] == "open" and only["judgement"] is None
    assert "rev-a" not in str(only)
    assert [o["actor"] for o in served["judgements"]["orphaned"]] == ["rev-a"]


def _real_structural_rows(figures: dict, identities: list[dict] | None = None) -> list[dict]:
    """Relation results from the REAL loader and evaluator, as ``run.result["structural"]`` stores
    them. ``identities`` replaces ``validation.identities`` when given."""
    import copy
    import json
    from decimal import Decimal
    from pathlib import Path

    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.schemas.loader import load_ontology, load_template
    from app.services.structural_checks import evaluate_structure

    samples = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
    raw = json.loads((samples / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    if identities is not None:
        raw = copy.deepcopy(raw)
        raw["validation"]["identities"] = list(identities)
    ont = load_ontology(raw, resolve=True)
    template = load_template(json.loads((samples / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))
    items = []
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        items.append(li)
    return [r.model_dump(mode="json")
            for r in evaluate_structure(template, items, ontology=ont).results]


def _real_guard_rows(figures: dict, guards: list[str] | None = None) -> list[dict]:
    """Guard results produced by the REAL loader and evaluator, as ``run.result["structural"]``
    stores them, so a test can reason about the shape the pipeline actually writes.

    ``guards`` replaces ``validation.cross_concept_guards`` when given — which is how the two
    same-id sentences below are declared, exactly as an admin uploading a rulebook could.
    """
    import copy
    import json
    from decimal import Decimal
    from pathlib import Path

    from app.core.models.enums import Basis
    from app.core.models.line_item import ExtractedValue, LineItem
    from app.schemas.loader import load_ontology, load_template
    from app.services.structural_checks import evaluate_structure

    samples = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
    raw = json.loads((samples / "hkfrs_hk_china_ontology.json").read_text(encoding="utf-8"))
    if guards is not None:
        raw = copy.deepcopy(raw)
        raw["validation"]["cross_concept_guards"] = list(guards)
    ont = load_ontology(raw, resolve=True)
    template = load_template(json.loads((samples / "hkfrs_hk_china_template.json").read_text(encoding="utf-8")))

    items = []
    for key, num in figures.items():
        li = LineItem(source_label=key, canonical_key=key)
        li.set_value(ExtractedValue(value=Decimal(str(num)), value_raw=Decimal(str(num)),
                                    basis=Basis.CONSOLIDATED, period_label="current"))
        items.append(li)
    report = evaluate_structure(template, items, ontology=ont)
    return [r.model_dump(mode="json") for r in report.results if r.kind == "guard"]


def test_the_guard_fixture_is_the_shape_the_evaluator_emits():
    """A FIXTURE THAT LIES REPORTS COVERAGE THAT DOES NOT EXIST.

    ``_guard_row`` used to hardcode ``details.target = keys[0]``, and for sign_expectation — the one
    guard the shipped rulebook declares — ``_guard_slot`` sets ``target`` to
    ``violations[0]["key"]``, which appeared in none of that fixture's violations. So the
    parametrized "goes stale" assertion above ran against a shape the producer never writes, and
    could not have caught the subject-moves-with-the-figures defect it was written for. This test is
    what keeps the fixture honest: the real evaluator's fields, compared against the fixture's.
    """
    real = _real_guard_rows({
        # negative_expected concepts carrying positive figures → two sign violations
        "pl_expenses__cost_of_goods_sold": 600,
        "pl_expenses__selling_and_marketing_expenses": 300,
    })
    sign = next(r for r in real if r["details"]["guard"] == "sign_expectation")
    assert sign["status"] == "fail"
    violations = sign["details"]["violations"]
    assert len(violations) == 2
    # The producer's own rule: the primary is the FIRST VIOLATION's key, not a declared operand.
    assert sign["details"]["target"] == violations[0]["key"]
    assert sign["details"]["guard_keys"] == []          # the sentence names no concept
    assert sign["expected"] is None and sign["actual"] is None and sign["difference"] is None

    mine = _guard_row("sign_expectation", violations, sign["details"]["guard_keys"])
    assert set(mine["details"]) == set(sign["details"]), "fixture and producer disagree on fields"
    for field in ("target", "components", "guard_keys", "violations", "violations_keys",
                  "sign_suspect", "guard", "op"):
        assert mine["details"][field] == sign["details"][field], field


def test_a_guards_subject_does_not_move_when_one_more_line_violates_it():
    """I3, on the check type where the shipped rulebook can actually reach it.

    ``details.target`` is DERIVED from the violation set for sign_expectation, and it was in the
    subject. So a second violated key that sorts BEFORE the first changed the SUBJECT: the stored
    acceptance detached and the screen reported it under "the findings they were recorded against
    were corrected, or are no longer raised" — while the blocking guard was failing on twice as many
    lines as when it was accepted. Evidence moving means stale; only the claim changing may move the
    subject.
    """
    from app.api.routes.documents import _build_review

    one = [{"key": "pl_finance_costs", "expected": "negative_expected", "value": "1200"}]
    # Sorted BEFORE pl_finance_costs, exactly as `_guard_slot` sorts its scan, so `details.target`
    # changes from pl_finance_costs to bs_ca__deposits between the two runs.
    two = [{"key": "bs_ca__deposits", "expected": "positive_expected", "value": "-90000"}, *one]

    card = _build_review([], "d.pdf", "en", [], [_guard_row("sign_expectation", one, [])])[
        "checks"][0]
    after = _build_review([], "d.pdf", "en", [], [_guard_row("sign_expectation", two, [])],
                          judgements=[_accepted(card)])
    grown = after["checks"][0]

    assert _guard_row("sign_expectation", two, [])["details"]["target"] \
        != _guard_row("sign_expectation", one, [])["details"]["target"]   # the defect's trigger
    assert grown["subject_key"] == card["subject_key"]                    # …moves no subject
    assert grown["status"] == "stale"
    assert after["judgements"]["orphaned"] == []
    assert after["summary"]["open"] == 1 and after["summary"]["accepted"] == 0
    # And nothing violation-derived is in the subject at all — nor anything POSITIONAL: `rule_id` is
    # gone too, because `structural_checks._unique` disambiguates two sentences sharing a base id by
    # appending an ORDINAL, and a byte-identical still-failing sentence renumbered from `…#2` to `…#1`
    # by an unrelated deletion would move this subject and orphan the acceptance.
    assert set(card["subject"]) == {"k", "scope", "predicate", "asserts", "rule"}
    assert "target" not in card["subject"] and "rule_id" not in card["subject"]


def test_two_rulebook_guards_sharing_a_predicate_and_first_key_are_two_findings():
    """Reproduced with the real loader: two ``equal to`` sentences that both start with the same
    concept resolve to one base id, because the id is ``guard:{predicate}:{keys[0]}`` and ontologies
    are admin-uploadable.

    Before this, run 1 failing sentence A and run 2 failing sentence B served B as 'stale' carrying
    A's reviewer, A's reason and A's figures — on a BLOCKING finding nobody had examined, at rank 0
    — and when both failed in one run the pair became a permanent conflict, so NEITHER could ever be
    accepted. The id is now unique per sentence, and the subject carries the sentence and its
    operands, so neither the screen nor the judgement layer can conflate them.
    """
    from app.api.routes.documents import _build_review

    a = ("bs_equity__non_controlling_interests equal to "
         "pl_profit_attributable_to__non_controlling_interests — the balance-versus-flow confusion.")
    b = ("bs_equity__non_controlling_interests equal to bs_equity__general_reserve — two unrelated "
         "captions cannot carry one figure.")
    both_fail = {"bs_equity__non_controlling_interests": 4000,
                 "pl_profit_attributable_to__non_controlling_interests": 4000,
                 "bs_equity__general_reserve": 4000}

    rows = [r for r in _real_guard_rows(both_fail, guards=[a, b]) if r["status"] == "fail"]
    assert len(rows) == 2, [r["rule_id"] for r in rows]
    # The id no longer collides — the card DOM id and the coverage report's per-rule alarms key on
    # it, and one id over two guards is one card for two assertions.
    assert len({r["rule_id"] for r in rows}) == 2
    cards = _build_review([], "d.pdf", "en", [], rows)["checks"]
    assert len({c["subject_key"] for c in cards}) == 2
    # Two findings, not one ambiguous pair and not a conflict: both are acceptable on their own.
    assert not any(c["conflict"] or c["ambiguous"] for c in cards)
    assert {c["subject"]["rule"] for c in cards} == {judgement_norm(a), judgement_norm(b)}

    # Run 2: sentence A now holds, B still fails on a different figure. B is nobody's finding yet.
    accepted_a = _accepted(next(c for c in cards
                                if c["subject"]["rule"] == judgement_norm(a)), actor="rev-a")
    second = {**both_fail, "pl_profit_attributable_to__non_controlling_interests": 90000}
    run_2 = [r for r in _real_guard_rows(second, guards=[a, b]) if r["status"] == "fail"]
    assert len(run_2) == 1
    served = _build_review([], "d.pdf", "en", [], run_2, judgements=[accepted_a])
    only = served["checks"][0]
    assert only["subject"]["rule"] == judgement_norm(b)
    assert only["status"] == "open" and only["judgement"] is None
    assert "rev-a" not in str(only)
    # A's acceptance is reported as orphaned — A really is no longer raised — and counts nowhere.
    assert [o["actor"] for o in served["judgements"]["orphaned"]] == ["rev-a"]
    assert served["summary"] == {"open": 1, "accepted": 0, "stale": 0, "conflict": 0,
                                "passed": served["summary"]["passed"]}


def judgement_norm(s: str) -> str:
    from app.services import judgement

    return judgement.norm(s)


def test_a_guard_card_and_a_conflict_note_are_translated_in_every_locale():
    """Both are card vocabulary, and an untranslated card is a zh/ar/fr reader shown English —
    the same defect the coverage band's own translation test pins."""
    from app.api.routes.documents import _build_review

    guard = [_guard_row("sign_expectation",
                        [{"key": "pl_finance_costs", "expected": "negative_expected",
                          "value": "1200"}], [])]
    conflicting = [_unmapped("Others", 1234, _prov(page=0)),
                   _unmapped("Others", 5678, _prov(page=0))]
    english = _build_review([], "d.pdf", "en", [], guard)["checks"][0]
    en_note = _build_review(conflicting, "d.pdf", "en")["checks"][0]["conflict_note"]

    for locale in ("zh", "ar", "fr"):
        card = _build_review([], "d.pdf", locale, [], guard)["checks"][0]
        assert card["title"] != english["title"], locale
        assert card["fix"] != english["fix"], locale
        # The row LABELS translate; the canonical keys and the figures do not.
        assert [r[0] for r in card["calc"][:3]] != [r[0] for r in english["calc"][:3]], locale
        assert [r[1] for r in card["calc"][2:]] == [r[1] for r in english["calc"][2:]], locale
        note = _build_review(conflicting, "d.pdf", locale)["checks"][0]["conflict_note"]
        assert note and note != en_note, locale


# --------------------------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------------------------

def _extracted_with_findings(client) -> str:
    """A real document extracted with NO template, so every line lands unmapped and the review
    queue has real, content-keyed findings to judge."""
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("bs.pdf", make_unmapped_row_pdf(),
                                         "application/pdf")}).json()["id"]
    client.post(f"/api/v1/documents/{doc_id}/extractions", json={})
    for _ in range(200):
        r = client.get(f"/api/v1/documents/{doc_id}/run")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    assert client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"]
    return doc_id


def test_accept_then_withdraw_then_accept_keeps_one_row_and_appends_history(client):
    from app.db.base import SessionLocal
    from app.db.models import ReviewJudgement

    doc_id = _extracted_with_findings(client)
    check = client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"][0]
    key, digest = check["subject_key"], check["evidence_digest"]
    body = {"subject_key": key, "evidence_digest": digest, "reason": "Traced to p.1; it stands."}

    assert client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                       json=body).json() == {"ok": True, "subject_key": key,
                                             "status": "accepted"}
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    card = next(c for c in after["checks"] if c["subject_key"] == key)
    assert card["status"] == "accepted"
    assert card["judgement"]["actor"] == "admin" and card["judgement"]["actor_role"] == "admin"
    assert card["judgement"]["reason"] == body["reason"]
    assert card["judgement"]["at"] and card["judgement"]["run_id"]
    assert card["judgement"]["accepted_rows"] and card["judgement"]["changed"] == []
    assert after["summary"]["accepted"] == 1

    assert client.delete(
        f"/api/v1/documents/{doc_id}/review/judgements/{key}").json()["withdrawn"] is True
    back = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert next(c for c in back["checks"] if c["subject_key"] == key)["status"] == "open"
    assert back["summary"]["accepted"] == 0

    assert client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                       json=body).status_code == 200

    with SessionLocal() as session:
        rows = session.query(ReviewJudgement).filter(
            ReviewJudgement.document_id == doc_id,
            ReviewJudgement.subject_key == key).all()
        assert len(rows) == 1                       # never rival rows a reader must date-sort
        assert rows[0].verdict == "accepted"
        # accepted → withdrawn → accepted: two PRIOR states recorded, newest last.
        assert [h["verdict"] for h in rows[0].history] == ["accepted", "withdrawn"]
        assert all(h["at"] and "evidence" in h for h in rows[0].history)


def _doc_with_rows(rows: list[dict], filename: str = "conflict.pdf") -> str:
    """A document with a stored succeeded run over `rows`, straight into the DB.

    The rows a conflict needs — two lines captioned identically on one page whose provenance
    reports no geometry — are not what the sample PDF yields, and the endpoint reads the run out
    of the database, so seeding it is the honest way to reach that state.
    """
    import uuid

    from app.db.base import SessionLocal, init_db
    from app.db.models import Document, ExtractionRun

    init_db()
    with SessionLocal() as session:
        doc = Document(filename=filename, fmt="pdf", byte_size=1, page_count=1,
                       content_hash=uuid.uuid4().hex, object_key="k", owner="admin",
                       status="extracted")
        session.add(doc)
        session.flush()
        session.add(ExtractionRun(document_id=doc.id, status="succeeded", options={},
                                  result={"rows": rows, "filename": filename}))
        session.commit()
        return doc.id


def test_the_post_refuses_a_subject_two_findings_disagree_about_rather_than_409ing(client):
    """The endpoint half of the conflict, and the second face of the same defect.

    Resolution used to be ``next(c for c in checks if c["subject_key"] == posted)`` — subject_key
    ALONE — so with two findings on one subject the posted digest was compared against whichever
    card sorted first. Accepting the FIRST silently re-labelled the second as "stale" under the
    accepting reviewer's name; accepting the SECOND returned 409 evidence_changed quoting the
    other line's figures, forever, because every retry re-read the same first match.

    Both are now refused with a code that says what is actually wrong, and neither figure set is
    quoted as if it were the card's own.
    """
    doc_id = _doc_with_rows([_unmapped("Others", 1234, _prov(page=0)),
                             _unmapped("Others", 5678, _prov(page=0))])
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    cards = review["checks"]
    assert len(cards) == 2 and all(c["status"] == "conflict" for c in cards)

    for card in cards:                      # neither card is acceptable, and neither is a 409
        r = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                        json={"subject_key": card["subject_key"],
                              "evidence_digest": card["evidence_digest"],
                              "reason": "Looked at it; it stands."})
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "subject_conflict"
        assert detail["count"] == 2 and len(detail["evidence_digests"]) == 2
        assert detail["note"]
        # NOT the old answer: no claim that the figures changed while the card was open.
        assert "evidence_changed" not in str(detail)

    # And nothing was written by either attempt, so no name sits against any of these figures.
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert after["summary"]["accepted"] == 0 and after["summary"]["stale"] == 0
    assert after["summary"]["conflict"] == 2
    assert all(c["judgement"] is None for c in after["checks"])


def test_the_history_records_when_the_verdict_was_made_not_when_it_was_changed(client):
    """An acceptance made at 10:00 and withdrawn at 15:00 was recorded as made at 15:00 — and
    10:00 then existed in no column, because ``updated_at`` is bumped by the same write. Every
    history entry carried its SUCCESSOR's timestamp. Nothing serves history yet, so no screen was
    wrong; the audit trail being accumulated was."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.db.base import SessionLocal
    from app.db.models import ReviewJudgement

    doc_id = _extracted_with_findings(client)
    check = client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"][0]
    key = check["subject_key"]
    assert client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                       json={"subject_key": key, "evidence_digest": check["evidence_digest"],
                             "reason": "Traced to p.1; it stands."}).status_code == 200

    # Age the acceptance by an hour, exactly as the clock would have: the verdict was made then.
    accepted_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None,
                                                                           microsecond=0)
    with SessionLocal() as session:
        session.execute(update(ReviewJudgement)
                        .where(ReviewJudgement.document_id == doc_id,
                               ReviewJudgement.subject_key == key)
                        .values(created_at=accepted_at, updated_at=accepted_at))
        session.commit()

    assert client.delete(
        f"/api/v1/documents/{doc_id}/review/judgements/{key}").status_code == 200
    with SessionLocal() as session:
        row = session.query(ReviewJudgement).filter(
            ReviewJudgement.document_id == doc_id,
            ReviewJudgement.subject_key == key).one()
        entry = row.history[-1]
        assert entry["verdict"] == "accepted"
        # The moment the acceptance was made, recovered from the row itself — not the withdrawal's.
        assert entry["at"] == accepted_at.isoformat(timespec="seconds")
        assert entry["at"] < (row.updated_at or row.created_at).isoformat(timespec="seconds")


def test_two_reviewers_accepting_at_the_same_instant_lose_neither_reason(client):
    """FINDING G, on the real constraint rather than a mocked one.

    REVIEWER and ADMIN both hold REVIEW_RESOLVE, so two people can POST for one subject at the
    same instant: both SELECTs return None and the loser's INSERT violates uq_judgement_subject.
    The IntegrityError was uncaught — a 500, and the reviewer's typed reason gone. The winner's row
    is committed here from a SECOND connection at the moment the loser's INSERT is issued, which
    is exactly the interleaving, and the constraint really does fire.
    """
    from sqlalchemy import event

    from app.db.base import SessionLocal, engine
    from app.db.models import ReviewJudgement

    doc_id = _extracted_with_findings(client)
    check = client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"][0]
    key = check["subject_key"]
    fired: list[bool] = []

    def _winner_commits_first(conn, cursor, statement, parameters, context, executemany):
        if fired or "review_judgements" not in statement.lower() \
                or not statement.lstrip().lower().startswith("insert"):
            return
        fired.append(True)
        with SessionLocal() as other:
            other.add(ReviewJudgement(
                tenant_id="default", document_id=doc_id, subject_key=key,
                subject=check["subject"], evidence=check["evidence"], verdict="accepted",
                reason="I got here first.", actor="reviewer", actor_role="reviewer",
                run_id="", history=[]))
            other.commit()

    event.listen(engine, "before_cursor_execute", _winner_commits_first)
    try:
        r = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                        json={"subject_key": key, "evidence_digest": check["evidence_digest"],
                              "reason": "And I typed mine at the same second."})
    finally:
        event.remove(engine, "before_cursor_execute", _winner_commits_first)

    assert fired, "the interleaving never happened, so this test proved nothing"
    assert r.status_code == 200, r.text                     # not a 500
    with SessionLocal() as session:
        rows = session.query(ReviewJudgement).filter(
            ReviewJudgement.document_id == doc_id,
            ReviewJudgement.subject_key == key).all()
        assert len(rows) == 1                               # the constraint held
        # The loser's reason is recorded, and the winner's is kept in history rather than lost.
        assert rows[0].reason == "And I typed mine at the same second."
        assert [h["reason"] for h in rows[0].history] == ["I got here first."]


def test_the_post_refuses_a_stale_digest_an_unknown_subject_and_a_blank_reason(client):
    doc_id = _extracted_with_findings(client)
    check = client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"][0]

    stale = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                        json={"subject_key": check["subject_key"],
                              "evidence_digest": "0" * 64, "reason": "fine"})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["error"] == "evidence_changed"
    assert detail["current"]["evidence_digest"] == check["evidence_digest"]
    assert detail["current"]["status"] == "open" and detail["current"]["accepted_rows"]

    missing = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                          json={"subject_key": "f" * 64,
                                "evidence_digest": check["evidence_digest"], "reason": "fine"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == {"error": "finding_not_found", "subject_key": "f" * 64}

    blank = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                        json={"subject_key": check["subject_key"],
                              "evidence_digest": check["evidence_digest"], "reason": "   "})
    assert blank.status_code == 422
    assert blank.json()["detail"] == {"error": "reason_required"}

    # Nothing was written by any of the three.
    assert client.get(f"/api/v1/documents/{doc_id}/review").json()["summary"]["accepted"] == 0
    assert client.delete(
        f"/api/v1/documents/{doc_id}/review/judgements/{check['subject_key']}"
    ).json()["detail"] == {"error": "no_judgement"}


def test_judging_a_document_with_no_run_is_a_404(client):
    doc_id = client.post("/api/v1/documents",
                         files={"file": ("norun.pdf", make_unmapped_row_pdf(),
                                         "application/pdf")}).json()["id"]
    r = client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                    json={"subject_key": "a" * 64, "evidence_digest": "b" * 64, "reason": "x"})
    assert r.status_code == 404 and r.json()["detail"] == {"error": "no_run"}


def test_only_a_reviewer_may_judge_while_an_analyst_keeps_the_edit_they_are_entitled_to(
        anon_client, auth):
    """Each control gets its own gate. The analyst holds EXTRACTION_EDIT and not REVIEW_RESOLVE
    (security/rbac.py), so accept/withdraw 403 while the PATCH the flip-sign fix lands on does
    not — gating the fix on review:resolve would deny an analyst the one mechanical correction
    the role map entitles them to."""
    up = anon_client.post("/api/v1/documents",
                          files={"file": ("rbac.pdf", make_unmapped_row_pdf(), "application/pdf")},
                          headers=auth("analyst"))
    doc_id = up.json()["id"]
    # Extracted against the real rulebook and template, so the PATCH below lands on a concept THIS
    # RUN carries. It used to run with no template at all and fall back to the literal
    # "bs_current_assets__inventories", which the route accepted only because `_template_for_run`
    # substituted the newest seeded template for the one the run never named — the edit was being
    # validated against a template the analyst never chose (finding E).
    # Line items is the single configuration engine: ``GET /ontologies`` and the run's
    # ``ontology_version_id`` pin are gone with the ontology store. The version list is read as
    # ADMIN because configuration is an admin surface (``CONFIG_LINE_ITEMS``) — the ontology list
    # was not gated that way. Nothing under test moves: the analyst still uploads, starts the run
    # and makes the edit below.
    from app.sample.reference import shipped_line_items_key

    cfg = next(c for c in anon_client.get("/api/v1/line-items/versions",
                                          headers=auth("admin")).json()
               if c["line_items_key"] == shipped_line_items_key())
    tpl = next(t for t in anon_client.get("/api/v1/templates", headers=auth("analyst")).json()
               if t["template_key"] == cfg["target_template_key"])
    anon_client.post(f"/api/v1/documents/{doc_id}/extractions",
                     json={"line_item_version_id": cfg["id"], "template_version_id": tpl["id"]},
                     headers=auth("analyst"))
    for _ in range(200):
        r = anon_client.get(f"/api/v1/documents/{doc_id}/run", headers=auth("analyst"))
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.05)
    review = anon_client.get(f"/api/v1/documents/{doc_id}/review",
                             headers=auth("analyst")).json()
    check = review["checks"][0]
    body = {"subject_key": check["subject_key"], "evidence_digest": check["evidence_digest"],
            "reason": "Looked at it."}

    assert anon_client.post(f"/api/v1/documents/{doc_id}/review/judgements", json=body,
                            headers=auth("analyst")).status_code == 403
    assert anon_client.delete(
        f"/api/v1/documents/{doc_id}/review/judgements/{check['subject_key']}",
        headers=auth("analyst")).status_code == 403
    assert anon_client.post(f"/api/v1/documents/{doc_id}/review/judgements", json=body,
                            headers=auth("reviewer")).status_code == 200

    # …and the analyst can still edit a figure, which is what the flip-sign button does.
    rows = anon_client.get(f"/api/v1/documents/{doc_id}/run",
                           headers=auth("analyst")).json()["result"]["rows"]
    key = next(r["canonical_key"] for r in rows if r.get("canonical_key"))
    patched = anon_client.patch(f"/api/v1/documents/{doc_id}/line-items/{key}",
                                json={"value": -100, "basis": "consolidated",
                                      "period": "current", "comment": "sign"},
                                headers=auth("analyst"))
    assert patched.status_code == 200, patched.text


def test_deleting_a_document_removes_its_judgements(client):
    from app.db.base import SessionLocal
    from app.db.models import ReviewJudgement

    doc_id = _extracted_with_findings(client)
    check = client.get(f"/api/v1/documents/{doc_id}/review").json()["checks"][0]
    client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                json={"subject_key": check["subject_key"],
                      "evidence_digest": check["evidence_digest"], "reason": "fine"})
    with SessionLocal() as session:
        assert session.query(ReviewJudgement).filter(
            ReviewJudgement.document_id == doc_id).count() == 1

    assert client.delete(f"/api/v1/documents/{doc_id}").status_code == 204
    with SessionLocal() as session:
        assert session.query(ReviewJudgement).filter(
            ReviewJudgement.document_id == doc_id).count() == 0


def test_the_sample_route_offers_no_judgement_and_no_fix(client):
    """The demo path serves the same TOTAL shape, with `subject_key: null` saying outright that
    these findings cannot be judged — so the two dead buttons disappear there too rather than
    being rendered and inert."""
    review = client.get("/api/v1/projects/demo/review").json()
    assert review["checks"]
    for c in review["checks"]:
        assert c["subject_key"] is None and c["fix_action"] is None
        assert c["status"] == "open" and c["judgement"] is None
        assert c["ambiguous"] is False and c["ambiguous_count"] == 0
        assert c["inputs_edited"] is False and c["inputs_edited_note"] == ""
    assert review["run_id"] == "" and review["judgements"] == {"orphaned": []}
    assert review["coverage"] == {
        "available": False, "reason": "sample",
        "reason_label": review["coverage"]["reason_label"]}
    assert review["coverage"]["reason_label"]
    # The two new counters are COUNTED from the checks served with them, not written as zeros.
    assert review["summary"]["accepted"] == sum(
        1 for c in review["checks"] if c["status"] == "accepted")
    assert review["summary"]["open"] == len(review["checks"])


def test_commentary_stops_counting_a_finding_a_human_accepted(client):
    """`build_commentary_from_rows` reads summary.open, so an accepted finding must drop out of
    the prose's count too. That the assessment improves is correct: a named person vouched, and
    the reason and actor on the judgement row are the record."""
    doc_id = _extracted_with_findings(client)
    review = client.get(f"/api/v1/documents/{doc_id}/review").json()
    before = review["summary"]["open"]
    assert before >= 1

    for check in review["checks"]:
        client.post(f"/api/v1/documents/{doc_id}/review/judgements",
                    json={"subject_key": check["subject_key"],
                          "evidence_digest": check["evidence_digest"],
                          "reason": "Confirmed against the page."})
    after = client.get(f"/api/v1/documents/{doc_id}/review").json()
    assert after["summary"]["open"] == 0 and after["summary"]["accepted"] == before
    # The commentary route builds the same review, so it reads the same zero.
    assert client.get(f"/api/v1/documents/{doc_id}/commentary").status_code == 200


def test_the_labels_beside_an_accepted_figure_are_translated():
    """`accepted_rows` is what the screen shows under a judgement, in the same two-column shape as
    a check's `calc`. An untranslated label there is English on a zh/ar/fr screen — which is how
    several of the live check-card labels were reaching those readers before this table gained
    them."""
    from app.api.routes.documents import _build_review

    rows = [_row("bs_total_assets", 100), _row("bs_total_equity_and_liabilities", 90)]
    bal = _build_review(rows, "doc.pdf", "en")["checks"][0]
    judged = [_accepted(bal)]

    english = _by_key(_build_review(rows, "doc.pdf", "en", judgements=judged),
                      bal["subject_key"])["judgement"]["accepted_rows"]
    assert english == [["Total assets", "100"], ["Total equity and liabilities", "90"],
                       ["Difference", "10"],
                       # `derived` is on the card, so it is part of what was judged.
                       ["Totals derived from the section subtotals", "No"]]
    for locale in ("zh", "ar", "fr"):
        got = _by_key(_build_review(rows, "doc.pdf", locale, judgements=judged),
                      bal["subject_key"])["judgement"]["accepted_rows"]
        # Same rows, same server-formatted figures, every label translated.
        assert [r[1] for r in got][:3] == [r[1] for r in english][:3]
        assert all(g[0] != e[0] for g, e in zip(got, english)), locale


# --------------------------------------------------------------------------------------------
# R1: nothing figure-derived may decide whether a card EXISTS
# --------------------------------------------------------------------------------------------

# A coverage block shaped as the presenter serves it, so `failed_reported_elsewhere` is filled in.
_COV_BLOCK = {"available": True, "aggregate": {}, "statements": [], "skips": [], "alarms": [],
              "run_id": "r", "engine_version": "0"}


def _real_review(figures: dict, judgements=None) -> dict:
    """One review payload whose ROWS and STRUCTURAL results come from the same figures.

    The structural rows are produced by the real loader + evaluator over the shipped
    shipped rulebook and its template (`_real_structural_rows`), so the guard results
    carry the fields `_guard_slot` really writes — including a ``details.target`` derived from the
    violations. A hand-built RuleResult cannot reproduce this defect: it is the producer's own
    derivation of `target` that collides.
    """
    from app.api.routes.documents import _build_review

    rows = [_row(k, v) for k, v in figures.items()]
    structural = _real_structural_rows(figures)
    review = _build_review(rows, "d.pdf", "en", [], structural, judgements=judgements,
                           coverage_block=dict(_COV_BLOCK))
    return {"rows": rows, "structural": structural, "review": review}


def _guard_card(review: dict) -> dict:
    return next(c for c in review["checks"] if c["subject"].get("k") == "guard")


def test_a_failed_guard_is_not_dropped_when_its_violation_lands_on_a_covered_target():
    """FINDING 1, on the shipped rulebook: a BLOCKING guard failing on two lines and showing no card.

    ``_structural_checks`` tested ``details.target in covered`` BEFORE the guard branch, and for
    sign_expectation ``_guard_slot`` sets ``details.target = violations[0]["key"]`` — the
    alphabetically first VIOLATING key, derived from the figures. So a run that mis-signed one more
    line could move a guard's target onto ``bs_total_assets``, which the balance card owns and has
    already put in ``covered``, and the entire guard card vanished from the queue: the reviewer's
    acceptance was reported as ORPHANED under "corrected, or no longer raised", and
    ``failed_reported_elsewhere`` counted the guard as reported elsewhere while nothing reported it.

    Round 3 removed ``target`` from the guard SUBJECT and left it deciding whether the card exists at
    all, which is the same I3 confusion through emission — hence R1.
    """
    # Run 1: total equity and liabilities is mis-signed. It sorts AFTER bs_total_assets, so the
    # guard's derived target is that key and nothing else claims it.
    run_1 = _real_review({"bs_total_assets": 5000, "bs_total_equity_and_liabilities": -9000})
    sign_1 = next(r for r in run_1["structural"]
                  if r["details"].get("guard") == "sign_expectation" and r["status"] == "fail")
    assert sign_1["details"]["target"] == "bs_total_equity_and_liabilities"
    # Whatever severity the rulebook declares for it — the queue serves the card either way, and this
    # records what the shipped sentence actually carries rather than asserting a wish.
    assert sign_1["details"]["severity"] in ("blocking", "warning")
    card = _guard_card(run_1["review"])
    assert card["subject"]["predicate"] == "sign_expectation"
    accepted = _accepted(card, actor="rev-a",
                         reason="Share capital genuinely negative here (treasury netting); "
                                "verified p.77")

    # Run 2 mis-signs bs_total_assets as well. `sorted(scanned)` now puts THAT key first, so the
    # producer's derived target becomes the one the balance card owns — the defect's trigger.
    run_2 = _real_review({"bs_total_assets": -5000, "bs_total_equity_and_liabilities": -9000},
                         judgements=[accepted])
    sign_2 = next(r for r in run_2["structural"]
                  if r["details"].get("guard") == "sign_expectation" and r["status"] == "fail")
    assert sign_2["details"]["target"] == "bs_total_assets"
    served = run_2["review"]
    assert "bs_total_assets" in {c["target"] for c in served["checks"]}      # …and it IS claimed

    # THE ASSERTIONS THAT FAIL WITH THE DEFECT RESTORED: the card is still on the queue, the
    # acceptance is stale rather than orphaned, and both are stated before anything else so the
    # failure names the reported symptom.
    assert [c for c in served["checks"] if c["subject"].get("k") == "guard"], \
        "the guard card is not in the queue at all while the guard is failing on two lines"
    assert served["judgements"]["orphaned"] == [], \
        "the acceptance is reported as corrected or no longer raised while the guard still fails"
    grown = _guard_card(served)
    assert grown["subject_key"] == card["subject_key"]
    assert grown["status"] == "stale"
    assert grown["evidence"]["violation_count"] == 2
    assert "bs_total_assets" in grown["evidence"]["violations_keys"]
    # …and the band's count stays true to what is actually reported: every failed relation it calls
    # "reported above" is one a served card really reports, and a guard is never one of them.
    reported = {c["target"] for c in served["checks"]}
    suppressed = [r for r in run_2["structural"]
                  if r["status"] == "fail" and not r["details"].get("guard")
                  and r["details"].get("target") in reported]
    assert served["coverage"]["failed_reported_elsewhere"] == len(suppressed)
    assert all(r["details"].get("guard") is None for r in suppressed)


def test_two_guard_sentences_are_kept_apart_without_the_id_that_numbers_them():
    """FINDING 2 (R2): a positional ordinal may not be part of an identity.

    ``structural_checks._unique`` makes ``guard:{predicate}:{keys[0]}`` unique by appending the
    sentence's 1-based ORDINAL among those sharing the base id. That ordinal is a fact about neither
    the guard nor its figures — it is POSITION. With ``rule_id`` in the subject, an admin deleting an
    unrelated sentence A renumbered a byte-identical, still-failing sentence B from ``…#2`` to ``…#1``,
    which moved subject_key and reported the acceptance as "corrected, or no longer raised".
    """
    from app.api.routes.documents import _build_review

    a = ("bs_equity__non_controlling_interests equal to "
         "pl_profit_attributable_to__non_controlling_interests — the balance-versus-flow confusion.")
    b = ("bs_equity__non_controlling_interests equal to bs_equity__general_reserve — two unrelated "
         "captions cannot carry one figure.")
    # Only B fails: NCI equals the general reserve, and does not equal the NCI profit share.
    only_b = {"bs_equity__non_controlling_interests": 4000, "bs_equity__general_reserve": 4000,
              "pl_profit_attributable_to__non_controlling_interests": 7000}

    with_a = [r for r in _real_guard_rows(only_b, guards=[a, b]) if r["status"] == "fail"]
    assert len(with_a) == 1
    assert with_a[0]["rule_id"].endswith("#2")               # numbered by position in the rulebook
    card = _build_review([], "d.pdf", "en", [], with_a)["checks"][0]
    accepted = _accepted(card, actor="rev-b", reason="Coincidence this year; checked p.104.")

    # An admin deletes sentence A. B is byte-identical and still failing — but no longer #2.
    without_a = [r for r in _real_guard_rows(only_b, guards=[b]) if r["status"] == "fail"]
    assert len(without_a) == 1
    assert without_a[0]["rule_id"] != with_a[0]["rule_id"]           # the defect's trigger
    assert without_a[0]["details"]["rule_text"] == with_a[0]["details"]["rule_text"]

    served = _build_review([], "d.pdf", "en", [], without_a, judgements=[accepted])
    still = served["checks"][0]
    # THE ASSERTIONS THAT FAIL WITH THE DEFECT RESTORED.
    assert still["subject_key"] == card["subject_key"]
    assert still["status"] == "accepted" and still["judgement"]["actor"] == "rev-b"
    assert served["judgements"]["orphaned"] == []
    # The id still travels — the DOM/expand key and coverage's per-rule alarms have only the id — it
    # just no longer decides who a stored verdict belongs to.
    assert still["id"] != card["id"] and without_a[0]["rule_id"] in still["where"]

    # …and two DIFFERENT sentences are still two findings, told apart by what each one asserts.
    both = {**only_b, "pl_profit_attributable_to__non_controlling_interests": 4000}
    pair = _build_review([], "d.pdf", "en", [],
                         [r for r in _real_guard_rows(both, guards=[a, b])
                          if r["status"] == "fail"])["checks"]
    assert len(pair) == 2 and len({c["subject_key"] for c in pair}) == 2
    assert not any(c["conflict"] or c["ambiguous"] for c in pair)


# --------------------------------------------------------------------------------------------
# I2 on low_confidence: the card's own subject matter is in its fingerprint
# --------------------------------------------------------------------------------------------

def _lowconf_card(conf, method="fuzzy", judgements=None):
    from app.api.routes.documents import _build_review

    rows = [_lowconf("Sundry balances", "bs_ca__others", 4200, conf=conf, method=method)]
    return _build_review(rows, "d.pdf", "en", judgements=judgements)


def _shipped_template() -> dict:
    import json
    from pathlib import Path

    from app.schemas.loader import load_template

    samples = Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
    return load_template(
        json.loads((samples / "hkfrs_hk_china_template.json").read_text(encoding="utf-8"))).model_dump(mode="json")


def test_a_guards_figure_derived_target_suppresses_no_other_cards_finding_either():
    """R1, on the OTHER card a guard's target could delete.

    ``_calculated_checks`` and ``_structural_checks`` are handed what the cards above them already
    assert, so one difference is not raised twice — and a guard card carries
    ``target = violations[0]["key"]``, derived from the FIGURES. So WHICH LINE IS MIS-SIGNED must not
    decide whether some other card exists, and once it did: mis-sign bs_total_equity_and_liabilities
    and that line's own card left the queue.

    A guard is excluded for two independent reasons, and both are pinned here because either alone
    would be enough to lose: ``_assertion_of`` returns None for a guard outright, and a guard's
    evidence carries no difference for it to assert even if that branch went. Making guards emit
    unconditionally made this reachable on every mis-signed run.
    """
    from app.api.routes.documents import _accounting_checks, _ASSERTED_DIFF_KEY, _assertion_of

    # A rollup whose components do not come to the printed figure, on the very line the sign guard
    # will name — so the guard card and a real relation card land on one key in one column.
    figures = {"bs_total_assets": 100, "bs_total_equity_and_liabilities": -90,
               "bs_equity__total_equity": 40, "bs_liabilities__total_liabilities": 60}
    rows = [_row(k, v) for k, v in figures.items()]
    structural = _real_structural_rows(figures)
    guard = next(r for r in structural
                 if r["details"].get("guard") == "sign_expectation" and r["status"] == "fail")
    assert guard["details"]["target"] == "bs_total_equity_and_liabilities"   # figure-derived…

    cards = _accounting_checks(rows, [], "en", structural, _shipped_template())
    kinds = {(c["subject"]["k"], c["target"]) for c in cards}
    # THE ASSERTION THAT FAILS WITH THE DEFECT RESTORED: the -190 break between that printed total
    # and its own components is still raised, beside the guard, because a guard is not a duplicate
    # of it — a guard asserts a CONDITION, not an equality.
    assert ("structural", "bs_total_equity_and_liabilities") in kinds
    assert ("guard", "bs_total_equity_and_liabilities") in kinds

    # …and both reasons a guard suppresses nothing, so neither can be removed silently.
    guard_card = next(c for c in cards if c["subject"]["k"] == "guard")
    assert _assertion_of(guard_card) is None
    assert not (set(guard_card["evidence"]) & set(_ASSERTED_DIFF_KEY.values()))

    # Declared targets still suppress on the DIFFERENCE and not on the target: the balance card
    # asserts the same 190 on bs_total_assets, and it is a different line, so both stand.
    assert ("balance", "bs_total_assets") in kinds
    assert len([c for c in cards if c["target"] == "bs_total_assets"]) == 1


# DELETED with the low-confidence card, whose confidence fingerprint they were entirely about:
#   test_a_collapsed_mapping_confidence_withdraws_the_acceptance_it_was_given
#   test_ordinary_confidence_jitter_does_not_withdraw_an_acceptance
#   test_a_method_change_is_not_jitter_and_withdraws_the_acceptance
#   test_a_confidence_band_that_crosses_re_opens_rather_than_hiding_a_move
#   test_a_finding_raised_with_no_score_at_all_carries_no_confidence_figure
#
# Each pinned a property of `_confidence_evidence`: that an acceptance made at 0.41 'fuzzy' does not
# survive a re-run at 0.02 'llm', that ordinary jitter inside a band does not churn it, that the
# METHOD is part of what was judged. Those were real lessons and the mechanism was built for them --
# but a mapping's strength is no longer a review finding, so there is no acceptance keyed to a
# confidence band for any of them to be true or false about. The mechanism went with the card; the
# reasoning is in the history of this file and of `documents._confidence_evidence`.
#
# The band-quantizing IDEA outlives them: `_prov_anchor` answers the same churn worry for geometry,
# and `judgement.q` for figures. Both are still tested.
