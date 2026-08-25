"""Synthetic fixture generators with known ground truth.

These build documents whose contents we control exactly, so extraction accuracy,
routing, and integrity checks can be asserted automatically without any real
(sensitive) financial statements. reportlab/openpyxl are dev-only dependencies.
"""
from __future__ import annotations

import io


def make_native_pdf(title: str = "Balance Sheet") -> bytes:
    """A single-page native (text-layer) PDF containing a small balance sheet."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, title)
    c.setFont("Helvetica", 10)
    rows = [
        ("Cash and cash equivalents", "Note 14", "1,204"),
        ("Trade receivables", "Note 15", "3,410"),
        ("Property, plant and equipment", "Note 5", "12,800"),
        ("Total assets", "", "17,414"),
    ]
    for label, note, value in rows:
        y -= 24
        c.drawString(72, y, label)
        c.drawString(320, y, note)
        c.drawRightString(500, y, value)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_unmapped_row_pdf() -> bytes:
    """A balance sheet carrying one caption the shipped rulebook cannot place.

    For the paths that need a row in the review queue with nothing mapped to it — the re-map offer
    and the judgement layer both act on such a card. Deliberately a caption no alias and no rule
    hint claims, and deliberately NOT a mangled version of a real one: those used to be produced by
    the reader gluing a page title onto the first item, which is a defect that has since been fixed,
    and a fixture that depends on a defect stops working the moment the defect does.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _width, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, "Balance Sheet")
    c.setFont("Helvetica", 10)
    for label, note, value in [("Cash and cash equivalents", "Note 14", "1,204"),
                               ("Trade receivables", "Note 15", "3,410"),
                               ("Total assets", "", "4,614")]:
        y -= 24
        c.drawString(72, y, label)
        c.drawString(320, y, note)
        c.drawRightString(500, y, value)
    c.showPage()

    # THE UNPLACEABLE ROW IS ON ITS OWN PAGE, and that is what makes it unplaceable. Printed among
    # the current assets it was swept into that section's residual concept instead
    # (``bs_current_assets__others`` at 0.4, flagged ``residual_combined``) — which is the residual
    # framework working: an unclaimed face row whose section resolves always reaches the face, so
    # ``unmapped_remaining`` was 0 and this fixture raised a LOW-CONFIDENCE finding rather than the
    # unmapped one its name promises. With no section resolvable around it the sweep reports the row
    # ineligible and it stays unmapped, which is the finding every caller of this fixture is after.
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Balance Sheet (continued)")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 100, "Sundry widgets held pending resale")
    c.drawRightString(500, height - 100, "560")
    c.showPage()
    c.save()
    return buf.getvalue()


def make_tax_note_split_pdf() -> bytes:
    """THE SHAPE REQUIREMENT 20 IS ABOUT: one line on the face, its components only in the note.

    A P&L printing a single "Income tax expense" citing note 11, and note 11 splitting it into
    current and deferred tax which sum to it exactly. Both halves of the authority to read that
    split are DECLARED rather than inferred, which is what the fixture exercises:

    * the TEMPLATE declares ``pl_tax_expense__total_tax_expense`` a subtotal over
      ``pl_tax_expense__current_tax`` and ``pl_tax_expense__deferred_tax`` — so which lines are the
      components is the template's own arithmetic, not a guess from what the note happens to list;
    * the RULEBOOK declares ``note_use: decomposition_allowed`` on that tax section, against a
      default of ``evidence_only`` everywhere else, with the stated reason that HKEX filings
      routinely print only the total on the face and split it in the tax note.

    The figures are the real filing's shape with round numbers: a deferred CREDIT, so one component
    is negative and the split is not merely additive. 1,200 - 200 = 1,000.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _width, height = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Consolidated Statement of Profit or Loss")
    c.setFont("Helvetica", 10)
    y = height - 104
    for label, note, cur, prior in [
            ("Profit before tax", "", "5,000", "4,200"),
            ("Income tax expense", "11", "1,000", "900")]:
        c.drawString(72, y, label)
        if note:
            c.drawString(360, y, f"Note {note}")
        c.drawRightString(452, y, cur)
        c.drawRightString(520, y, prior)
        y -= 22
    c.showPage()

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Notes to the Financial Statements")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 100, "Note 11: Income tax")
    y = height - 124
    # 1,200 - 200 = 1,000 and 1,050 - 150 = 900 — the printed total, exactly, in both periods.
    for label, cur, prior in [("Current tax", "1,200", "1,050"),
                              ("Deferred tax", "(200)", "(150)")]:
        c.drawString(72, y, label)
        c.drawRightString(452, y, cur)
        c.drawRightString(520, y, prior)
        y -= 22
    # The note's own total, which the ROLE keeps out of the components.
    c.drawString(72, y, "Total tax charge for the year")
    c.drawRightString(452, y, "1,000")
    c.drawRightString(520, y, "900")
    c.showPage()
    c.save()
    return buf.getvalue()


def make_containment_gap_pdf() -> bytes:
    """A gross parent printed beside a component it contains, not accounted for by it.

    The shipped rulebook declares that ``bs_current_assets__cash_and_cash_equivalents`` CONTAINS
    ``bs_current_assets__restricted_cash``, so printing both means the parent was printed gross:
    ``map_ontology._enforce_containment`` un-files the parent to stop the section counting the money
    twice, and 1,204 − 500 = 704 of the printed figure then sits on no line. That is one
    ``containment_gap`` card.

    FOR THE PATHS THAT NEED A FINDING OVER A MAPPED CONCEPT — one whose figures can then be moved
    through the line-item PATCH, which is what makes an accepted judgement go stale. Three properties
    are deliberate, and each was arrived at by a fixture that lacked it:

    * ONE card, with a UNIQUE identity. A printed subtotal that disagrees with its components raises
      the section's rollup AND its reconciliation — two cards under ONE ``subject_key``, an ambiguous
      pair — so a locator keyed on that identity matches twice and accepting one accepts both.
    * a card that NAMES AN EXTRACTED LINE (``names`` is the child), so there is a real row to edit.
      A relation card names every concept its section declares, most of which this filing has no row
      for, and its own target is a CALCULATED line that a printed figure may not be written onto.
    * a PRIOR COLUMN THAT TIES (1,100 against 1,100), so an edit to the current column leaves the
      prior one alone and "exactly one card went stale" is a real assertion rather than an artefact
      of there being only one card in the queue.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _width, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, "Balance Sheet")
    c.setFont("Helvetica", 9)
    y -= 20
    c.drawRightString(470, y, "2023")
    c.drawRightString(540, y, "2022")
    c.setFont("Helvetica", 10)
    # NO SECTION SUBTOTAL is printed, on purpose: one would raise the section's relations as well,
    # which is the ambiguous pair the docstring above is about.
    for label, note, cur, prior in [
            ("Current assets", "", "", ""),
            ("Cash and cash equivalents", "Note 14", "1,204", "1,100"),
            ("Restricted cash", "Note 16", "500", "1,100")]:
        y -= 24
        c.drawString(72, y, label)
        if note:
            c.drawString(320, y, note)
        if cur:
            c.drawRightString(470, y, cur)
        if prior:
            c.drawRightString(540, y, prior)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_dual_basis_pdf() -> bytes:
    """A native PDF whose columns are a two-level Consolidated | Standalone header, each with
    a current + prior period — for testing consolidated+standalone extraction in one pass."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "Balance Sheet")
    # Two-level column header: Consolidated over cols ~300/370, Standalone over ~440/510.
    c.setFont("Helvetica-Bold", 9)
    y -= 22
    c.drawString(300, y, "Consolidated")
    c.drawString(445, y, "Standalone")
    c.setFont("Helvetica", 9)
    y -= 14
    c.drawRightString(330, y, "2025")
    c.drawRightString(400, y, "2024")
    c.drawRightString(475, y, "2025")
    c.drawRightString(545, y, "2024")
    c.setFont("Helvetica", 10)
    rows = [
        ("Trade receivables", ("3,410", "2,900", "3,100", "2,700")),
        ("Cash and cash equivalents", ("1,204", "980", "1,050", "900")),
    ]
    for label, (cc, cp, sc, sp) in rows:
        y -= 22
        c.drawString(72, y, label)
        c.drawRightString(330, y, cc)
        c.drawRightString(400, y, cp)
        c.drawRightString(475, y, sc)
        c.drawRightString(545, y, sp)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_group_company_pdf() -> bytes:
    """A native PDF headed the way an HKEX filing heads its balance sheet: "Group" over one pair
    of dated columns and "Company" over another, with the scale declared in the statement header.

    This is the layout the engine used to read as single-basis — the Company's figures were filed
    as the Group's and added to them — because the detector only knew the words "Consolidated" and
    "Standalone"."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "Consolidated Statement of Financial Position")
    c.setFont("Helvetica-Bold", 9)
    y -= 24
    c.drawString(300, y, "Group")
    c.drawString(445, y, "Company")
    c.setFont("Helvetica", 9)
    y -= 14
    for x, text in ((330, "2024"), (400, "2023"), (475, "2024"), (545, "2023")):
        c.drawRightString(x, y, text)
    y -= 12
    c.drawRightString(330, y, "RMB'000")
    c.setFont("Helvetica", 10)
    rows = [
        ("Investments in subsidiaries", ("", "", "8,000", "7,500")),
        ("Trade receivables", ("3,410", "2,900", "310", "270")),
        ("Cash and cash equivalents", ("1,204", "980", "105", "90")),
        ("Total assets", ("4,614", "3,880", "8,415", "7,860")),
    ]
    for label, cells in rows:
        y -= 22
        c.drawString(72, y, label)
        for x, text in zip((330, 400, 475, 545), cells):
            if text:
                c.drawRightString(x, y, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_group_and_company_note_pdf() -> bytes:
    """A NOTES page whose prose names both entities in one sentence — "The Group and the Company
    had no material contingent liabilities" — above a small table.

    The negative case for basis banding: a sentence that mentions both is not a two-basis column
    header, and reading it as one splits the comparative so last year's figures are reported as
    this year's for a second entity."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, y, "34. Contingent liabilities")
    c.setFont("Helvetica", 10)
    y -= 22
    c.drawString(72, y, "The Group and the Company had no material contingent liabilities as at")
    y -= 16
    c.drawString(72, y, "31 December 2024 other than the guarantees set out below.")
    y -= 24
    for x, text in ((430, "2024"), (510, "2023")):
        c.drawRightString(x, y, text)
    for label, cur, pri in (("Guarantees to banks", "1,200", "900"),
                            ("Guarantees to suppliers", "450", "500"),
                            ("Other guarantees", "30", "40"),
                            ("Total guarantees", "1,680", "1,440")):
        y -= 22
        c.drawString(72, y, label)
        c.drawRightString(430, y, cur)
        c.drawRightString(510, y, pri)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_company_limited_header_pdf() -> bytes:
    """A single-basis face page whose RUNNING HEADER names a "Group" on the left and ends in
    "Company Limited" on the right, where the value columns are.

    The other negative case, and the one that isolates the geometric test: both words a two-basis
    header needs are on one line, in separate runs, with no amount on it — and the page still has
    one basis, because a basis caption bands the figures it stands OVER and "Group" here stands
    over the caption column. Banding on the filer's own name would split the comparative, so last
    year's figures would be reported as this year's for a second entity."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 50
    c.setFont("Helvetica", 8)
    c.drawString(72, y, "Sunrise Group Holdings Limited")
    c.drawRightString(523, y, "Sunrise Company Limited")
    c.setFont("Helvetica-Bold", 13)
    y -= 26
    c.drawString(72, y, "Statement of Financial Position")
    c.setFont("Helvetica", 9)
    y -= 22
    c.drawRightString(430, y, "2024")
    c.drawRightString(510, y, "2023")
    c.setFont("Helvetica", 10)
    for label, cur, pri in (("Inventories", "2,000", "1,800"),
                            ("Trade receivables", "3,410", "2,900"),
                            ("Cash and cash equivalents", "1,204", "980"),
                            ("Total assets", "6,614", "5,680")):
        y -= 22
        c.drawString(72, y, label)
        c.drawRightString(430, y, cur)
        c.drawRightString(510, y, pri)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_rich_pdf() -> bytes:
    """A fuller native PDF: BS totals + P&L headline + a notes page with qualitative
    disclosures — so derived ratios, the disclosure scan, and free-form notes all populate."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    def rows(items, y):
        c.setFont("Helvetica", 10)
        for label, cur, pri in items:
            c.drawString(72, y, label)
            c.drawRightString(430, y, cur)
            c.drawRightString(510, y, pri)
            y -= 22
        return y

    c.setFont("Helvetica-Bold", 13); c.drawString(72, height - 60, "Balance Sheet")
    y = rows([
        ("Inventories", "2,000", "1,800"),
        ("Trade receivables", "3,410", "2,900"),
        ("Cash and cash equivalents", "1,204", "980"),
        ("Total current assets", "6,614", "5,680"),
        ("Total current liabilities", "3,300", "3,100"),
        ("Total non-current liabilities", "1,200", "1,400"),
        ("Total equity", "9,114", "7,180"),
        ("Total assets", "13,614", "11,680"),
    ], height - 90)
    c.showPage()

    c.setFont("Helvetica-Bold", 13); c.drawString(72, height - 60, "Statement of Profit or Loss")
    rows([
        ("Revenue from operations", "20,000", "18,000"),
        ("Operating profit", "3,200", "2,700"),
        ("Profit for the year", "2,400", "1,950"),
    ], height - 90)
    c.showPage()

    c.setFont("Helvetica-Bold", 12); c.drawString(72, height - 60, "Notes to the Financial Statements")
    c.setFont("Helvetica", 9)
    for i, line in enumerate([
        "The auditor has issued a qualified opinion in respect of inventory valuation.",
        "Contingent liabilities: the Group is subject to legal proceedings estimated at 500.",
        "The Company has provided financial guarantees to subsidiaries totalling 1,200.",
        "Related party transactions with associates are disclosed in note 28.",
        "Subsequent events: a dividend was declared after the reporting period.",
    ]):
        c.drawString(72, height - 90 - i * 18, line)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_multipage_pdf() -> bytes:
    """A 2-page native PDF: face on page 0, notes on page 1."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Statement of Financial Position")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 100, "Cash and cash equivalents      Note 14      1,204")
    c.showPage()

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Notes to the Financial Statements")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 100, "Note 14: Cash and cash equivalents")
    c.drawString(72, height - 120, "Cash on hand      204")
    c.drawString(72, height - 140, "Balances with banks      1,000")
    c.showPage()
    c.save()
    return buf.getvalue()


def make_sub_note_pdf() -> bytes:
    """A face row citing a SUB-NOTE of a note table numbered without the sub-part.

    The shape a real HK filing prints and the one the citation rule exists for: the balance sheet
    says "16(b)" beside trade receivables, and the notes section prints ONE table numbered 16 with
    (a) and (b) inside it. The printed citation therefore matches no note table by name, and every
    stage that reads citations by name — link, prune, section segmentation — has to fall back to
    the parent or leave the figure with nothing behind it.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Consolidated Statement of Financial Position")
    c.setFont("Helvetica", 10)
    y = height - 100
    for label, note, value in [("Trade receivables", "16(b)", "3,410"),
                               ("Cash and cash equivalents", "17", "1,204")]:
        c.drawString(72, y, label)
        c.drawString(320, y, f"Note {note}")
        c.drawRightString(500, y, value)
        y -= 24
    c.showPage()

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Notes to the Financial Statements")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 100, "Note 16: Trade and other receivables")
    c.drawString(72, height - 120, "Trade receivables      3,410")
    c.drawString(72, height - 140, "Prepayments      120")
    c.showPage()
    c.save()
    return buf.getvalue()


def make_named_running_header_pdf() -> bytes:
    """A filing whose running header is JUST THE COMPANY NAME, with a figure on its baseline.

    The HK house style no word list catches: no "Annual Report", no 年報 — only the entity, printed
    at the top of every page, and on one page a page-number-ish figure sitting on the same
    baseline. The header then reads as an ordinary caption carrying an amount, and was published
    as a line item on the face of a statement.

    Four pages so the header clears the repeat threshold, and the amount beside it is a real one
    (not a year or a day-of-month) so no existing date-fragment rule can catch it either.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    def header(page_no: int, with_amount: bool) -> None:
        c.setFont("Helvetica", 8)
        c.drawString(72, height - 40, "Lai Sun Garment (International) Limited")
        if with_amount:
            # Right-aligned on the SAME baseline as the header — where a value column sits.
            c.drawRightString(500, height - 40, "8,461")
        c.setFont("Helvetica", 10)

    for page_no, (title, rows) in enumerate([
        ("Consolidated Statement of Financial Position",
         [("Trade receivables", "Note 16(b)", "3,410"),
          ("Cash and cash equivalents", "Note 17", "1,204")]),
        ("Notes to the Financial Statements",
         [("Note 16: Trade and other receivables", "", ""),
          ("Trade receivables", "", "3,410")]),
        ("Corporate Information", [("Registered office", "", "")]),
        ("Five Year Financial Summary", [("Revenue", "", "9,000")]),
    ]):
        header(page_no, with_amount=(page_no == 0))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, height - 72, title)
        c.setFont("Helvetica", 10)
        y = height - 100
        for label, note, value in rows:
            c.drawString(72, y, label)
            if note:
                c.drawString(320, y, note)
            if value:
                c.drawRightString(500, y, value)
            y -= 24
        c.showPage()
    c.save()
    return buf.getvalue()


def make_financial_highlights_pdf() -> bytes:
    """A FINANCIAL HIGHLIGHTS page in the front matter, then the real P&L.

    The shape measured on the China SCE 2023 filing: the highlights page prints
    "SUMMARY OF STATEMENT OF PROFIT OR LOSS 損益表摘要" over a two-year extract with a percentage
    change column. That title matches the P&L pattern outright, so the page arrived carrying a
    STRONG title, and a strong title is worth more to the face state than the backmatter signal
    takes off it — while the state that exists for summaries sits after the statements and cannot
    be reached from front matter. The page was published as a P&L face page, and the figures it
    repeats collided on their canonical keys with the same concepts read off the real statement.

    Deliberately the SAME concepts on both pages, at DIFFERENT figures, because that is what makes
    the duplication a wrong answer rather than a harmless one.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    # THE HAN TITLE IS THE ONE THAT MATCHED. On the real filing the classifier resolved the page
    # from "損益表摘要" — 損益表 is the P&L statement and 摘要 trails it as the qualifier — not from
    # the English line, which the coverage floor rejects because "SUMMARY OF" is a third of it.
    # A fixture with only the English text is classified correctly for the wrong reason.
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    # FRONT MATTER FIRST, and it is load-bearing. The state a summary page would otherwise fall
    # into is the one for BACK matter, which cannot be entered from front matter — that transition
    # is deliberately expensive, because a five-year summary does not precede the statements. Reach
    # the highlights page with the decode already in the front-matter state and its only remaining
    # options are front matter or FACE, which is the position the real filing puts it in. Without
    # these pages the fixture is classified correctly for the wrong reason and proves nothing.
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, height - 60, "CHAIRMAN'S STATEMENT")
    c.setFont("Helvetica", 9)
    y = height - 90
    for line in ("The board presents its review of the year. Trading conditions in the period",
                 "remained difficult, and the group continued to prioritise liquidity while",
                 "completing the projects already under construction. We thank our staff."):
        c.drawString(72, y, line)
        y -= 14
    c.showPage()

    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, height - 60, "CORPORATE INFORMATION")
    c.setFont("Helvetica", 9)
    y = height - 90
    for line in ("Registered office: Cricket Square, Grand Cayman", "Auditor: Ernst & Young",
                 "Principal bankers: Bank of China (Hong Kong) Limited"):
        c.drawString(72, y, line)
        y -= 14
    c.showPage()

    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, height - 60, "FINANCIAL HIGHLIGHTS")
    c.setFont("STSong-Light", 12)
    # EACH LANGUAGE ON ITS OWN LINE, as the filing prints them and as the reader groups them: a
    # candidate is one text line, so Han glued onto the English baseline gives a 46-character
    # candidate in which 損益表 is 3 characters — below the title-coverage floor, and matching
    # nothing. That is a fixture that cannot reproduce the defect, not a filing that lacks it.
    c.drawString(72, height - 76, "財務摘要")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, height - 100, "SUMMARY OF STATEMENT OF PROFIT OR LOSS")
    c.setFont("STSong-Light", 12)
    c.drawString(72, height - 118, "損益表摘要")
    c.setFont("Helvetica", 9)
    c.drawString(72, height - 136, "For the year ended 31 December")
    y = height - 158
    for label, cur, prior, change in [("Revenue", "20,960,968", "26,705,112", "-21.5"),
                                      ("Gross profit", "2,630,992", "5,410,308", "-51.4"),
                                      ("Total equity", "20,482,326", "36,625,241", "-44.1")]:
        c.drawString(72, y, label)
        c.drawRightString(330, y, cur)
        c.drawRightString(420, y, prior)
        c.drawRightString(500, y, change)
        y -= 22
    c.showPage()

    # The statement itself, twenty pages later in a real filing and one here: the same concepts,
    # different figures, and this is the page whose figures must survive.
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "CONSOLIDATED STATEMENT OF PROFIT OR LOSS")
    c.setFont("STSong-Light", 12)
    c.drawString(72, height - 90, "綜合損益表")
    c.setFont("Helvetica", 10)
    y = height - 120
    for label, cur, prior in [("Revenue", "20,960,968", "26,705,112"),
                              ("Cost of sales", "(18,329,976)", "(21,294,804)"),
                              ("Gross profit", "2,630,992", "5,410,308")]:
        c.drawString(72, y, label)
        c.drawRightString(420, y, cur)
        c.drawRightString(500, y, prior)
        y -= 24
    c.showPage()
    c.save()
    return buf.getvalue()


def make_decomposed_note_pdf(cross_section: bool = False) -> bytes:
    """A combined caption on the FACE, itemised in the NOTE it cites.

    The shape the same-section split exists for. The balance sheet prints one line --
    "Prepayments, other receivables and other assets 5,000" citing note 18 -- and note 18 lists the
    components, each of which is a template concept in the SAME section (current assets), summing
    exactly to the printed line in both periods.

    ``cross_section=True`` prints the same filing with one component swapped for a caption whose
    concept lives in a DIFFERENT section (a non-current asset), which must leave the aggregate
    standing rather than split it.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Consolidated Statement of Financial Position")
    c.setFont("Helvetica", 10)
    y = height - 104
    c.drawString(72, y, "CURRENT ASSETS")
    y -= 22
    for label, note, cur, prior in [
            ("Prepayments, other receivables and other assets", "18", "5,000", "4,400"),
            ("Cash and cash equivalents", "19", "1,204", "1,100")]:
        c.drawString(72, y, label)
        c.drawString(360, y, f"Note {note}")
        c.drawRightString(452, y, cur)
        c.drawRightString(520, y, prior)
        y -= 22
    c.showPage()

    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, height - 72, "Notes to the Financial Statements")
    c.setFont("Helvetica", 10)
    c.drawString(72, height - 100, "Note 18: Prepayments, other receivables and other assets")
    y = height - 124
    # 3,410 + 1,000 + 590 = 5,000 and 3,000 + 900 + 500 = 4,400 -- the aggregate, exactly.
    third = ("Property, plant and equipment" if cross_section
             else "Prepaid income tax")
    # ONE CONCEPT ITEMISED TWICE. A note routinely splits a single template concept across two
    # disclosed rows (here under a mainland and an overseas heading), so the components are summed
    # per concept rather than assumed one-to-one: 400 + 190 = 590 is what the aggregate needs.
    for label, cur, prior in [("Trade receivables", "3,410", "3,000"),
                              ("Due from related parties", "1,000", "900"),
                              (third, "400", "340"),
                              (third, "190", "160")]:
        c.drawString(72, y, label)
        c.drawRightString(452, y, cur)
        c.drawRightString(520, y, prior)
        y -= 22
    # The note's own total, which must not be counted as a component.
    c.drawString(72, y, "Total")
    c.drawRightString(452, y, "5,000")
    c.drawRightString(520, y, "4,400")
    c.showPage()
    c.save()
    return buf.getvalue()


def make_company_statement_after_notes_pdf() -> bytes:
    """THE HK HOUSE STYLE THAT MIS-LOADS: the Group's balance sheet, a note, then the COMPANY's own
    balance sheet printed after the notes with the SAME title minus the word "consolidated".

    Every existing basis fixture puts both entities on ONE page, side by side, where
    ``_basis_bands`` finds them. This one puts them on separate single-basis pages, which is how
    an HKEX filing actually prints the Company's statement of financial position — past the notes,
    titled only "STATEMENT OF FINANCIAL POSITION". No column header names an entity, so nothing in
    ``row_reconstruct`` can tell the two pages apart, and the Company's figures were added to the
    Group's: the two pages share every label, so they map to the same canonical keys and the
    spread sums them.

    The note page in the middle is load-bearing, not decoration. It is what makes the classifier's
    notes-region walk mark the third page as company-only (``classify._scope_of`` reads a
    face-titled page inside the notes region as the Company's), and that verdict is the only
    evidence on the page of whose figures these are.

    Ground truth: p0 consolidated (36,683 / 40,904 / 53,035), p2 company (647 / 0 / 7,551).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    def _rows(c, y, rows):
        c.setFont("Helvetica", 10)
        for label, cur, pri in rows:
            y -= 20
            c.drawString(72, y, label)
            c.drawRightString(430, y, cur)
            c.drawRightString(510, y, pri)
        return y

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    y = height - 72
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "CONSOLIDATED STATEMENT OF FINANCIAL POSITION")
    c.setFont("Helvetica", 9)
    y -= 20
    c.drawString(72, y, "As at 31 July 2025")
    y -= 22
    c.drawRightString(430, y, "2025")
    c.drawRightString(510, y, "2024")
    _rows(c, y, (("Investment properties", "36,683", "37,095"),
                 ("Inventories", "40,904", "47,131"),
                 ("Total non-current assets", "53,035", "55,400")))
    c.showPage()

    y = height - 72
    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, y, "29. Cash and cash equivalents")
    c.setFont("Helvetica", 10)
    y -= 22
    c.drawString(72, y, "Cash at banks earns interest at floating rates based on daily bank")
    y -= 16
    c.drawString(72, y, "deposit rates.")
    y -= 24
    c.drawRightString(430, y, "2025")
    c.drawRightString(510, y, "2024")
    _rows(c, y, (("Cash and bank balances", "2,379", "2,502"),
                 ("Time deposits", "647", "324")))
    c.showPage()

    y = height - 72
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "STATEMENT OF FINANCIAL POSITION")
    c.setFont("Helvetica", 9)
    y -= 20
    c.drawString(72, y, "As at 31 July 2025")
    y -= 22
    c.drawRightString(430, y, "2025")
    c.drawRightString(510, y, "2024")
    _rows(c, y, (("Investment properties", "647", "698"),
                 ("Inventories", "0", "0"),
                 ("Total non-current assets", "7,551", "7,598")))
    c.showPage()
    c.save()
    return buf.getvalue()


def make_issuer_named_untokened_face_pdf() -> bytes:
    """A filing that names NO entity anywhere a scope can be read from, behind a front-matter page
    that latches the notes-region walk.

    Two false positives in one document, both of which would move every figure in it to standalone:

    * ``_title_candidates`` joins the issuer-name line onto the statement title, so the text a scope
      is read from is "SUNRISE DEVELOPMENT COMPANY LIMITED BALANCE SHEET" — carrying the word
      Company for reasons that say nothing about whose figures these are.
    * the Corporate Information page's registered-office line, "1 Harbour View Street, …", matches
      ``classify._NOTE_ONE``, so the page decodes as NOTES and every later face page is "past the
      notes" — which on its own used to be enough to call a face page the Company's.

    Ground truth: this filing has ONE set of figures, they are the Group's by default, and the right
    answer for the page's scope is None — no evidence either way, stated rather than guessed.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    y = height - 72
    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, y, "Corporate Information")
    c.setFont("Helvetica", 10)
    for text in ("Registered office", "1 Harbour View Street, Central, Hong Kong",
                 "Auditors: A Firm LLP", "Share registrar: A Registrar Limited"):
        y -= 20
        c.drawString(72, y, text)
    c.showPage()

    y = height - 60
    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "SUNRISE DEVELOPMENT COMPANY LIMITED")
    y -= 26
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "BALANCE SHEET")
    c.setFont("Helvetica", 9)
    y -= 20
    c.drawString(72, y, "As at 31 December 2024")
    y -= 22
    c.drawRightString(430, y, "2024")
    c.drawRightString(510, y, "2023")
    c.setFont("Helvetica", 10)
    for label, cur, pri in (("Investment properties", "36,683", "37,095"),
                            ("Inventories", "40,904", "47,131"),
                            ("Total non-current assets", "53,035", "55,400")):
        y -= 20
        c.drawString(72, y, label)
        c.drawRightString(430, y, cur)
        c.drawRightString(510, y, pri)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_consolidated_statement_spanning_two_pages_pdf() -> bytes:
    """A consolidated balance sheet running over two pages, where only the FIRST carries the title.

    A statement's entity is named once, at the top of the run — exactly as its statement TYPE is,
    which the classifier already carries forward in ``current``. Resolving the entity per page
    instead leaves the continuation with no verdict at all, and a run whose first page is the
    Company's would have its second page fall back to the Group.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    def _page(c, height, title, rows):
        y = height - 72
        if title:
            c.setFont("Helvetica-Bold", 13)
            c.drawString(72, y, title)
            c.setFont("Helvetica", 9)
            y -= 20
            c.drawString(72, y, "As at 31 July 2025")
        y -= 22
        c.drawRightString(430, y, "2025")
        c.drawRightString(510, y, "2024")
        c.setFont("Helvetica", 10)
        for label, cur, pri in rows:
            y -= 20
            c.drawString(72, y, label)
            c.drawRightString(430, y, cur)
            c.drawRightString(510, y, pri)
        c.showPage()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    _page(c, height, "CONSOLIDATED STATEMENT OF FINANCIAL POSITION",
          (("Investment properties", "36,683", "37,095"),
           ("Total non-current assets", "53,035", "55,400")))
    _page(c, height, None,
          (("Bank loans - current portion", "10,886", "2,523"),
           ("Total current liabilities", "19,411", "6,588")))
    c.save()
    return buf.getvalue()


def make_annual_report_pdf() -> bytes:
    """A realistic HKEX/IFRS-shaped annual report: cover, an auditor's report (which mentions
    face phrases in prose), three *consolidated* face statements, then a notes section that
    opens WITHOUT a 'Notes to…' banner — straight into '1 General information' — followed by a
    numbered continuation note that also mentions 'profit or loss' in prose. Ground truth:
    the three statement pages are FACE, the auditor page is not FACE, and the two note pages
    are NOTES (so their tables are extracted, not skipped)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4

    def page(title, body_lines, title_size=14):
        c.setFont("Helvetica-Bold", title_size)
        c.drawString(72, height - 72, title)
        c.setFont("Helvetica", 10)
        y = height - 104
        for ln in body_lines:
            c.drawString(72, y, ln); y -= 20
        c.showPage()

    # 0: cover / contents (OTHER)
    page("Annual Report 2024", ["Contents", "Corporate information", "Chairman's statement"])
    # 1: independent auditor's report — prose mentions "profit or loss" / "cash flows" but is
    #    NOT a statement face (and precedes the statements).
    page("Independent Auditor's Report", [
        "In our opinion the consolidated financial statements give a true and fair view.",
        "We audited the statement of profit or loss and the statement of cash flows.",
        "Key audit matters were addressed in forming our opinion."])
    # 2-4: the three consolidated face statements (FACE)
    page("Consolidated Statement of Profit or Loss", [
        "Revenue      Note 5      45,230",
        "Cost of sales      (28,110)",
        "Profit for the year      6,120"])
    page("Consolidated Statement of Financial Position", [
        "Property, plant and equipment      Note 12      88,400",
        "Trade receivables      Note 15      12,300",
        "Cash and cash equivalents      Note 18      9,870",
        "Total assets      143,900"])
    page("Consolidated Statement of Cash Flows", [
        "Net cash from operating activities      14,200",
        "Net cash used in investing activities      (9,100)"])
    # 5: notes section opens with a numbered heading, NO 'Notes to…' banner (exercises the
    #    'first numbered note after the face statements' start rule).
    page("1 General information", [
        "The Company is incorporated in the Cayman Islands with limited liability.",
        "Its shares are listed on The Stock Exchange of Hong Kong Limited."])
    # 6: a numbered continuation note that mentions a face phrase in prose (must stay NOTES).
    page("18 Cash and cash equivalents", [
        "Cash at banks and on hand      9,870",
        "Amounts are measured at amortised cost; see the statement of profit or loss for interest.",
        "Short-term deposits      2,400"])
    c.save()
    return buf.getvalue()


def make_hk_running_header_report_pdf() -> bytes:
    """Reproduces the structure of a real HK/PRC-listed annual report that trips naive
    classifiers: a bilingual RUNNING HEADER on every page, a Financial Highlights page that
    quotes 'Summary of Statement of Profit or Loss' (must NOT be a face), an auditor's report
    that mentions statements in prose, statement titles SPLIT across two lines, a 'Notes to
    Consolidated Financial Statements' banner, and a Five Year Financial Summary in back-matter.
    Ground truth: highlights + auditor = OTHER, the three statements = FACE, the two note pages =
    NOTES, the five-year summary = OTHER."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    n = [0]

    def page(title_lines, body_lines):
        y = height - 56
        c.setFont("Helvetica", 8)
        c.drawString(60, y, f"{n[0]:02d}")                       # page number
        c.drawString(90, y, "Acme Holdings Limited / Annual Report 2024")   # running header EN
        c.setFont("Helvetica", 8)
        c.drawString(90, y - 12, "艾克美控股有限公司 / 二零二四年年報")          # running header ZH
        y -= 40
        c.setFont("Helvetica-Bold", 13)
        for tl in title_lines:                                    # the real page title (may split)
            c.drawString(72, y, tl); y -= 18
        c.setFont("Helvetica", 10)
        y -= 6
        for bl in body_lines:
            c.drawString(72, y, bl); y -= 18
        c.showPage(); n[0] += 1

    page(["Annual Report 2024"], ["Corporate information", "Contents"])                    # 0 cover
    page(["Financial Highlights", "Summary of Statement of Profit or Loss"],               # 1 highlights (OTHER)
         ["Revenue      45,230", "Profit for the year      6,120"])
    page(["Independent Auditor's Report"],                                                 # 2 auditor (OTHER)
         ["In our opinion the consolidated financial statements give a true and fair view.",
          "We audited the consolidated statement of profit or loss and the statement of cash flows."])
    page(["Consolidated Statement of", "Profit or Loss"],                                  # 3 FACE (split title)
         ["Revenue      Note 5      45,230", "Profit for the year      6,120"])
    page(["Consolidated Statement of", "Financial Position"],                              # 4 FACE (split title)
         ["Property, plant and equipment      Note 12      88,400",
          "Cash and cash equivalents      Note 18      9,870", "Total assets      143,900"])
    page(["Consolidated Statement of", "Cash Flows"],                                      # 5 FACE (split title)
         ["Net cash from operating activities      14,200"])
    page(["Notes to Consolidated", "Financial Statements"],                                # 6 NOTES (banner)
         ["1. Corporate and group information",
          "The Company is incorporated in the Cayman Islands with limited liability."])
    page(["18 Cash and cash equivalents"],                                                 # 7 NOTES (continuation)
         ["Cash at banks and on hand      9,870",
          "See the statement of profit or loss for related interest income."])
    page(["Five Year Financial Summary"],                                                  # 8 back-matter (OTHER)
         ["Revenue      45,230      40,110      38,900      35,000      31,200"])
    c.save()
    return buf.getvalue()


def make_xlsx() -> bytes:
    """A workbook with a negative-number format and a hidden sheet."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"
    ws["A1"] = "Cash and cash equivalents"
    ws["B1"] = 1204
    ws["A2"] = "Accumulated depreciation"
    ws["B2"] = -500
    ws["B2"].number_format = "#,##0;(#,##0)"

    hidden = wb.create_sheet("Working")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "scratch"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def make_comparative_pdf() -> bytes:
    """A two-column comparative balance sheet — this year beside last year.

    Deliberately includes a line printed for the PRIOR YEAR ONLY ("Pledged deposits", released
    during the year). That row is the one a positional reader mis-files as the current year, and
    it is also the case a reviewer has to be able to click through to last year's page for.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    y = height - 72
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, y, "Consolidated Statement of Financial Position")
    y -= 20
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(430, y, "31 December 2024")
    c.drawRightString(520, y, "31 December 2023")
    c.setFont("Helvetica", 10)
    rows = [
        ("Property, plant and equipment", "Note 5", "12,800", "11,400"),
        ("Inventories", "Note 8", "2,150", "1,980"),
        ("Trade receivables", "Note 15", "3,410", "3,120"),
        ("Cash and cash equivalents", "Note 14", "1,204", "990"),
        ("Pledged deposits", "Note 14", "", "2,031"),          # prior year only
        ("Total assets", "", "19,564", "21,521"),
    ]
    for label, note, cur, prior in rows:
        y -= 24
        c.drawString(72, y, label)
        c.drawString(300, y, note)
        if cur:
            c.drawRightString(430, y, cur)
        if prior:
            c.drawRightString(520, y, prior)
    c.showPage()
    c.save()
    return buf.getvalue()
