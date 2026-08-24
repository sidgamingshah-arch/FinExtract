"""Feedback-batch backend behaviours:

* Fuzzy mapping is a LAST RESORT — weak fuzzy no longer auto-maps into the review queue;
  a near-exact typo is still nobody's row; a rule hint is what carries an unlisted wording.
* Classification keeps a sticky NOTES section, so note-continuation pages (no "Notes to…"
  banner) are NOTES, not swept into FACE — the cause of "no notes extracted".
* Entity-name detection from the opening pages.
"""
from __future__ import annotations

import io

import pytest

from app.core.models.enums import MappingMethod
from app.schemas.ontology import OntologyDefinition, OntologyMapping
from app.services.mapping import OntologyMatcher


def _ont() -> OntologyDefinition:
    return OntologyDefinition(
        ontology_key="t", target_template_key="t",
        mappings=[
            OntologyMapping(canonical_key="assets.current.cash",
                            aliases=["Cash and cash equivalents"], keyword_hints=["cash"]),
            OntologyMapping(canonical_key="assets.current.receivables",
                            aliases=["Trade receivables", "Trade and other receivables"]),
        ],
    )


def test_weak_fuzzy_is_not_auto_mapped():
    """A caption no alias and no rule hint claims must NOT become a low-confidence
    mapping that clutters review — it is left unmapped for a human."""
    m = OntologyMatcher(_ont())
    r = m.match("Amounts recoverable from trade")   # overlaps 'trade' only → ~0.5–0.7 fuzzy
    assert r.canonical_key is None
    assert r.method == MappingMethod.UNMATCHED and r.needs_review


def test_a_near_exact_typo_is_still_not_mapped_by_resemblance():
    """There is no last-resort string match. A single typo is as unmapped as any other caption
    nothing can place, and it says so instead of asserting the concept it resembles."""
    m = OntologyMatcher(_ont())
    r = m.match("Trade recievables")                # one typo, and still nobody's row
    assert r.canonical_key is None and r.needs_review


def test_a_rule_hint_is_what_carries_an_unlisted_wording():
    m = OntologyMatcher(_ont())
    r = m.match("Cash at bank and in hand")          # keyword 'cash' → rule
    assert r.canonical_key == "assets.current.cash"
    assert r.method == MappingMethod.RULE


# --- sticky notes classification ------------------------------------------------------------
pytest.importorskip("fitz")


def _pdf(pages: list[list[str]]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    for lines in pages:
        y = h - 72
        for i, ln in enumerate(lines):
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 13 if i == 0 else 10)
            c.drawString(72, y, ln)
            y -= 20
        c.showPage()
    c.save()
    return buf.getvalue()


def test_notes_continuation_pages_stay_notes():
    from app.services.documents import analyze_document
    from app.core.models import PageKind

    data = _pdf([
        ["Balance Sheet", "Total assets 1,000", "Total equity 1,000"],
        ["Statement of Profit or Loss", "Revenue 500", "Profit for the year 90"],
        ["Notes to the Financial Statements", "1. Significant accounting policies"],
        # Continuation: numbered heading, NO "Notes to…" banner, and it mentions a face
        # phrase ("profit or loss") in the body — must still classify as NOTES.
        ["14. Cash and cash equivalents", "Cash on hand 204",
         "Fair value gains in profit or loss 12", "Balances with banks 1,000"],
    ])
    doc, _ = analyze_document(data, filename="ar.pdf")
    kinds = [p.kind for p in doc.pages]
    assert kinds[0] == PageKind.FACE
    assert kinds[1] == PageKind.FACE
    assert kinds[2] == PageKind.NOTES
    assert kinds[3] == PageKind.NOTES        # sticky — was swept into FACE before


# --- entity-name detection ------------------------------------------------------------------
def test_detect_entity_name():
    from app.services.derived import detect_entity_name

    pages = [(0, "ACME Holdings Limited\nAnnual Report 2025\n"
                 "Consolidated Balance Sheet\nTotal assets 1,000")]
    assert detect_entity_name(pages) == "ACME Holdings Limited"


def test_detect_entity_name_none_when_absent():
    from app.services.derived import detect_entity_name

    assert detect_entity_name([(0, "Balance Sheet\nTotal assets 100")]) is None


def test_detect_entity_name_from_running_header():
    """Real HK/PRC filings print a running header 'Company Limited / Annual Report YYYY' on every
    page — the entity is the segment before the slash, not the whole line (which reads as chrome)."""
    from app.services.derived import detect_entity_name

    pages = [(0, "01\nChina SCE Group Holdings Limited / Annual Report 2023\n中駿集團控股有限公司 / 二零二三年年報")]
    assert detect_entity_name(pages) == "China SCE Group Holdings Limited"


# --- admin template detail renders a REAL configured template (#13) -------------------------
def test_template_detail_renders_real_template(client):
    # THE SHIPPED TEMPLATE, NAMED. `templates[0]` used to be it by accident: the list came back in
    # insertion order, so the seeded v1 sorted first. The list is now newest-first per key
    # (routes/templates.py), which is what every screen needs to default correctly — and after any
    # earlier test publishes a revision, the first row is that draft rather than the reference
    # template this test is about. The suite already learned this once, in the e2e template-index
    # test; asserted here the same way, on the template it means.
    templates = [t for t in client.get("/api/v1/templates").json()
                 if t["template_key"] == "hkfrs_hk_china_v1"]
    assert templates, "the reference template should be seeded"
    tid = next(t["id"] for t in templates if t["is_latest"])
    detail = client.get(f"/api/v1/templates/{tid}/detail").json()
    assert detail["tree"], "a configured template must render a non-empty tree"
    assert detail["template"]["line_items"] > 0
    # Leaf nodes carry per-node config (aliases from the paired ontology, a sign convention).
    leaves = [n for n in detail["tree"] if n["lvl"] == 2]
    assert leaves and all(n["id"] in detail["node_config"] for n in leaves)
    some = detail["node_config"][leaves[0]["id"]]
    assert {"breadcrumb", "label", "aliases", "sign", "netting"} <= set(some)
