"""Page-classification stage (face / notes / other).

Two properties matter for real annual reports, and the second is why this is a DECODE rather than a
scan. A filing runs narrative → the statement faces → the notes → back-matter, so a page's kind is
mostly settled by where it sits; but the local evidence on a page is noisy in both directions. Note
pages quote face phrases in prose ("12. Financial assets at fair value through profit or loss"), the
auditor's report lists every statement it audited in bold, and the contents page prints all of their
titles with leader dots. Judging each page alone therefore over-produces faces, and judging by
position alone cannot recover from one bad guess.

So: per-page evidence becomes an EMISSION score, document order becomes TRANSITION costs, and a
Viterbi decode picks the sequence that best explains the whole filing. One page's strong title can no
longer flip the rest of the document, and — unlike the fixed region walk this replaces — the notes
region is no longer one-way. That mattered: HK filings routinely print the Company-only statement of
financial position AFTER the consolidated notes, past note 40, and a one-way walk classified it as a
note forever. `NOTES → FACE` is now merely expensive, so a genuine face title there can win.

The lexicon carries two tiers per statement. STRONG patterns are self-anchoring — the phrase alone
identifies a statement face. WEAK patterns are generic and need an English structural anchor
elsewhere on the line, because "profit or loss" on its own is also a note-heading fragment. Chinese
patterns are all STRONG: they are multi-character and specific, so they need no anchor, and they must
NOT be end-anchored — a continuation page titled 綜合權益變動表(續) and a bilingual one-line title
both fail an end-of-line anchor.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from app.core.models import DocumentModel, PageKind
from app.core.models.enums import DocFormat
from app.core.stage import PipelineContext

# ---------------------------------------------------------------- lexicons ---
#
# Per-character classes ([資资]) collapse Traditional, Simplified and the mixed-script typesetting HK
# filings sometimes produce into one pattern each.

_EN_ANCHOR = re.compile(
    r"statements?\s+of|balance\s+sheets?|income\s+statements?"
    r"|profit\s+and\s+loss\s+accounts?", re.I)

# (name, STRONG patterns, WEAK patterns)
_STATEMENTS: list[tuple[str, list[str], list[str]]] = [
    ("changes_in_equity", [
        r"statements?\s+of\s+changes\s+in\s+[a-z'’\s]{0,28}equity",
        r"statements?\s+of\s+(?:shareholders|owners|stockholders|equity\s+holders)"
        r"'?’?\s*equity",
        r"statements?\s+of\s+changes\s+in\s+net\s+assets",
        r"[權权]益[變变][動动][報报]?表",
    ], [
        r"changes\s+in\s+[a-z'’\s]{0,28}equity",
    ]),

    ("cash_flow", [
        r"statements?\s+of\s+cash\s?flows?",
        r"cash\s?flows?\s+statements?",
        r"[現现]金流[量動动][報报]?表",
    ], [
        r"cash\s?flows?",
    ]),

    ("balance_sheet", [
        r"statements?\s+of\s+financial\s+(?:position|condition)",
        r"balance\s+sheets?",
        r"statements?\s+of\s+assets\s+and\s+liabilities",
        r"[資资][產产][負负][債债][報报]?表",
        r"[財财][務务][狀状][況况][報报]?表",
    ], [
        r"financial\s+position",
    ]),

    # Not preferred by list order — by MATCH LENGTH at the topmost y (see _resolve_statement), so
    # "…OF PROFIT OR LOSS AND OTHER COMPREHENSIVE INCOME" beats a bare comprehensive-income hit.
    ("comprehensive_income", [
        r"statements?\s+of\s+(?:other\s+)?comprehensive\s+(?:income|loss|expenses?)",
        r"(?:其他)?全面(?:收益|收入|[虧亏][損损])[報报]?表",
        r"其他[綜综]合收益[報报]?表",
    ], [
        r"(?:other\s+)?comprehensive\s+(?:income|loss)",
    ]),

    ("profit_and_loss", [
        r"statements?\s+of\s+profit\s+(?:or|and|&)\s+loss(?:es)?",
        # Ch.18A biotech and other pre-revenue issuers title this "STATEMENTS OF LOSS"; the old
        # lexicon required the word "profit" and so resolved those filings to nothing.
        r"statements?\s+of\s+(?:loss|profit|income|earnings|operations)(?:es|s)?\b",
        r"income\s+statements?",
        r"profit\s+and\s+loss\s+accounts?",                 # legacy HK GAAP
        r"[損损]益[表報报賬帳账]",
        r"[利][潤润][報报]?表",
        r"[虧亏][損损][報报]?表",
        r"收益[報报]?表",
        r"[經经][營营][業业][績绩][報报]?表",
    ], [
        r"profit\s+(?:or|and|&)\s+loss(?:es)?",
    ]),
]

# 綜合收益表 vs 综合收益表: Traditional HK usage is 綜合 = consolidated and 全面收益 = comprehensive
# income, so 綜合收益表 is the income statement. PRC Simplified usage is 合并 = consolidated and
# 综合收益 = comprehensive income. Both land on profit_and_loss here because the bare 收益表 pattern
# is SHORTER than 全面收益表, so a genuine comprehensive-income page still wins on match length. A
# Simplified CAS filing with a separate 综合收益表 page is therefore tagged profit_and_loss, and is
# flagged in page.evidence["title_ambig"] rather than silently decided.
_ZH_CI_AMBIG = re.compile(r"[綜综]合收益[報报]?表")

# Single-statement presentation: one page carrying P&L and OCI together, including the all-loss form.
_OCI_COMBINED = re.compile(
    r"\b(?:profit\s+(?:or|and|&)\s+loss(?:es)?|loss(?:es)?|income|operations)\b"
    r"[^\n]{0,24}\band\s+other\s+comprehensive\s+(?:income|loss)\b"
    r"|[損损]益及其他(?:全面|[綜综]合)(?:收益|[虧亏][損损])"
    r"|[虧亏][損损]及其他(?:全面|[綜综]合)(?:收益|[虧亏][損损])"
    r"|收益及其他全面收益", re.I)

# A title candidate is disqualified outright by any of these. Positive matching alone cannot separate
# a face title from a note heading, a contents line or auditor prose quoting the same words — and the
# contents page is the expensive case, because its titles used to start the face region early and
# hand pages of front matter to the extractor as statements.
_TITLE_NEGATIVE = re.compile(
    r"notes?\s+to\s+the\b|notes?\s+to\s+(?:consolidated|financial)\b"
    r"|notes\s+forming\s+part\s+of"
    r"|附\s*[註注]"
    r"|\.{3,}|·{3,}|…"                                                   # contents leader dots
    r"|\bpages?\s+\d+|\bset\s+out\s+(?:on|in)\b|\brefer(?:\s+to)?\b"
    r"|\bin\s+our\s+opinion\b|\bwe\s+have\s+audited\b|\bas\s+described\s+in\b"
    r"|\breconciliation\s+of\b|\bextract(?:ed|s)?\s+from\b|\bsummar(?:y|ised)\b"
    r"|\bpro\s+forma\b|[備备]考"
    r"|\bunaudited\s+supplementary\b"
    r"|[載载][於于]|[見见]\s*[附第]|[第]\s*\d+\s*[頁页]"
    # A statement NAME inside a longer note heading is not that statement. 资产负债表日后事项
    # is "events AFTER the balance sheet date" — the standard CAS subsequent-events note —
    # and it contains 资产负债表 verbatim, so it resolved as a balance-sheet FACE title. On the
    # measured filing that made PDF page 196 (printed 197) a face page: 39 lines of pure
    # litigation prose plus the guarantee and letter-of-credit totals, no table. Face pages
    # never become notes, so 0 of 190 notes and 0 of 284 line items carried that disclosure
    # and ¥118,754,500 of contingent liabilities was reachable only by sweeping the raw text.
    #
    # Disqualified lexically rather than by counting figures on the page: a numeric-density
    # test cannot tell this page from a legitimate one whose statement title sits low under a
    # finished table (合并利润表 beneath the balance sheet's closing 负债合计, which is the
    # standard PRC layout and is pinned by
    # test_a_chinese_statement_title_below_a_completed_table_is_recognised). The words differ;
    # the shapes do not.
    r"|[資资][產产][負负][債债][表报][日]?[後后][事][項项]|[日][後后][事][項项]"
    #
    # TWO MORE SHAPES OF THE SAME SWALLOWING, measured on a 287-page CAS filing (10972689):
    #
    #   78、现金流量表项目          note 78, "cash flow statement ITEMS" — PDF p.227. It matched
    #                            现金流量表 STRONG at 0.95 and carried pp.228-230 as continuations.
    #   资产负债表日存在的重要承诺    "significant commitments existing AT the balance sheet DATE"
    #                            — PDF p.268, read as a balance-sheet face.
    #
    # A statement name with 项目 after it names that statement's LINE ITEMS, which is what a CAS
    # filing calls the note decomposing them — 合并财务报表项目注释 heads the whole notes block. And
    # 资产负债表日 is a DATE: whatever follows, the heading is about the date, so this generalises
    # the 日后事项 arm above rather than sitting beside it as a second special case.
    #
    # THE DATE ARM REQUIRES A NON-DIGIT AFTER 日, which is what keeps it off a real title. A PRC
    # balance sheet heads its page 合并资产负债表 with 2024年12月31日 under it, and
    # `_title_candidates` joins adjacent lines into one candidate — so the text tested here really
    # can be "合并资产负债表 2024年12月31日". There 日 ends the candidate and the lookahead fails;
    # in 资产负债表日存在 and 资产负债表日后事项 a Han character follows and it matches.
    #
    # BOTH ARMS ONLY EVER REFUSE A TITLE. They cannot admit a page the lexicon did not already
    # match, which is why they are shipped while the numbered-heading widening they were developed
    # alongside is not: that one changes `PageFeat.note_heading` on every page of every filing and
    # moved 6,605 of 13,772 figures across six CAS filings, including a tenfold collapse in
    # 3bfe0c0e's gross profit. Measured separately, these two move nothing.
    r"|(?:[資资][產产][負负][債债]|[利][潤润]|[損损]益|[現现]金流[量動动]|[權权]益[變变][動动]"
    r"|[財财][務务][報报])[報报]?表\s*[項项]目"
    r"|[資资][產产][負负][債债][表报]\s*日(?=[^\d\s])"
    r"|^\s*(?:contents|index|目[錄录])\s*$", re.I)

# Statement-ish but unresolved. Logged onto the document so lexicon coverage is measurable instead of
# guessed: a filing whose titles are all recognised and one whose titles are all missed both look
# like silence otherwise.
_TITLE_HINT = re.compile(
    r"statements?\b|balance\s+sheet|account\b|[報报]?表\s*[（(]?\s*$|[報报]表", re.I)

_NOTES_BANNER = [
    r"notes?\s+to\s+(?:the\s+)?(?:consolidated\s+|unconsolidated\s+|standalone\s+)?"
    r"financial\s+statements",
    r"notes?\s+to\s+(?:the\s+)?accounts",            # legacy HK GAAP
    r"notes\s+forming\s+part\s+of\s+the",
    r"(?:material|significant)\s+accounting\s+polic",
    r"[財财][務务][報报]?表?附\s*[註注]",
    r"[合併合并綜综]+[財财][務务][報报]表附\s*[註注]",
    r"(?:重要|主要)[會会][計计]政策",
]

_BACKMATTER = re.compile(
    r"five[\s-]?year\s+(?:financial\s+)?summary|financial\s+(?:summary|highlights)"
    r"|[五][年][財财][務务][摘概][要要]|[財财][務务][摘概][要要]", re.I)

# THE FILER'S NAME IS NOT A SCOPE MARKER, and it is printed where one would be. ``_title_candidates``
# joins the issuer-name line onto the statement title so a title split over two lines is matched as
# one, which means the text a scope is read from is routinely
# "SUNRISE DEVELOPMENT COMPANY LIMITED BALANCE SHEET" — carrying both "Company" and "Group" for
# reasons that say nothing about whose figures the page presents. A corporate suffix AFTER the token
# is what separates a name from a marker: "… Company Limited" names the filer, "… of the Company"
# names the entity. The stakes are asymmetric now that this verdict decides a basis — a name read as
# a marker moves a whole filing's figures to the wrong entity, while a marker read as a name only
# leaves the page on the consolidated default — so the guard is applied to BOTH sides.
_CORP_SUFFIX = (r"(?:limited|ltd\.?|plc|inc\.?|incorporated|corporation|corp\.?|holdings?|group"
                r"|company|companies|pte|llc|llp|s\.?a\.?|n\.?v\.?|a\.?g\.?)")

# Scope. Consolidated is tested FIRST, because "…of the Company and its subsidiaries" contains the
# word Company and must not be read as the company-only statement.
_SCOPE_CONSOL = re.compile(
    rf"\bconsolidated\b|\bgroup\b(?!\s+{_CORP_SUFFIX}\b)|[合][併并]", re.I)
_SCOPE_COMPANY = re.compile(
    rf"\bcompany\b(?!\s+{_CORP_SUFFIX}\b)|\bthe\s+bank\b(?!\s+{_CORP_SUFFIX}\b)"
    r"|\bparent\b|\bstandalone\b|\bunconsolidated\b"
    r"|[母][公][司]|[本][公][司]", re.I)
# 綜合 vs 综合. Traditional HK usage is 綜合 = consolidated (the comment on ``_ZH_CI_AMBIG`` above says
# so), and 綜合損益及其他全面收益表 — the commonest Traditional face title there is — is the GROUP's.
# Refusing it as a consolidation marker because a comprehensive-income token follows answers the
# STATEMENT question in the SCOPE test, and on a bilingual filing whose Chinese statements repeat the
# English ones past the notes it labelled the Group's figures as the Company's. The lookahead is
# kept for the PRC Simplified form, where 综合 really does mean comprehensive and 合并 is consolidated.
_ZH_CONSOL_AMBIG = re.compile(r"綜合|综合(?!收益|[損损]益|全面|[虧亏][損损])")

# Prose pages that discuss the statements without being one.
_NARRATIVE = re.compile(
    r"\bin\s+our\s+opinion\b|\bwe\s+have\s+audited\b|\bindependent\s+auditor"
    r"|\bdirectors'?\s+report\b|\bkey\s+audit\s+matters?\b|\bbasis\s+for\s+opinion\b"
    r"|[核][數数][師师][報报][告告]|[董][事][會会][報报][告告]|[獨独][立][核][數数][師师]", re.I)

# THE SECTION OF THE REPORT THIS PAGE IS IN, as its own running header names it.
#
# An integrated annual report is built out of named sections and prints the section's name at the
# top of every page of it. That header is the one piece of evidence that separates a statement
# title from a discussion OF a statement, and nothing was reading it.
#
# MEASURED on Asian Paints' Integrated Annual Report 2025-26, 293 pages of which about 110 are
# financial. Page 40 is the Management Discussion and Analysis' ten-year review, and its table
# carries the mid-page heading "INCOME STATEMENT" — a perfect match for the P&L pattern, covering
# the whole of its own line, so `_mid_page_statement` resolved it and the page arrived with a
# STRONG TITLE worth +6 to the face state. Page 42 did the same with "Strong balance sheet
# supporting".
#
# WHAT THAT COST IS NOT ONE PAGE, AND THAT IS WHY IT MATTERS. `(_FACE, _PRE)` costs 8.0 — front
# matter may become a face but a face may not become front matter again — while `(_FACE, _NOTES)`
# is free and `(_NOTES, _NOTES)` is free. So once page 40 latched a face, the 140 pages of ESG,
# value-creation, governance and statutory narrative that follow it could only be NOTES, and their
# prose became bindable note rows: 220 of 293 pages came out NOTES, and "Read more at Page"
# published a figure onto `bs_nca__other_non_current_assets`.
#
# TESTED AGAINST THE TITLE ZONE ONLY, not the body text, because that is where a running header
# is; the body of a genuine note may discuss management's analysis without being part of it.
# "Financial Statements" is deliberately ABSENT — it is the header the real statements carry, and
# pages 180-183 of that filing print it.
#
# INERT ON A FILING WITH NO SUCH HEADERS, which is the whole shipped corpus: an HKEX annual report
# does not label its pages this way, so nothing matches and no page moves. Where an HKEX filing
# DOES carry a Management Discussion and Analysis section, calling those pages narrative is correct
# — they are not statements either.
_REPORT_SECTION = re.compile(
    r"management\s+discussion\s+and\s+analysis"
    r"|business\s+responsibility\s+and\s+sustainability\s+report|\bbrsr\b"
    r"|report\s+on\s+corporate\s+governance|corporate\s+governance\s+report"
    r"|board'?s\s+report|corporate\s+overview|statutory\s+reports?"
    r"|notice\s+of\s+(?:the\s+)?annual\s+general\s+meeting"
    r"|value\s+(?:proposition|creation\s+model)", re.I)

# The notes section opens at note 1 even when a filing omits the banner.
_NOTE_ONE = re.compile(r"(?m)^\s*(?:note\s*)?1[.)、]?\s+[A-Za-z一-鿿]{2,}")
# A numbered note heading, e.g. "14. Cash and cash equivalents" / "14 现金及现金等价物".
_NUMBERED_HEADING = re.compile(r"(?m)^\s*(?:note\s*)?\d{1,2}[.)、]?\s+[A-Za-z一-鿿]{2,}")
# A NUMBERED NOTE HEADING IN THE CJK FORM — "10、存货", "78、现金流量表项目", "20、投资性房地产".
#
# WHY IT IS A SEPARATE PATTERN AND NOT AN ARM OF `_NUMBERED_HEADING`. That one requires WHITESPACE
# after the number, and CJK typesetting puts none after an ideographic comma, so no numbered note
# heading in a PRC CAS filing matches it — `PageFeat.note_heading` was False on every one of them,
# costing the emission both halves of its note evidence (+2.5 for NOTES, -3.0 against FACE). What
# remained was the numeric-density point, FACE +1.0 against NOTES +0.5, so FACE won each page by
# 0.5; and with `(_FACE, _FACE)` free and `(_NOTES, _FACE)` at 3.0 the decode had no reason to
# leave. Measured on a 287-page Shenzhen filing: 119 of 287 pages came out FACE, including a
# 93-page unbroken run through the notes, every page of it at 0.45-0.53 confidence because no title
# matched on any of them. A face page never becomes a note, so those disclosures were unreachable.
#
# RELAXING THE LATIN ARM INSTEAD IS WHAT MUST NOT BE DONE, and it was measured too. `_features`
# sets `note_heading` from a `.search()` over the WHOLE page text, so anything that pattern gains it
# gains on every page of every filing: "1 January 2024", "31 December 2024" and every
# three-digit-then-word line on an untitled STATEMENT page would read as a note heading. Swept, that
# moved 7,419 of 14,018 figures across seven filings and took turnover on one from 408,721,552 to
# 820,695,961.
#
# SO THIS ONE IS POSITION-BOUNDED, and that is the whole safety of it: it is asked only of the
# page's opening lines, through `_opens_with_zh_note_heading`, never of the page's body. A
# statement's face page opens with its title and its column headings; "10、存货" at the top of a
# page is a note heading and nothing else. The Latin behaviour is unchanged, byte for byte.
_ZH_NUMBERED_HEADING = re.compile(r"^\s*(?:note\s*)?\d{1,3}[.、)]\s*[一-鿿]{2,}")

# A page's repeating running header, skipped so a title is read from the page's own heading.
_RUNNING_HEADER = re.compile(r"annual report|interim report|年報|年度報告|中期報告", re.I)
_NUM_TOKEN = re.compile(r"\(?-?[\d,]+\.?\d*\)?")
# A line carrying a printed AMOUNT — a figure with thousands separators, or one set to two
# decimals. Deliberately not "a line containing a number": every page prints its own folio
# ("154 / 256"), its report year and its period captions, and none of those is a statement line.
# What this decides is whether a statement is still running ABOVE a title printed part-way down a
# page, which is a question about content and not about how far down the title sits — see
# ``PageFeat.amounts_above_title``.
_AMOUNT_LINE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}(?!\d)")
# A split title runs to two or three short lines; beyond that a "run" of heading-shaped lines is
# the title plus the top of the table.
_JOIN_MAX_LINES = 3
_JOIN_MAX_CHARS = 90

# A statement face never titles three different statements; a contents page does.
_MAX_DISTINCT_TITLES = 3
# Which of the resolved names the rest of the pipeline understands. `comprehensive_income` is folded
# into profit_and_loss for page.statement because ontology mapping scopes candidate concepts by this
# value and no template declares a separate OCI statement — a page tagged with a statement the
# template does not carry would reject every concept on it. The finer label is kept in evidence.
_STATEMENT_ALIAS = {"comprehensive_income": "profit_and_loss"}


@dataclass
class PageFeat:
    """One page's local evidence, before the decode weighs it against its neighbours."""

    index: int = 0
    title_lines: list[str] = field(default_factory=list)
    statement: str | None = None
    oci_combined: bool = False
    title_ambig: bool = False
    matched_title: str | None = None
    matched_title_y: float | None = None
    # Whether a statement is still RUNNING above this page's title: an amount is printed above it.
    # The evidence that a page carries the tail of one statement and the head of the next, which
    # is what lets `pdf_extract` read the two halves as the statements they belong to.
    amounts_above_title: bool = False
    # A SECOND exact title printed BELOW the first one, with a statement's figures between them —
    # the page ends in a different statement (or a different entity's statement) from the one it
    # opened with. See `_closing_statement`.
    closing_statement: str | None = None
    closing_title: str | None = None
    closing_title_y: float | None = None
    unmapped: list[str] = field(default_factory=list)
    strong_title: bool = False
    narrative: bool = False
    notes_banner: bool = False
    note_heading: bool = False
    note_one: bool = False
    backmatter: bool = False
    numeric_density: float = 0.0
    scope: str | None = None
    scope_columns: list[str] = field(default_factory=list)


def _looks_like_heading(line: str) -> bool:
    """A statement title is a short heading line, not a sentence. Auditor prose such as "We audited
    the statement of profit or loss …" mentions face phrases but is long and ends like a sentence."""
    s = line.strip()
    if not s or len(s) > 110:
        return False
    if s.endswith((".", ";", ":", ",", "。", "，", "、", "；", "：")):
        return False
    # At most ONE figure. A title may carry a year; a data row carries a caption and its amounts
    # ("Revenue   Note 5   45,230"), and those rows were being joined onto the title above them —
    # the classification stayed right but `matched_title` came out as the title plus half the table,
    # which is the evidence a reader checks and the text scope resolution reads.
    if len(_NUM_TOKEN.findall(s)) > 1:
        return False
    return sum(ch.isdigit() for ch in s) <= 8


def _title_zone(lines: list[dict], limit: int = 8) -> list[dict]:
    """The page's heading lines: the first few non-empty ones, minus the leading page number and the
    repeating running header, which sit above a real title."""
    out: list[dict] = []
    for line in lines:
        s = line["text"].strip()
        if not s or re.fullmatch(r"\d{1,4}", s) or _RUNNING_HEADER.search(s):
            continue
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _title_candidates(lines: list[dict]) -> list[dict]:
    """Heading-like lines, plus joins of consecutive heading-like lines so a title split across two
    lines ("CONSOLIDATED STATEMENT OF" / "CASH FLOWS") is matched as one."""
    cands = [dict(c) for c in lines if _looks_like_heading(c["text"])]

    def joined(run: list[dict]) -> None:
        """Join a run, but only as far as a TITLE plausibly runs.

        A genuinely split title is short ("CONSOLIDATED STATEMENT OF" / "CASH FLOWS"). Joining a
        whole run unbounded swept the first data rows onto the end of the title, because a row
        carrying one figure is heading-shaped by every other measure — so the cap is on the join,
        not on the line.
        """
        if len(run) < 2:
            return
        text = run[0]["text"]
        for nxt in run[1:_JOIN_MAX_LINES]:
            candidate = f"{text} {nxt['text']}"
            if len(candidate) > _JOIN_MAX_CHARS:
                break
            text = candidate
        if text != run[0]["text"]:
            cands.append({**run[0], "text": text})

    run: list[dict] = []
    for line in lines:
        if _looks_like_heading(line["text"]):
            run.append(line)
        else:
            joined(run)
            run = []
    joined(run)
    return cands


# A STATEMENT TITLE PRINTED WITH ITS DATE ON THE SAME LINE, which an Indian annual report does as a
# matter of course: "Standalone Balance Sheet as at 31 March 2025", "Statement of Profit and Loss for
# the year ended 31st March, 2025", "Balance Sheet as at March 31, 2025". The date gives the line two
# numbers, so `_looks_like_heading` refuses it and the page was never a face. What follows the title
# is a date phrase and nothing else, so the line is offered again WITHOUT it — the title and only the
# title, still subject to every test a title is. Switched on for an Indian filing only
# (`services.regime`); the text a candidate came from is kept, because the title's position on the
# page is found by that text.
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_TITLE_DATE = (rf"(?:\d{{1,2}}(?:st|nd|rd|th)?[\s.,\-/]*{_MONTH}[\s.,\-/]*\d{{4}}"
               rf"|{_MONTH}[\s.]+\d{{1,2}}(?:st|nd|rd|th)?[\s.,]*\d{{4}}"
               r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})")
_TITLE_DATE_TAIL = re.compile(
    r"^(?P<title>.+?)[\s,]+(?:as\s+(?:at|on)|for\s+the\s+(?:financial\s+)?(?:year|period|quarter|"
    r"half[\s-]?year)\s+(?:ended|ending)|(?:year|period)\s+ended|ended|on)\s+"
    + _TITLE_DATE + r"\s*\.?\s*$", re.IGNORECASE)


def _without_date_tail(text: str) -> str | None:
    """The title part of a heading line that ends in its own date phrase, or None."""
    m = _TITLE_DATE_TAIL.match((text or "").strip())
    if not m:
        return None
    title = m.group("title").strip(" ,-–—:")
    return title if title and _looks_like_heading(title) else None


def _dated_title_candidates(lines: list[dict]) -> list[dict]:
    """The lines of `lines` that are a title followed by its date, offered as the title alone."""
    out = []
    for line in lines:
        text = line["text"].strip()
        if len(text) > 160 or text.endswith((";", ",")):
            continue
        title = _without_date_tail(text)
        if title:
            out.append({**line, "text": title, "printed_as": text})
    return out


def _mid_page_statement(lines: list[dict], title_zone: list[dict], *, dated_titles: bool = False
                        ) -> tuple[str | None, bool, str | None, bool]:
    """Find an exact statement title that starts below a completed table on the same page.

    "EXACT" IS ENFORCED HERE AND WAS ONLY STATED. This path considers every heading-shaped line
    ANYWHERE below the title zone, which is far more text than the title zone offers, so the
    coverage floor that keeps a sentence from being read as a title has to be higher here than the
    0.35 `_TITLE_COVERAGE` calibrated for a printed title band.

    MEASURED: Asian Paints' page 42 is value-creation narrative whose line "Strong balance sheet
    supporting" gave "balance sheet" 13 of 30 characters — 0.43, comfortably over 0.35 — so the
    page resolved a balance-sheet title and latched a face in the middle of the front matter. At
    `_MID_PAGE_TITLE_COVERAGE` it does not, while a real mid-page title ("STATEMENT OF PROFIT AND
    LOSS" below a finished balance sheet, which is the case this function exists for) covers
    essentially all of its line.
    """
    zone_ids = {id(line) for line in title_zone}
    candidates = [dict(line) for line in lines if id(line) not in zone_ids
                  and _looks_like_heading(line["text"])]
    if dated_titles:
        candidates += _dated_title_candidates([line for line in lines if id(line) not in zone_ids])
    return _resolve_statement(candidates, coverage=_MID_PAGE_TITLE_COVERAGE)


def _closing_statement(lines: list[dict], title_zone: list[dict], title_y: float | None
                       ) -> tuple[str | None, str | None, float | None]:
    """The LAST exact statement title printed below this page's own title, as
    ``(statement, title, y)`` — only when a statement's figures are printed between the two.

    WHY THE PAGE'S FIRST TITLE IS NOT ENOUGH. A mainland filing prints its statements in numbered
    pairs and a short pair fits on one page: 河钢股份 000709 page 88 opens with 5、合并现金流量表 and
    prints 6、母公司现金流量表 at y=0.77, with the parent company's statement running on to the top of
    page 89. The mid-page title was only ever looked for when the TOP of the page named nothing, so
    page 88 recorded the Group's title alone, the run the next page continues was taken to be the
    Group's, and page 89's company rows above 7、合并所有者权益变动表 were filed CONSOLIDATED — the
    published consolidated operating cash flow was 13,718,703,924.02, the Group's printed
    9,678,206,759.05 plus the company's 4,040,497,164.97, and the same for investing, financing and
    closing cash in both years.

    The rows ON the page are not this function's concern — `row_reconstruct` already switches the
    basis at a mainland statement title part-way down a page (`_title_entity_basis`). What it decides
    is what the page hands ON: the statement, and the entity, that is still running when it ends.

    The same strict floor `_mid_page_statement` applies, because this reads the same region of the
    page; and a title with no figures between it and the page's own title is not a second statement
    but the first one's title printed twice.
    """
    if title_y is None:
        return None, None, None
    zone_ids = {id(line) for line in title_zone}
    below = [line for line in lines if id(line) not in zone_ids
             and float(line.get("y", 0.0)) > title_y and _looks_like_heading(line["text"])]
    for cand in sorted(below, key=lambda line: float(line.get("y", 0.0)), reverse=True):
        name, _combined, title, _ambig = _resolve_statement([dict(cand)],
                                                            coverage=_MID_PAGE_TITLE_COVERAGE)
        if not name:
            continue
        y = float(cand.get("y", 0.0))
        if any(title_y < float(line.get("y", 0.0)) < y and _AMOUNT_LINE.search(line["text"])
               for line in lines):
            return name, title, y
        return None, None, None
    return None, None, None


def _title_scope(title: str | None) -> str | None:
    """The entity a statement TITLE names on its own — the title half of `_scope_of`, with neither
    the column-header band nor the past-the-notes inference."""
    if not title:
        return None
    if _SCOPE_CONSOL.search(title) or _ZH_CONSOL_AMBIG.search(title):
        return "consolidated"
    if _SCOPE_COMPANY.search(title):
        return "company"
    return None


# THE CASH-FLOW SUPPLEMENT, which a mainland filing prints AS A NOTE. 现金流量表补充资料 is the
# indirect-method reconciliation of the Group's operating cash flow, and the classifier reads it as a
# cash-flow face page on purpose (see `_TITLE_QUALIFIER`): it is the only place a CAS filing prints
# the `cf_oper_indirect__*` lines. But it is a note of the chapter it sits in, not a statement, and
# two things follow that a statement page does not have:
#
#   * its ENTITY is the chapter's. It sits in 七、合并财务报表项目注释 on all three mainland reference
#     filings, so it is the Group's — while the past-the-notes rule in `_scope_of` called it the
#     Company's, because it "re-presents" a statement already shown as consolidated. 688008's
#     standalone operating cash flow was published as 1,904,623,231.54: the company's printed
#     213,301,725.40 plus the Group's 1,691,321,506.14 from the supplement.
#   * its EXTENT is the reconciliation's. It is titled part-way down a page of other notes, and its
#     reconciliation closes at 经营活动产生的现金流量净额 — what follows is the note's other tables
#     (non-cash activities, the cash movement, the composition of cash) and then the next note. Read
#     as a face, every one of those rows became a cash-flow row: 688008's cash composition
#     (现金的期末余额 6,698,931,684.67, 可随时用于支付的银行存款 6,651,096,406.08) was filed on
#     `cf_financing__other_financing_cash_flows`, and 300319's foreign-currency table after it.
_CF_SUPPLEMENT_TITLE = re.compile(r"补充资料|補充資料")
_CF_SUPPLEMENT_CLOSE = re.compile(r"^\s*(?:经营活动产生的现金流量净额|經營活動產生的現金流量淨額)")
# A notes chapter about the statements' line items — 七、合并财务报表项目注释 for the Group's,
# 十九、母公司财务报表主要项目注释 for the company's. Matched on a ROW (688008 prints the numeral
# and the title as two lines at one height).
_STATEMENT_NOTES_CHAPTER = re.compile(
    r"^\s*[一二三四五六七八九十]{1,3}\s*[、.．]\s*(?P<entity>.{0,8}?)"
    r"(?:财务报表|財務報表|会计报表|會計報表)(?:主要)?项目(?:注释|附注|註釋|附註)\s*$")


def _rows_of(lines: list[dict], tol: float = 1.5) -> list[tuple[float, str]]:
    """Lines printed at one height joined into one row, top-down, as ``(y, text)``."""
    rows: list[tuple[float, list[str]]] = []
    for line in lines:
        y = float(line.get("y", 0.0))
        if rows and abs(rows[-1][0] - y) <= tol:
            rows[-1][1].append(line["text"])
        else:
            rows.append((y, [line["text"]]))
    return [(y, " ".join(t.strip() for t in texts)) for y, texts in rows]


def _statement_notes_chapter(lines: list[dict], below_y: float | None = None) -> str | None:
    """The entity of the LAST statement-notes chapter heading on these lines — ``"company"`` for the
    parent company's, ``"consolidated"`` for the Group's — or None when none is printed. ``below_y``
    (in the lines' own units) limits the search to headings printed above that height."""
    found: str | None = None
    for y, text in _rows_of(lines):
        if below_y is not None and y >= below_y:
            break
        m = _STATEMENT_NOTES_CHAPTER.match(text)
        if m:
            found = "company" if _SCOPE_COMPANY.search(m.group("entity") or "") else "consolidated"
    return found


def _cf_supplement_extent(path: list[str], feats: list, cache: list, *, log=None
                          ) -> tuple[list[str], dict[int, dict]]:
    """Bound each cash-flow supplement printed in the notes to its own reconciliation.

    Returns the corrected path and, per page POSITION, the evidence `pdf_extract` reads the page
    with: ``supplement`` on the page the supplement is titled on, ``notes_above_title`` when another
    note's figures are printed above that title, and ``face_ends_at_y`` (a page fraction) on the
    page its reconciliation closes on.

    A FACE run counts as a supplement run only when it sits inside the notes (some page before it is
    NOTES) and its first titled page is a cash-flow title naming 补充资料. Untitled pages of the
    run BEFORE that title are notes — a supplement's run begins at its title, and the decode put
    them in FACE on figure density alone (000709 page 160, note 55's tables). Untitled pages AFTER
    the page the reconciliation closes on are notes for the same reason (300319 page 171: the cash
    composition and note 59's foreign-currency table). When the closing row is not found the run is
    left exactly as it was: the extent is unknown, and a guess would drop real lines.

    Nothing outside the notes region is touched, so no statement face — and nothing on a filing
    that prints no supplement, which is every HKFRS one — can move.
    """
    out = list(path)
    extent: dict[int, dict] = {}
    if not feats or len(feats) != len(path) or len(cache) != len(path):
        return out, extent
    first_notes = next((i for i, s in enumerate(path) if s == _NOTES), None)
    if first_notes is None:
        return out, extent
    i = first_notes + 1
    while i < len(path):
        if path[i] != _FACE:
            i += 1
            continue
        j = i
        while j + 1 < len(path) and path[j + 1] == _FACE:
            j += 1
        titled = [k for k in range(i, j + 1) if feats[k].matched_title is not None]
        head = titled[0] if titled else None
        if (head is not None
                and _STATEMENT_ALIAS.get(feats[head].statement or "", feats[head].statement)
                == "cash_flow"
                and _CF_SUPPLEMENT_TITLE.search(feats[head].matched_title or "")):
            close_at: tuple[int, float | None] | None = None
            for k in range(head, j + 1):
                if k > head and feats[k].matched_title is not None:
                    break                        # another statement's title: the run is not ours
                lines, height = cache[k]
                floor = (feats[k].matched_title_y or 0.0) * (height or 1.0) if k == head else -1.0
                hit = next((line for line in lines if float(line.get("y", 0.0)) > floor
                            and _CF_SUPPLEMENT_CLOSE.match(line["text"])), None)
                if hit is None:
                    continue
                hy = float(hit.get("y", 0.0))
                tol = 0.006 * (height or 1.0)
                nxt = min((float(line.get("y", 0.0)) for line in lines
                           if float(line.get("y", 0.0)) > hy + tol), default=None)
                close_at = (k, (nxt / height) if (nxt is not None and height) else None)
                break
            if close_at is not None:
                extent[head] = {"supplement": True,
                                "notes_above_title": bool(feats[head].amounts_above_title)}
                k_close, end_y = close_at
                extent.setdefault(k_close, {})["face_ends_at_y"] = end_y
                before = list(range(i, head))
                after: list[int] = []
                for k in range(k_close + 1, j + 1):
                    if feats[k].matched_title is not None:
                        break                    # a statement titled of its own: not the supplement's
                    after.append(k)
                for k in before + after:
                    out[k] = _NOTES
                if log:
                    log(f"classify:cf_supplement=page{feats[head].index}"
                        f"(closes_on={feats[k_close].index},face_ends_at_y={end_y},"
                        f"notes_above_title={extent[head]['notes_above_title']},"
                        f"to_notes={[feats[k].index for k in before + after]})")
        i = j + 1
    return out, extent


def _anchored(t: str) -> bool:
    """English structural anchor. Chinese patterns are self-anchoring — deliberately NO end-of-line
    anchor, which rejected every continuation page titled 綜合權益變動表(續) and every bilingual
    one-line title."""
    return bool(_EN_ANCHOR.search(t))


# How much of a title candidate the statement's name has to BE, for the candidate to count as that
# statement's title. A note's prose refers to the statement it belongs to — "These items are included
# in “cost of sales” in the consolidated statement of profit or loss" — and the title pattern matches
# perfectly inside that sentence. Nothing else the classifier has separates the two: the sentence is
# heading-shaped by every other measure, ``_TITLE_NEGATIVE`` only knows note HEADINGS, and the false
# title then erases the evidence against itself (the page's own numbered note heading is ignored once
# a strong title is found). What does separate them is proportion. A printed title IS the statement's
# name, plus at most a qualifier and its translation; a sentence that merely names the statement is
# mostly other words.
#
# 0.35, measured against two real HKEX filings: the lowest genuine title in either covers 0.52 — a
# one-line bilingual "CONSOLIDATED STATEMENT OF CASH FLOWS 綜合現金流量表" — and no page that resolved
# a statement before resolves a different one now. The three prose sentences that were being served
# as face profit-and-loss pages are far below it.
_TITLE_COVERAGE = 0.35


# THE SAME QUESTION ASKED OF A MID-PAGE LINE, where the answer has to be stricter.
# `_TITLE_COVERAGE` is calibrated for the printed TITLE BAND — a handful of lines at the top of the
# page, where a statement's name appearing at all is already strong evidence. `_mid_page_statement`
# considers every heading-shaped line anywhere below that band, which is most of the page, so the
# same floor admits sentence fragments: Asian Paints' "Strong balance sheet supporting" gives
# "balance sheet" 0.43 of its line and latched a face in the front matter. A genuine mid-page title
# is the statement's name and nothing else, so it covers essentially all of its line.
_MID_PAGE_TITLE_COVERAGE = 0.70


# WHAT A STATEMENT'S TITLE IS MADE OF BESIDES ITS NAME, and must therefore not count against it.
#
# MEASURED, AND IT IS WHY THE STRICTER MID-PAGE FLOOR NEEDS THIS BESIDE IT. At 0.70 against the raw
# line, 河钢股份 000709 lost its balance sheet: a CAS filing prints each statement's title MID-PAGE,
# ENUMERATED and QUALIFIED by basis — "1、合并资产负债表" covers 0.56, "2、母公司资产负债表" 0.50,
# "3、合并利润表" 0.43 — so pages 81-86 resolved no statement, the first face moved from 81 to 87,
# and the run went from 505 figures and 32 checked relations to 292 and none. Those are exact
# titles. The enumerator names the statement's position in the report and the qualifier names
# which entity's statement it is; neither makes the line a sentence.
#
# A BILINGUAL TITLE IS THE SAME TITLE TWICE, so it is measured in the script the match is in: an
# HKEX title "CONSOLIDATED STATEMENT OF CASH FLOWS 綜合現金流量表" is 0.52 of its raw line (the
# title-band test records this) and would fail the mid-page floor in exactly the same way.
#
# What is NOT stripped is what the floor exists to refuse: "Strong balance sheet supporting" has no
# enumerator, no qualifier and one script, so it stays at 0.43 and stays refused.
_TITLE_ENUMERATOR = re.compile(
    r"^\s*(?:[（(]?\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3}|[ivxIVX]{1,4}|[A-Za-z])\s*[)）]?\s*[、.．:：)]"
    r"|[（(]\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3})\s*[)）])\s*")
# THREE KINDS OF WORD IN IT, each measured on the corpus rather than supposed:
#   * WHOSE STATEMENT — 合并/母公司/Consolidated/Company. 000709's "1、合并资产负债表".
#   * WHOSE EQUITY — 所有者/股东. 000709's "7、合并所有者权益变动表": the pattern names 权益变动表,
#     so "owners'" counted against a title that is exactly the statement's name (0.625).
#   * ITS SUPPLEMENTARY SCHEDULE — 补充资料. "现金流量表补充资料" is the one place a CAS filing prints
#     the indirect-method reconciliation, which the run reads as the cash-flow statement and which
#     is what fills `cf_oper_indirect__*`: refusing it cost 688008 and 000709 their indirect-method
#     lines (29 and 126 figures), where the title band's 0.35 floor had always admitted it.
_TITLE_QUALIFIER = re.compile(
    r"合并|合併|母公司|本公司|本集团|本集團|集团|集團|公司|所有者|股东|股東"
    r"|补充资料|補充資料"
    r"|（\s*续\s*）|（\s*續\s*）|\(\s*continued\s*\)"
    r"|\bsupplementary\s+information\b"
    r"|\b(?:consolidated|standalone|separate|company|group|parent|the)\b", re.I)
_LATIN_TEXT = re.compile(r"[A-Za-z][A-Za-z\s,&'’()/-]*")
_HAN_TEXT = re.compile(r"[\u3001\u3400-\u9fff（）()]+")


def _title_body(match: str, text: str) -> str:
    """The part of a title line a statement's NAME has to cover: the enumerator stripped, only the
    script the match is written in, and the basis qualifiers removed."""
    body = _TITLE_ENUMERATOR.sub("", text or "", count=1)
    has_latin, has_han = bool(re.search(r"[A-Za-z]", body)), bool(re.search(r"[\u3400-\u9fff]", body))
    if has_latin and has_han:
        script = _HAN_TEXT if re.search(r"[\u3400-\u9fff]", match or "") else _LATIN_TEXT
        body = " ".join(run.strip() for run in script.findall(body) if run.strip())
    body = _TITLE_QUALIFIER.sub(" ", body)
    return re.sub(r"\s+", " ", body).strip()


def _covers_title(match: str, text: str, coverage: float | None = None) -> bool:
    """Is ``match`` most of ``text`` — i.e. is this line the statement's name rather than a
    sentence that happens to contain it?

    With the default floor the RAW line is the denominator, exactly as the title band was
    calibrated. Only the stricter mid-page floor measures the title's BODY (`_title_body`), because
    that floor is the one a CAS filing's enumerated, qualified titles fell under."""
    if coverage is None:
        return len(match) / max(len(text), 1) >= _TITLE_COVERAGE
    body = _title_body(match, text)
    named = _TITLE_QUALIFIER.sub(" ", match or "")
    named = re.sub(r"\s+", " ", named).strip() or (match or "")
    return len(named) / max(len(body), 1) >= coverage


# A SUMMARY OF A STATEMENT IS NOT THE STATEMENT. A filing's Financial Highlights page prints
# "SUMMARY OF STATEMENT OF PROFIT OR LOSS" / "損益表摘要" over a two-year extract with a % change
# column, and that title matches the P&L pattern exactly — so the page arrived carrying a STRONG
# title, which is worth +6 to the face state against the -4 that ``_BACKMATTER`` takes off it.
#
# Nothing else could catch it. The highlights page sits in FRONT matter, and the state that exists
# for summaries (POST) is reachable only at a cost of 6.0 from PRE, because back matter does not
# come before the statements. So the decode's real choice was PRE or FACE, and the strong title won.
#
# Measured on the China SCE 2023 filing: its highlights page was read as a P&L face page and
# published EIGHT rows, three of which mapped — revenue, gross profit, and profit attributable to
# owners — each colliding on its canonical key with the same concept read from the real P&L twenty
# pages later, at different figures. The % change column landed as a third period.
#
# The qualifier is refused in either language and in either position: English puts it before
# ("summary of …"), Chinese after ("損益表摘要").
_SUMMARY_TITLE = re.compile(
    r"\bsummar(?:y|ies)\b|\bhighlights?\b|\bextract(?:ed|s)?\s+from\b"
    r"|[摘概][要要]|[概][覽览]", re.I)


def _resolve_statement(cands: list[dict], *, coverage: float | None = None
                       ) -> tuple[str | None, bool, str | None, bool]:
    """(statement, oci_combined, matched_title, ambiguous).

    ``coverage`` overrides the `_covers_title` floor. Defaulted so the title-band path and the
    worksheet path keep exactly the floor they were calibrated with, and only
    :func:`_mid_page_statement` asks for a stricter one.

    POSITION first, then match length — never list order. Pages genuinely carry two candidates (an
    equity-statement tail above a cash-flow title; P&L above OCI), and longest-match-at-topmost-y is
    what picks the right one. Reverting to list order reintroduces the equity-tail bug.

    A match only counts if it is most of the candidate line (:func:`_covers_title`), which is what
    keeps a note's prose from being read as the statement it refers to.
    """
    hits: list[tuple[float, int, str, str]] = []
    names: set[str] = set()
    for c in cands:
        t = c["text"]
        if _TITLE_NEGATIVE.search(t):        # note heading / contents line / auditor prose
            continue
        if _SUMMARY_TITLE.search(t):         # a summary OF a statement is not the statement
            continue
        low = t.lower()
        best: tuple[int, str] | None = None
        for name, strong, weak in _STATEMENTS:
            for p in strong:
                m = re.search(p, low) or re.search(p, t)
                if m and _covers_title(m.group(0), t, coverage) and (best is None
                                                           or len(m.group(0)) > best[0]):
                    best = (len(m.group(0)), name)
            # ``anchored`` on the candidate itself, for text whose CONTEXT is the anchor. The
            # English-anchor test exists because a page's prose says "cash flows" constantly, so a
            # weak pattern alone would classify an auditor's paragraph; a worksheet TAB NAME is not
            # prose but a deliberate label of what the sheet holds, and "Cash Flow" on a tab means
            # exactly one thing. Page candidates never set it, so nothing about the page path moves.
            if c.get("anchored") or _anchored(t):
                for p in weak:
                    m = re.search(p, low) or re.search(p, t)
                    if m and _covers_title(m.group(0), t, coverage) and (best is None
                                                              or len(m.group(0)) > best[0]):
                        best = (len(m.group(0)), name)
        if best:
            hits.append((c.get("y", 0.0), -best[0], best[1], t))
            names.add(best[1])

    if not hits:
        return None, False, None, False
    if len(names) >= _MAX_DISTINCT_TITLES:   # a contents/index page lists them all at once
        return None, False, None, False

    hits.sort(key=lambda h: (h[0], h[1]))
    _, _, name, title = hits[0]
    combined = bool(_OCI_COMBINED.search(title))
    if combined:
        name = "profit_and_loss"
    ambig = bool(_ZH_CI_AMBIG.search(title)) and not combined
    return name, combined, title, ambig


# How many leading rows of a worksheet are read looking for its title, and how many text cells are
# taken from them. A statement title sits at the top of the sheet, above the column headings; reading
# further only offers the decode data rows to mistake for a heading.
_SHEET_TITLE_ROWS = 15
_SHEET_TITLE_CELLS = 12


def statement_of_sheet(sheet_name: str, cell_texts: list[str]) -> tuple[str | None, str | None]:
    """``(statement, matched_title)`` for one worksheet, or ``(None, None)``.

    The title vocabulary is NOT restated here. A worksheet's title is the same phrase as a printed
    page's — "Consolidated statement of financial position", 綜合財務狀況表 — so the candidates go
    through ``_title_candidates`` and ``_resolve_statement`` exactly as a page's lines do, and every
    rule they carry comes along: the strong/weak patterns per statement, the negative filter that
    rejects note headings and contents lines, the two-line join, the OCI-combined collapse, and the
    contents-page guard that refuses a sheet listing every statement at once.

    The line dicts are synthesised the way ``_page_lines`` synthesises them for a page whose spans
    cannot be read — ``y`` is an ordinal, not a coordinate — because ordering is all
    ``_resolve_statement`` needs from it.

    The SHEET NAME is offered first, at the topmost ordinal, so it wins a position tie against a
    title inside the sheet. It is the more reliable statement of what a sheet IS: a tab called
    "Balance Sheet" says so deliberately, whereas the first rows of a sheet may carry the entity name
    or a prior statement's tail. A name that matches nothing contributes no candidate at all, so an
    unhelpful "Sheet1" costs nothing.
    """
    lines: list[dict] = [{"text": (sheet_name or "").strip(), "y": -1.0, "size": 0.0,
                          "bold": False, "anchored": True}]
    for i, text in enumerate(cell_texts[:_SHEET_TITLE_CELLS]):
        if text and text.strip():
            lines.append({"text": text.strip(), "y": float(i), "size": 0.0, "bold": False})
    lines = [line for line in lines if line["text"]]
    if not lines:
        return None, None
    statement, _combined, matched, _ambig = _resolve_statement(_title_candidates(lines))
    return (_STATEMENT_ALIAS.get(statement or "", statement), matched)


def sheet_title_cells(sheet) -> list[str]:
    """The text cells of a worksheet's leading rows, in reading order — the input above."""
    out: list[str] = []
    for row in sheet.iter_rows(min_row=1, max_row=_SHEET_TITLE_ROWS, values_only=True):
        for value in row:
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return out


def _scope_of(title: str | None, lines: list[dict], page_h: float,
              repeat_after_notes: bool = False) -> tuple[str | None, list[str]]:
    """Scope from the title, plus column-header scope when a Group and a Company column sit side by
    side on one face page — routine in HK balance sheets, and the reason scope_columns exists."""
    scope = None
    if title:
        if _SCOPE_CONSOL.search(title) or _ZH_CONSOL_AMBIG.search(title):
            scope = "consolidated"
        elif _SCOPE_COMPANY.search(title):
            scope = "company"
        elif repeat_after_notes:
            # A face page printed past the notes, RE-PRESENTING a statement this filing has already
            # shown as the Group's and carrying no consolidation token of its own, is the Company's:
            # HK filings print it there, past note 40, titled only "Statement of financial position".
            #
            # THE RE-PRESENTATION IS THE EVIDENCE, not the page position, and the caller owns it.
            # Position alone is the ``seen_notes`` latch, which one front-matter line can set — a
            # registered-office address matches ``_NOTE_ONE`` — after which every face page in a
            # filing that titles its statements "Balance Sheet" would be read as the Company's and
            # the whole document would come out standalone. Requiring a Group presentation of the
            # SAME statement first is what the defect actually looks like (pp.348-349 repeat p.187,
            # which is why the spread summed them) and it refuses that latch, because a first
            # occurrence has nothing to repeat.
            scope = "company"
    band = " ".join(l["text"] for l in lines if l.get("y", 0.0) <= 0.42 * (page_h or 1.0))
    cols: list[str] = []
    if re.search(r"\bgroup\b|[本][集][團团]", band, re.I):
        cols.append("consolidated")
    if re.search(r"\bcompany\b|\bbank\b|[本][公][司]", band, re.I):
        cols.append("company")
    if len(cols) == 2:
        scope = "mixed"
    return scope, cols


# A line that is nothing but a page number, with the brackets or dashes some filings print
# around it: "12", "(12)", "- 12 -".
_FOLIO = re.compile(r"^[-–—(\[]?\s*(\d{1,4})\s*[-–—)\]]?$")
# How far into the page a folio can sit, as a fraction of the page height. Deliberately tight: the
# folio is printed in the margin, and the band immediately inside it holds the column headings of
# every statement — a top band of 0.10 would collect "2025" from the header of a balance sheet.
_FOLIO_BAND = 0.07


def _printed_folio(lines: list[dict], page_h: float) -> str | None:
    """The page's own printed page number, if it prints one.

    Read from the margins only, bottom preferred, because that is where a folio is set and because
    the further in the search reaches the more of the statement it can mistake for one. A year is
    never a folio — an annual report has no page 2025 — and that one exclusion is what keeps a
    column heading in the top margin from being read as the page number.
    """
    if not page_h:
        return None
    best: tuple[int, float, str] | None = None
    for line in lines:
        m = _FOLIO.match(line["text"].strip())
        if not m:
            continue
        folio = m.group(1)
        if re.fullmatch(r"(?:19|20)\d\d", folio):
            continue
        y = float(line.get("y", 0.0)) / page_h
        if y >= 1.0 - _FOLIO_BAND:
            zone, edge = 0, 1.0 - y            # bottom margin: where a folio normally sits
        elif y <= _FOLIO_BAND:
            zone, edge = 1, y
        else:
            continue
        if best is None or (zone, edge) < (best[0], best[1]):
            best = (zone, edge, folio)
    return best[2] if best else None


# How many of a page's own opening lines count as "the top". A statement continuation puts its
# folio, running header and column band there — six lines covers "191 | Annual Report … | 2025 |
# 2024 | Notes | HK$'000 | HK$'000" — and a note puts its numbered heading first.
_TOP_LINES = 7


def _opens_with_note_heading(lines: list[dict]) -> bool:
    """Whether a page OPENS as a note: a numbered note heading among its first lines.

    Not anywhere on the page. A statement's continuation page is full of note REFERENCES ("6(d)")
    and of rows that begin with a figure, and asking whether the page contains a numbered heading
    somewhere answers a different question than whether it starts one.
    """
    seen = 0
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text or _RUNNING_HEADER.search(text):
            continue
        if _NUMBERED_HEADING.match(text):
            return True
        seen += 1
        if seen >= _TOP_LINES:
            return False
    return False


def _opens_with_zh_note_heading(lines: list[dict]) -> bool:
    """Whether a page OPENS with a CJK numbered note heading — see `_ZH_NUMBERED_HEADING`.

    The same top-zone rule `_opens_with_note_heading` applies, and for the same reason its docstring
    gives: a statement's continuation page is full of note REFERENCES and of rows beginning with a
    figure, so asking whether the page CONTAINS such a heading somewhere answers a different
    question than whether it starts one.
    """
    seen = 0
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text or _RUNNING_HEADER.search(text):
            continue
        if _ZH_NUMBERED_HEADING.match(text):
            return True
        seen += 1
        if seen >= _TOP_LINES:
            return False
    return False


def _page_lines(page) -> tuple[list[dict], float]:
    """Lines as (text, y, size, bold), top-down. Read from the span dict rather than plain text
    because a title's position and weight are evidence the decode uses."""
    height = float(getattr(page.rect, "height", 0.0) or 0.0)
    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001 — a damaged page still yields plain text below
        data = None
    lines: list[dict] = []
    if data:
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans") or []
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                size = max((float(s.get("size", 0.0)) for s in spans), default=0.0)
                bold = any("bold" in str(s.get("font", "")).lower() for s in spans)
                y = float(line.get("bbox", (0, 0, 0, 0))[1])
                lines.append({"text": text, "y": y, "size": size, "bold": bold})
        lines.sort(key=lambda l: l["y"])
    if not lines:
        raw = page.get_text("text") or ""
        lines = [{"text": s.strip(), "y": float(i), "size": 0.0, "bold": False}
                 for i, s in enumerate(raw.splitlines()) if s.strip()]
    return lines, height


def _features(index: int, lines: list[dict], page_h: float, text: str, *,
              dated_titles: bool = False) -> PageFeat:
    f = PageFeat(index=index)
    zone = _title_zone(lines)
    f.title_lines = [l["text"] for l in zone]
    cands = _title_candidates(zone)
    if dated_titles:
        cands += _dated_title_candidates(zone)
    # A dated candidate's text is the title alone; the line it was printed as is what locates it.
    printed_as = {c["text"]: c["printed_as"] for c in cands if c.get("printed_as")}

    f.statement, f.oci_combined, title, f.title_ambig = _resolve_statement(cands)
    if f.statement is None:
        f.statement, f.oci_combined, title, f.title_ambig = _mid_page_statement(
            lines, zone, dated_titles=dated_titles)
        if dated_titles and title is not None and title not in printed_as:
            printed_as.update({c["text"]: c["printed_as"] for c in _dated_title_candidates(lines)})
    joined = " ".join(f.title_lines)
    # `_REPORT_SECTION` against the TITLE ZONE alone — a running header is printed there, and a
    # genuine note's body may discuss management's analysis without belonging to that section.
    f.narrative = bool(_NARRATIVE.search(joined) or _NARRATIVE.search(text[:1500])
                       or _REPORT_SECTION.search(joined))
    if f.narrative:
        # The auditor's report names every statement it audited, in bold, in the top band; a
        # management discussion prints the name of every statement it discusses.
        f.statement, title = None, None
    f.matched_title = title
    if title is not None and page_h:
        located = printed_as.get(title, title)
        hit = next((line for line in lines if line["text"].strip() == located), None)
        if hit is not None:
            f.matched_title_y = float(hit["y"]) / page_h
            # Anything with an AMOUNT above the title belongs to the statement that was running
            # before it. Page chrome is excluded by `_AMOUNT_LINE` itself rather than by a second
            # running-header test: the folio, the report year and the period caption all carry
            # digits and none of them carries a figure.
            f.amounts_above_title = any(
                line["y"] < hit["y"] and _AMOUNT_LINE.search(line["text"])
                for line in lines)
            # …and whether the page ENDS in another one. Asked of every titled page, top-zone or
            # mid-page alike, because both can be followed by a second statement's title.
            name, closing, closing_y = _closing_statement(lines, zone, float(hit["y"]))
            if name:
                f.closing_statement, f.closing_title = name, closing
                f.closing_title_y = closing_y / page_h
    f.strong_title = f.statement is not None

    f.notes_banner = any(re.search(p, joined, re.I) for p in _NOTES_BANNER)
    f.note_heading = (bool(_NUMBERED_HEADING.search(text))
                      or _opens_with_zh_note_heading(lines)) and not f.strong_title
    f.note_one = bool(_NOTE_ONE.search(text)) and not f.strong_title
    f.backmatter = bool(_BACKMATTER.search(joined))

    tokens = text.split()
    f.numeric_density = (sum(1 for t in tokens if _NUM_TOKEN.fullmatch(t)) / len(tokens)
                         if tokens else 0.0)

    if not f.statement:
        for c in cands:
            t = c["text"]
            if _TITLE_NEGATIVE.search(t) or len(t) > 120:
                continue
            if _TITLE_HINT.search(t):
                f.unmapped.append(t)

    f.scope, f.scope_columns = _scope_of(title, lines, page_h)
    return f


# ------------------------------------------------------------------ decode ---
# States, in document order. PRE and POST both surface as OTHER; they are separate states because
# what may follow them differs — front matter can become a face, back-matter should not.
_PRE, _FACE, _NOTES, _POST = "pre", "face", "notes", "post"
_STATES = (_PRE, _FACE, _NOTES, _POST)

# Cost of leaving state A for state B. Zero is "expected in a filing"; a large number is "possible,
# but the evidence had better be strong". NOTES → FACE is the load-bearing one: at 3.0 a genuine
# face title after the notes can win, which is how a Company-only balance sheet printed past note 40
# is recovered — the fixed region walk this replaces made that transition impossible.
_TRANSITION: dict[tuple[str, str], float] = {
    (_PRE, _PRE): 0.0, (_PRE, _FACE): 0.0, (_PRE, _NOTES): 1.0, (_PRE, _POST): 6.0,
    (_FACE, _FACE): 0.0, (_FACE, _NOTES): 0.0, (_FACE, _POST): 4.0, (_FACE, _PRE): 8.0,
    (_NOTES, _NOTES): 0.0, (_NOTES, _POST): 0.0, (_NOTES, _FACE): 3.0, (_NOTES, _PRE): 10.0,
    (_POST, _POST): 0.0, (_POST, _NOTES): 6.0, (_POST, _FACE): 6.0, (_POST, _PRE): 10.0,
}


def _emission(f: PageFeat, state: str) -> float:
    """How well this page's own evidence fits one state. Deliberately coarse: the decode's job is to
    combine weak local signals with document order, not to be certain page by page."""
    dense = 1.0 if f.numeric_density >= 0.18 else 0.0
    if state == _FACE:
        s = 6.0 if f.strong_title else 0.0
        s += dense
        s -= 6.0 if f.narrative else 0.0
        s -= 4.0 if f.notes_banner else 0.0
        s -= 3.0 if (f.note_heading or f.note_one) else 0.0
        s -= 4.0 if f.backmatter else 0.0
        return s
    if state == _NOTES:
        s = 6.0 if f.notes_banner else 0.0
        s += 4.0 if f.note_one else 0.0
        s += 2.5 if f.note_heading else 0.0
        s += dense * 0.5
        s -= 4.0 if f.strong_title else 0.0
        s -= 5.0 if f.narrative else 0.0
        s -= 4.0 if f.backmatter else 0.0
        return s
    if state == _POST:
        return 6.0 if f.backmatter else -2.0
    # PRE: prose, or simply nothing that looks like a statement or a note.
    s = 1.0
    s += 2.0 if f.narrative else 0.0
    s -= 3.0 if f.strong_title else 0.0
    s -= 3.0 if f.notes_banner else 0.0
    s -= 1.0 if dense else 0.0
    return s


def _notes_follow_the_face(path: list[str], log=None, feats: list | None = None) -> list[str]:
    """THE ORDERING INVARIANT: no notes page precedes the face of the statements.

    A filing states its statements and then explains them. The notes to the financial statements
    are printed AFTER the face — always — so a page decoded as NOTES before any face page has been
    seen is not a note, whatever its own evidence looked like.

    The evidence that produces such a page is real and common: the numbered-heading feature
    (``note_heading``) fires on any "1. …" / "2. …" run, and front matter is full of them —
    a contents page, an auditor's report with numbered paragraphs, a corporate-information page,
    a financial-highlights page quoting statement titles. The decode weighs those against document
    order, but PRE -> NOTES costs only 1.0, so a strong enough numbered-heading page ahead of the
    statements can enter the notes state early. Everything after it then reads as notes-or-later,
    because NOTES -> FACE costs 3.0.

    THIS IS NOT THE SAME CLAIM AS "the face never follows the notes", which would be false: an
    HKEX filing prints the Company's own balance sheet PAST note 40, and recovering that page is
    what the NOTES -> FACE transition exists for. Only the FIRST face page is anchored here —
    anything after it is left exactly as the decode left it.

    Fail-open, and the reason matters: with no face page anywhere, this cannot know where the face
    would have been, and a document that really is only notes pages (a notes section uploaded on
    its own) would lose every one of them. So the layer does nothing and says so, rather than
    emptying the notes index to satisfy an invariant it cannot locate.

    Corrected to PRE (served as OTHER) rather than to FACE: the page's own evidence did not look
    like a statement — that is why the decode did not choose FACE for it — so the only thing this
    invariant licenses is refusing the notes reading, never asserting a statement.

    There is a second effect worth naming. ``seen_notes`` in the stage below drives
    ``repeat_after_notes``, which is what makes an untitled face page read as the COMPANY's
    statement rather than the Group's. A spurious notes page in the front matter set that flag
    before the first statement was even reached, so the Group's own balance sheet could be read as
    the Company's re-presentation of it. Anchoring the notes to the face fixes that too.
    """
    # THE ANCHOR MUST BE A PAGE THAT LOOKS LIKE A STATEMENT, not merely one the decode called FACE.
    #
    # Opening the path in FACE is free — `_decode` gives PRE and FACE the same opening score — and
    # it SAVES the 1.0 that `(_PRE, _NOTES)` costs, so once the front matter carries enough note
    # evidence the cheapest path becomes "FACE on page 1, NOTES from page 2". That anchors this
    # invariant at index 0 and leaves every front-matter page in the notes. Measured on a 287-page
    # CAS filing whose MD&A is itself full of numbered subsections: `other` fell from 68 pages to 6
    # and a hundred pages of narrative, governance and segment tables were handed to the note
    # extractor.
    #
    # `strong_title` is the right test because it is the evidence this function already reasons
    # about: it may refuse a notes reading but never assert a statement, so the page it anchors to
    # has to be one that asserted itself. Fail open to the previous behaviour when no feats are
    # given or no face page carries a title — a filing whose statements are untitled still has its
    # notes anchored by the decode's own first face page, which beats not anchoring at all.
    titled = None
    if feats:
        titled = next((i for i, st in enumerate(path)
                       if st == _FACE and i < len(feats)
                       and getattr(feats[i], "strong_title", False)), None)
    first_face = titled if titled is not None else next(
        (i for i, s in enumerate(path) if s == _FACE), None)
    if first_face is None:
        if _NOTES in path and log:
            log("classify:notes_before_face=kept(no_face_page_in_filing)")
        return path
    moved = [i for i in range(first_face) if path[i] == _NOTES]
    if not moved:
        return path
    out = list(path)
    for i in moved:
        out[i] = _PRE
    if log:
        log(f"classify:notes_before_face={moved}->other(first_face={first_face})")
    return out


def _untitled_face_inside_the_notes_is_notes(path: list[str], feats: list, *, log=None) -> list[str]:
    """A run of FACE pages that never names a statement, walled in by notes, is notes.

    A STATEMENT IS TITLED. Every real face run in a filing opens with one — `1、合并资产负债表`,
    `3、合并利润表`, `现金流量表补充资料` — and the untitled pages inside such a run are its
    continuation sheets, which is why `current` is carried forward for them. A run in which NOT ONE
    page carries a title has no statement to continue, and the per-page loop below says so by
    leaving `statement=None` on every page of it: a face page with no statement is a contradiction,
    because a face page IS a statement.

    WHAT IT COSTS TO GET THIS WRONG, measured on 000709. The decode put pages 172-198 in FACE — 27
    consecutive pages, no title on any of them, `classification_evidence` empty, margin 0.525 — and
    `(_NOTES, _FACE)` costs only 3.0, so a stretch of note tables that look like statement rows
    (a related-party transactions chapter is page after page of counterparty names and amounts)
    pulls the whole region across. Those pages then reach no notes walk at all:

        pages that produced notes: 99-160, 163-172, 200-208

    That hole covers the back half of chapter 十二 关联方及关联交易 — including the
    `（6）关联方应收应付款项` table the spec's Find 3 is authored to read — and the whole of chapters
    十三 to 十七. `sub__rp_find_3` selected zero rows on every filing for this reason, so the
    "highest of Find 1, Find 2, Find 3" rule was deciding from a sample of one.

    BOUNDED ON BOTH SIDES, deliberately, and that is what keeps this from reaching anything else.
    The same filing has three other untitled face runs — pages 6-8 in the front matter, page 80
    opening the balance-sheet run, page 92 inside the equity run — and every one of them either
    sits outside the notes region or lies within a run that IS titled. Requiring a NOTES page
    immediately before and immediately after selects 172-198 and nothing else. A filing whose
    genuine statements are untitled keeps them: they are not walled in by notes.
    """
    if not feats or len(feats) != len(path):
        return path

    def titled(i: int) -> bool:
        f = feats[i]
        return getattr(f, "matched_title", None) is not None or bool(getattr(f, "statement", ""))

    out = list(path)
    moved: list[int] = []
    i = 0
    while i < len(path):
        if path[i] != _FACE:
            i += 1
            continue
        j = i
        while j + 1 < len(path) and path[j + 1] == _FACE:
            j += 1
        walled = i > 0 and path[i - 1] == _NOTES and j + 1 < len(path) and path[j + 1] == _NOTES
        if walled and not any(titled(k) for k in range(i, j + 1)):
            for k in range(i, j + 1):
                out[k] = _NOTES
            moved.append((i, j))
        i = j + 1

    if moved and log:
        for a, b in moved:
            log(f"classify:untitled_face_inside_notes={a}-{b}->notes"
                f"(no statement title on any of {b - a + 1} page(s))")
    return out


def _decode(feats: list[PageFeat]) -> tuple[list[str], list[float]]:
    """Viterbi over the page sequence. Returns the state path and each page's decode MARGIN — how
    much better the chosen state was than the runner-up, which is a measured confidence rather than
    the fixed constant per branch the previous classifier served."""
    if not feats:
        return [], []
    score = {s: _emission(feats[0], s) + (0.0 if s in (_PRE, _FACE) else -2.0) for s in _STATES}
    back: list[dict[str, str]] = []
    for f in feats[1:]:
        nxt: dict[str, float] = {}
        step: dict[str, str] = {}
        for s in _STATES:
            best_prev, best_val = None, float("-inf")
            for p in _STATES:
                cost = _TRANSITION.get((p, s))
                if cost is None:
                    continue
                v = score[p] - cost
                if v > best_val:
                    best_prev, best_val = p, v
            nxt[s] = best_val + _emission(f, s)
            step[s] = best_prev or _PRE
        score, _ = nxt, back.append(step)

    last = max(_STATES, key=lambda s: score[s])
    path = [last]
    for step in reversed(back):
        path.append(step[path[-1]])
    path.reverse()

    # The margin is per page against its own alternatives, given the state actually chosen.
    margins: list[float] = []
    for f, s in zip(feats, path):
        others = [_emission(f, o) for o in _STATES if o != s]
        margins.append(_emission(f, s) - max(others))
    return path, margins


_KIND = {_PRE: PageKind.OTHER, _FACE: PageKind.FACE,
         _NOTES: PageKind.NOTES, _POST: PageKind.OTHER}


def _confidence(margin: float) -> float:
    """A decode margin mapped into 0..1. Measured, not asserted: the classifier this replaces served
    a fixed 0.72 for every face page and 0.4 for everything else, so a page it was sure about and one
    it guessed reported the same number."""
    return round(min(0.97, max(0.30, 0.5 + margin / 20.0)), 4)


def dump_review(doc: DocumentModel) -> str:
    """A per-page decision table, plus the unmapped titles as a trailing block.

    The unmapped titles are document-level and are what tell you whether the lexicon still has holes,
    so they are written once at the end rather than repeated on every row.
    """
    rows = ["idx\tkind\tstatement\tscope\tcolumns\tconf\tmatched_title"]
    for p in doc.pages:
        ev = p.evidence or {}
        rows.append("\t".join([
            str(p.index), str(getattr(p.kind, "value", p.kind)), p.statement or "—",
            p.scope or "—", ",".join(p.scope_columns) or "—",
            "" if p.classification_confidence is None else f"{p.classification_confidence:.2f}",
            str(ev.get("matched_title") or "—"),
        ]))
    if doc.unmapped_titles:
        rows.append("")
        rows.append(f"# unmapped titles ({len(doc.unmapped_titles)}) — statement-ish, resolved to "
                    f"nothing; fold the real vocabulary back into _STATEMENTS")
        rows.extend(f"# {t}" for t in doc.unmapped_titles)
    return "\n".join(rows)


class ClassifyStage:
    name = "classify"

    @staticmethod
    def _reclaim_statement_continuations(pages: list, feats: list, cache: list,
                                         ctx: PipelineContext) -> int:
        """A NOTES page between two FACE pages is a face page. Returns how many were reclaimed.

        THE STATEMENTS RUN CONTIGUOUSLY. A filing prints its statements one after another and then
        its notes; a note never appears BETWEEN two pages of the statements. So a page classified
        NOTES whose immediate neighbours are both FACE is not a note — it is a statement's
        continuation page that the classifier had nothing to recognise.

        WHY IT HAPPENS, and it is not a tuning failure. A statement running to three pages titles
        only the first: the middle page re-prints the column header band ("2025 2024 Notes HK$'000
        HK$'000") and nothing else. Measured on a 367-page filing, that is exactly page 193 of its
        three-page cash-flow statement — titled neither, bounded by two titled cash-flow pages, and
        classified NOTES. A whole page of the cash flow was therefore read as note detail rows
        instead of face rows, which loses them from the statement silently: they are not missing,
        they are somewhere else.

        THE RULE AS STATED IS NOT QUITE TRUE, AND THE EXCEPTION IS THE HK HOUSE STYLE. A filing
        prints the Group's statements, then the notes, then the COMPANY's own statement of financial
        position — so the notes section really does sit between two face pages. What saves the
        common case is that this tests the IMMEDIATE neighbours, so a notes section of two pages or
        more is never touched. But a filing whose Company statement follows a SINGLE note page has
        exactly the shape this rule looks for, and reclaiming that page would move a real note onto
        the face.

        SO THE VETO ASKS WHETHER THE PAGE OPENS AS A NOTE, which is the difference between the two:

            a real note      "29. Cash and cash equivalents"                     <- its first line
            a continuation   "191 | Annual Report … | 2025 | 2024 | Notes | …"   <- folio and band

        A note starts with its numbered heading. A statement's continuation page starts with the
        running header and the column band, and its first heading-shaped line is the section it is
        continuing ("CASH FLOWS FROM OPERATING ACTIVITIES (continued)"). Only the TOP of the page is
        read, because that is what "opens as" means — the whole-page ``note_heading`` feature fires
        on any "1. …" run anywhere, its own docstring says so, and using it vetoed the very page
        this rule exists for. The notes running header still vetoes on its own.

        The page inherits the preceding face page's statement, scope and columns, because that is
        what a continuation IS. Without the statement it would be a face page whose rows have no
        statement to be gated by, which is a different way of losing them.
        """
        if len(pages) < 3:
            return 0
        reclaimed = 0
        for pos in range(1, len(pages) - 1):
            page, prev, nxt = pages[pos], pages[pos - 1], pages[pos + 1]
            if page.kind is not PageKind.NOTES:
                continue
            if prev.kind is not PageKind.FACE or nxt.kind is not PageKind.FACE:
                continue
            # Consecutive in the DOCUMENT, not merely consecutive in this list: a filter upstream
            # could have dropped a page between them, and then they are not neighbours at all.
            if prev.index != page.index - 1 or nxt.index != page.index + 1:
                continue
            if feats[pos].notes_banner:
                ctx.log(f"classify:page={page.index}:sandwiched_note_kept"
                        " (carries the notes running header)")
                continue
            if _opens_with_note_heading(cache[pos][0]):
                ctx.log(f"classify:page={page.index}:sandwiched_note_kept"
                        " (opens with a numbered note heading)")
                continue
            page.kind = PageKind.FACE
            page.statement = page.statement or prev.statement
            page.scope = page.scope or prev.scope
            page.scope_columns = page.scope_columns or prev.scope_columns
            if isinstance(page.evidence, dict):
                page.evidence["reclaimed_between_face_pages"] = True
            reclaimed += 1
            ctx.log(f"classify:page={page.index}:reclaimed_as_face"
                    f"(between {prev.index} and {nxt.index}, statement={page.statement})")
        if reclaimed:
            ctx.log(f"classify:reclaimed_statement_continuations={reclaimed}")
        return reclaimed

    @staticmethod
    def _classify_workbook(doc: DocumentModel, data: bytes,
                           ctx: PipelineContext) -> DocumentModel:
        """Name each worksheet's statement, so a spreadsheet is scoped like a page.

        THE DEFECT THIS CLOSES. This stage used to return early for anything that is not a PDF, so
        every worksheet kept ``statement=None`` from ingest — and a statement is not decoration
        downstream, it is a BOUNDARY. ``residual._section_of_row`` guards each of its structural
        signals with ``if statement and statement_of(nxt) not in (None, statement)``, which is inert
        when the statement is None: the walk then runs past the end of the sheet it started on and a
        balance-sheet row can take its section from a cash-flow subtotal on a later sheet. The v1
        router (``residual._route_by_template``) is keyed by statement type outright, so it placed no
        Excel row at all. ``map_ontology.batch_groups`` likewise had no statement to batch by, so
        every spreadsheet row was mapped with the whole ontology in front of it.

        The classifier's own machinery decides it (``statement_of_sheet``); nothing about the
        vocabulary is duplicated for spreadsheets.

        A sheet whose title resolves is a FACE page. One whose title does not is left as ingest set
        it rather than guessed at: ``kind`` gates ``doc.face_pages()``, and calling a cover sheet or a
        list of assumptions a face would put its rows into the statement.
        """
        try:
            import openpyxl
        except ImportError:                      # pragma: no cover - openpyxl is a hard dependency
            return doc
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        except Exception as exc:  # noqa: BLE001 — a workbook we cannot reopen is not a failure here
            ctx.log(f"classify:xlsx_open_failed:{exc}")
            return doc
        try:
            names = wb.sheetnames
            for page in doc.pages:
                if page.index >= len(names):
                    continue
                name = names[page.index]
                statement, matched = statement_of_sheet(name, sheet_title_cells(wb[name]))
                page.statement = statement
                if statement:
                    page.kind = PageKind.FACE
                page.evidence = {"sheet": name, "matched_title": matched}
                ctx.log(f"classify:sheet={name}:statement={statement or 'unresolved'}")
        finally:
            wb.close()
        return doc

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        data = ctx.raw_bytes
        if not data:
            return doc
        if doc.fmt in (DocFormat.XLSX, DocFormat.XLS):
            return self._classify_workbook(doc, data, ctx)
        if doc.fmt.value != "pdf":
            return doc
        try:
            import fitz
        except ImportError:
            return doc
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
        except Exception:  # noqa: BLE001
            return doc

        pages = [p for p in doc.pages if p.index < len(pdf)]
        feats: list[PageFeat] = []
        cache: list[tuple[list[dict], float]] = []
        texts: list[str] = []
        for page_src in pages:
            lines, height = _page_lines(pdf[page_src.index])
            cache.append((lines, height))
            texts.append("\n".join(l["text"] for l in lines))
        # THE REGIME, decided once from the whole filing and kept on the context for the readers
        # after this one (`stages.extract`). See `services.regime` for what it switches on and why
        # only there.
        from app.services.regime import is_indian_filing

        indian = is_indian_filing(texts)
        ctx.indian_filing = indian
        if indian:
            ctx.log("classify:regime=indian (dated titles and enumerated rows are read)")
        for page_src, (lines, height), text in zip(pages, cache, texts):
            feats.append(_features(page_src.index, lines, height, text, dated_titles=indian))

        path, margins = _decode(feats)
        # The notes explain statements already printed, so none of them precedes the face.
        path = _notes_follow_the_face(path, log=ctx.log, feats=feats)
        # …and a face run walled in by notes that never names a statement is notes. AFTER the
        # anchoring above, which decides where the notes region begins: this one reads the region
        # the anchoring produced.
        path = _untitled_face_inside_the_notes_is_notes(path, feats, log=ctx.log)
        # …and a cash-flow supplement printed in the notes is a face only as far as its own
        # reconciliation runs. LAST, because it reads the notes region both passes above settled.
        path, supplement = _cf_supplement_extent(path, feats, cache, log=ctx.log)

        # A statement runs across several pages and only the first is titled, so a face page with no
        # resolvable title inherits the last one named. Reset when the face run ends.
        current: str | None = None
        seen_notes = False
        # The entity of the statement-notes chapter in force — 七、合并财务报表项目注释 is the
        # Group's, 十九、母公司财务报表主要项目注释 the company's. A supplement printed in the notes
        # takes the entity of the chapter it is printed in.
        notes_chapter: str | None = None
        # Which statements this filing has already presented as the GROUP's. A Company statement
        # printed past the notes is a SECOND presentation of one of them — that duplication is what
        # makes the two sets of figures collide on the same canonical keys — so it is the
        # corroboration ``_scope_of`` requires before position alone may decide an entity.
        consolidated_stmts: set[str] = set()
        # The entity the current face RUN was titled for. A Company statement of financial position
        # spans two pages in a real filing and only the first carries the title, so without this the
        # continuation page keeps the consolidated default and its figures are still added to the
        # Group's — the same half-fix as leaving the scope unread altogether.
        run_scope: str | None = None
        for pos, (page_src, f, state, margin, (lines, height)) in enumerate(zip(
                pages, feats, path, margins, cache)):
            page_src.kind = _KIND[state]
            page_src.classification_confidence = _confidence(margin)
            # Every page, not just the faces: the viewer names any page the reader scrolls to.
            page_src.printed_page = _printed_folio(lines, height)
            extent = supplement.get(pos) or {}
            closing_scope: str | None = None
            if state == _FACE:
                preceding_statement = current
                preceding_scope = run_scope
                named = _STATEMENT_ALIAS.get(f.statement or "", f.statement)
                if named:
                    current = named
                page_src.statement = current
                # Scope is resolved again here because only the decode knows whether this face page
                # RE-presents, past the notes, a statement already shown as the Group's — which is
                # what makes an untitled one the Company statement.
                scope, cols = _scope_of(
                    f.matched_title, lines, height,
                    repeat_after_notes=bool(seen_notes and current
                                            and current in consolidated_stmts
                                            and not extent.get("supplement")))
                resolved = f.scope or scope
                if extent.get("supplement") and resolved is None:
                    # NOT A RE-PRESENTATION: a note of the chapter it is printed in. A chapter
                    # heading on this page counts only when it is printed above the title.
                    here = _statement_notes_chapter(
                        lines, below_y=(f.matched_title_y or 0.0) * (height or 1.0))
                    resolved = "company" if (here or notes_chapter) == "company" else "consolidated"
                    ctx.log(f"classify:page={page_src.index}:entity_scope=supplement_in_notes"
                            f"({resolved}, chapter={here or notes_chapter or 'unread'})")
                if resolved is None and f.matched_title is None:
                    # An untitled continuation of a titled run: the entity was named once, on the
                    # page the run started. A page that DID resolve a title and still says nothing
                    # keeps its silence — it is a new statement, not a continuation.
                    resolved = run_scope
                    if resolved is not None:
                        ctx.log(f"classify:page={page_src.index}:entity_scope=carried({resolved})")
                elif f.matched_title is not None:
                    # Including None: a titled page whose own scope is unresolved ENDS the run's
                    # verdict rather than passing it on to whatever follows.
                    run_scope = resolved
                    if resolved is None:
                        # SAID WHETHER OR NOT THE NOTES HAVE BEEN SEEN. A titled statement page
                        # whose entity could not be resolved is the same fact either way, and a
                        # reader has to be able to tell a missing basis from a wrong one. This was
                        # gated on ``seen_notes``, which meant the refusal went unlogged for a
                        # filing whose statements come before any note — i.e. for the ordinary
                        # case, and for every filing now that a front-matter page can no longer
                        # latch the notes walk (``_notes_follow_the_face``). The reason names
                        # which case it is, since past-the-notes is the one that matters for
                        # deciding whether an untitled page re-presents the Group's statement.
                        why = "face_after_notes" if seen_notes else "titled_page"
                        ctx.log(f"classify:page={page_src.index}:entity_scope="
                                f"unresolved({why}:{current or '?'})")
                page_src.scope = resolved
                page_src.scope_columns = f.scope_columns or cols
                if resolved == "consolidated" and current:
                    consolidated_stmts.add(current)
                if f.closing_statement:
                    # THE PAGE ENDS IN ANOTHER STATEMENT, so that is the run the next page
                    # continues: 000709 page 88 opens with the Group's cash flow and closes with the
                    # company's, whose tail heads page 89. This page's own verdict is unchanged —
                    # its rows are split at the title by `row_reconstruct` — only what it hands on.
                    current = _STATEMENT_ALIAS.get(f.closing_statement, f.closing_statement)
                    closing_scope = _title_scope(f.closing_title)
                    run_scope = closing_scope
                    if closing_scope == "consolidated":
                        consolidated_stmts.add(current)
                    ctx.log(f"classify:page={page_src.index}:closing_title="
                            f"{f.closing_title!r}({current},{closing_scope or 'unresolved'})")
            else:
                page_src.statement = None
                current = None if state == _NOTES else current
                # Stricter than ``current``, which survives a non-notes page: an entity verdict must
                # not leak across back matter into whatever face page appears next.
                run_scope = None
            if state == _NOTES:
                seen_notes = True
            notes_chapter = _statement_notes_chapter(lines) or notes_chapter
            page_src.evidence = {"state": state, "matched_title": f.matched_title,
                                 "matched_title_y": f.matched_title_y,
                                 # WHETHER A STATEMENT IS STILL RUNNING ABOVE THE TITLE, decided
                                 # by what is printed above it rather than by how far down the page
                                 # it sits. The test used to be `matched_title_y > 0.20`, and a
                                 # mainland filing puts the title just above that line: 688008
                                 # page 154 prints the company balance sheet's grand total and the
                                 # signatures, then 合并利润表 at y=0.178. No prior statement was
                                 # recorded, `pdf_extract` did not split the page, and the whole
                                 # consolidated income statement was read as one batch with the
                                 # balance sheet's closing row — whose caption 股东权益）总计
                                 # scopes as an EQUITY banner and then scoped every income-statement
                                 # row beneath it, so the section gate refused every P&L concept on
                                 # the page. What the spread published for 销售费用 / 管理费用 /
                                 # 研发费用 was the PARENT COMPANY's figures off the next page,
                                 # because those rows carried no leaked banner.
                                 "statement_before_title": (
                                     preceding_statement if state == _FACE
                                     and f.amounts_above_title else None),
                                 "scope_before_title": (
                                     preceding_scope if state == _FACE
                                     and f.amounts_above_title else None),
                                 "title_ambig": f.title_ambig, "margin": round(margin, 2),
                                 "oci_combined": f.oci_combined}
            if state == _FACE and f.closing_statement:
                page_src.evidence.update({"closing_title": f.closing_title,
                                          "closing_title_y": f.closing_title_y,
                                          "scope_after_closing_title": closing_scope})
            if state == _FACE and extent:
                # WHERE ON THE PAGE THE FACE IS, for a supplement printed in the notes: above its
                # title is another note's tail, which `pdf_extract` hands to the notes reader, and
                # below its reconciliation's closing row is not read as a face.
                page_src.evidence.update({k: v for k, v in extent.items() if v is not None})
            if f.unmapped:
                doc.unmapped_titles.extend(f.unmapped[:3])

        self._reclaim_statement_continuations(pages, feats, cache, ctx)
        # A PERSON'S CORRECTION OUTRANKS THE READING ABOVE, and is applied last so nothing above
        # can undo it. See `services.page_overrides`.
        if ctx.page_overrides:
            from app.services.page_overrides import apply as apply_overrides

            apply_overrides(pages, ctx.page_overrides, log=ctx.log)

        pdf.close()
        doc.unmapped_titles = sorted(set(doc.unmapped_titles))[:60]
        stmts = {p.statement for p in doc.face_pages() if p.statement}
        ambig = sum(1 for p in doc.pages if (p.evidence or {}).get("title_ambig"))
        ctx.log(f"classify:face={len(doc.face_pages())} notes={len(doc.notes_pages())} "
                f"statements={sorted(stmts)}")
        ctx.log(f"classify:unmapped_titles={len(doc.unmapped_titles)} title_ambig={ambig}")
        return doc
