"""THE PRINTED STATEMENT, AS CONTEXT FOR A LINE READ OFF IT — the face's answer to `note_context`.

NEW FILE -> backend/app/services/face_context.py

WHY IT EXISTS. `services.note_context.identified_notes` supplies the notes a line's configuration
selected, and a request carrying it can be answered. A line read off the FACE selects no note, so
`note_sets` produces no entry for it and the request it gets carries an EMPTY note block — and the
reply contract's own advice to a model in that position is to answer with an empty `sources`.
Measured on the shipped set, 343 of the 506 asked-about lines are in exactly that position. There
was nothing in the request to locate the figure in, so this is the block that gives a face line
something to answer from.

KEYED BY STATEMENT, NOT BY SECTION, AND THAT IS MEASURED RATHER THAN CONVENIENT. Grouping the rows
by their printed banner was the obvious shape and it is the wrong one. Run over 000709's 255 face
rows:

  * THE STATEMENT RESOLVES FOR ALL OF THEM — 0 rows with no statement — because it comes off the
    page the row was read from, which the classifier settled.
  * THE BANNER RESOLVES FOR 163, so section-keying would silently DROP 92 rows, a third of the
    statement, from the context of the very lines that need it.
  * AND WHERE IT DOES RESOLVE IT IS SOMETIMES WRONG: 12 changes-in-equity rows came back labelled
    `cash_flow_from_financing_activities`, 10 profit-and-loss rows as `equity`, 8 more as
    `non_current_liabilities`. `services.note_sections`' docstring already records why — on a page
    full of headings the nearest preceding banner is whatever the typesetter printed last — and a
    section block assembled on that signal would hand a balance-sheet line a block of mislabelled
    income-statement rows.

So the rows are supplied AS THE FILING PRINTS THEM, in page and print order, for the statement the
line is gated to. The banner travels as a LABEL on each row (`under`) rather than as a filter: it
is what the page said, the model may weigh it, and a wrong one costs a hint rather than a row.

EVERY ROW, NOT ONLY THE UNCLAIMED ONES. A row already matched to a line says so (`line`), and that
is the point rather than redundancy: it is the deterministic route's proposal, which is what makes
a request answerable as "confirm or correct this" instead of "find this from nothing". It is also
the context that makes a caption legible at all — that "Buildings" sits between "Land" and "Plant
and machinery" is most of what identifies it.

ONCE PER REQUEST, NOT ONCE PER LINE, for the reason `identified_notes` gives at length: a block
attached to each of a request's line items is the same block multiplied by the request's size, and
at 88 lines on one statement that fails the provider outright rather than merely costing more.
"""
from __future__ import annotations

import re

from app.services import units
from app.services.gap_explanation import _BOILERPLATE
from app.services.mapping import normalize_statement
from app.services.statement_sets import PREFERRED_FRAMEWORK as _PREFERRED


def _figures(row) -> dict[str, str]:
    """The row's figures by period label.

    `column_index` values are skipped, the same rule `note_context` applies: those are the columns
    of a matrix page (a changes-in-equity grid), where the columns are equity components rather
    than periods, so their labels are not periods and would read as invented ones.
    """
    out: dict[str, str] = {}
    for ev in (getattr(row, "values", None) or {}).values():
        if getattr(ev, "column_index", None) is not None:
            continue
        if getattr(ev, "value", None) is None:
            continue
        out[str(getattr(ev, "period_label", "") or "?")] = printed_figure(ev)
    return out


def printed_figure(ev) -> str:
    """The figure AS THE PAGE PRINTS IT: `value_raw`, not the sign-normalised `value`.

    A statement block is the statement as printed, and the engine's sign convention is not what
    was printed. 688008's consolidated income statement prints 财务费用 as -240,504,105.54 (net
    interest income) and 其中：利息费用 as 1,512,598.38; the block sent them as 240504105.54 and
    -1512598.38, the opposite of the page — so a model reading the block saw an expense line the
    filing reports as income, and a figure it could not find on the page it was told to read.
    `value` stays the fallback for a fact whose raw figure was never kept.
    """
    raw = getattr(ev, "value_raw", None)
    return str(raw if raw is not None else ev.value)


def _statement_of(row, stmt_of_page: dict, page) -> str:
    """The statement a face row was PRINTED on: the stamp `pdf_extract` puts on a row read from
    one side of a page that holds two statements (`buckets.PRINTED_ON_FLAG`), else its page's.

    A page holds two statements where one ends part way down and the next begins, and where a
    2-up spread prints a balance sheet beside a statement of profit and loss — Asian Paints' PDF
    page 184. Looked up by page alone, the balance sheet's rows were sent in the profit-and-loss
    block and the balance-sheet requests had no standalone block at all."""
    from app.services.buckets import PRINTED_ON_FLAG

    for flag in (getattr(getattr(row, "confidence", None), "flags", None) or ()):
        if str(flag).startswith(f"{PRINTED_ON_FLAG}:"):
            return normalize_statement(str(flag).split(":", 1)[1])
    return stmt_of_page.get(page if page is not None else -1, "")


def _page_of(row) -> int | None:
    """The page this row was read from, off its first provenanced figure.

    `LineItem.statement_id` is the field this ought to read and it is written by nothing — grep
    finds no producer and no other consumer — so the page's own classification is the only
    statement a face row actually has.
    """
    for ev in (getattr(row, "values", None) or {}).values():
        prov = getattr(ev, "provenance", None)
        page = getattr(prov, "page_index", None) if prov is not None else None
        if page is not None:
            return int(page)
    return None


def face_rows(doc, statements) -> list[dict]:
    """The printed rows of each named statement, in page and print order. One entry per statement.

    `statements` is the set of statement tokens a request's lines are gated to — `RequestPlan.
    sections` carries the `(statement, section)` pairs and only the statement half selects here,
    for the reason in the module docstring.

    A row with no figure at all is omitted: it is a heading or a spacer, and a caption with nothing
    beside it cannot be the answer to "where is this line's figure printed".
    """
    # BOTH SIDES THROUGH THE FOLD, because one statement has two spellings and this join is where
    # that bites. `StatementType` — what a line DECLARES — spells it `equity_changes`; the page
    # classifier and every downstream caller say `changes_in_equity`. So a line gated to the
    # statement of changes in equity asked for `equity_changes`, no page carried that, and the
    # block came back EMPTY: the model was shown nothing for the one statement whose rows it most
    # needs supplied, and the failure looked like "this filing has no such page".
    #
    # `mapping.normalize_statement` is the designated owner of the fold — see `_STATEMENT_SPELLINGS`
    # there, whose own comment says a declaration the gate cannot compare to the classifier's
    # verdict scopes nothing. The other three statements spell the same either way, so this is a
    # no-op for them and the equity statement is the whole of its effect.
    want = {normalize_statement(str(s)) for s in (statements or ()) if str(s)}
    want.discard("")
    if not want:
        return []
    doc_unit = getattr(doc, "unit_context", None)
    by_block, legends = _face_blocks(doc, want)
    context = _blocks_context(doc, by_block)
    by_block_entries: dict[tuple[str, str, str, str], list[tuple[int, dict]]] = {}
    for block_key, members in by_block.items():
        for page, row, figures, unit in members:
            entry: dict = {"caption": shown_caption(row), "figures": figures, "_unit": unit}
            under, sub = context.get((block_key, id(row)), ("", ""))
            if under:
                entry["under"] = under
            if sub:
                entry["subheading"] = sub
            # THE DETERMINISTIC ROUTE'S PROPOSAL. Omitted for a row the mapper could not place —
            # `stages.face_mapping_contract` keys those `engine_unclassified_face__…`, which names
            # no line and would read as one.
            key = str(getattr(row, "canonical_key", "") or "")
            if key and not key.startswith("engine_unclassified"):
                entry["line"] = key
            by_block_entries.setdefault(block_key, []).append((page, entry))
    by_block = by_block_entries

    # THE SET THIS TEMPLATE READS, when the filing prints two. The HK template is built on
    # HKFRS / IFRS, and an H-share filer's IFRS set is the one its lines are answered from: its
    # blocks come first and keep the reader's proposals. The other set (CAS) is KEPT APART — its
    # own block after them, named by its standards, with no row proposed for a line, and saying
    # it is there only for a figure the IFRS statements do not print on their face (PetroChina's
    # CAS balance sheet prints short-term borrowings without the current portion of long-term
    # borrowings, which its IFRS face includes and its IFRS note 29 splits out).
    frameworks = {k[3] for k in by_block if k[3]}
    read = (PREFERRED_FRAMEWORK if PREFERRED_FRAMEWORK in frameworks
            else sorted(frameworks)[0]) if len(frameworks) > 1 else None
    out: list[dict] = []
    for key in sorted(by_block, key=lambda k: (k[0], bool(read and k[3] and k[3] != read),
                                               _ENTITY_ORDER.get(k[1], 9), k[2], k[3])):
        statement, entity, part, framework = key
        rows = [e for _p, e in sorted(by_block[key], key=lambda pe: pe[0])]
        apart = bool(read and framework and framework != read)
        if apart:
            rows = [{k: v for k, v in e.items() if k != "line"} for e in rows]
        block: dict = {"statement": statement, "entity": entity}
        if framework and read:
            block["set"] = framework
        if apart:
            pages = sorted({p for p, _e in by_block[key]})
            block["kept_apart"] = (f"the {framework} statements (pages {pages[0] + 1}-"
                                   f"{pages[-1] + 1}), not the {read} set this template reads: "
                                   f"answer from the {read} block, and cite this block only for "
                                   f"a figure the {read} statements do not print")
        if part:
            block["part"] = "supplement"
            block["title"] = part
            block["printed_in"] = "notes"
        if legends.get(key):
            # The printed heading of each figure key — the period's date, as the page states it.
            block["columns"] = legends[key]
        # THE UNIT THE FIGURES ARE IN, as the run knows it — see `units.describe`.
        block["unit"] = units.block_units(rows, doc_unit)
        block["rows"] = rows
        out.append(block)
    return out


def _face_blocks(doc, want=None) -> tuple[dict, dict]:
    """The face rows of each block — ``{(statement, entity, part, set): [(page, row, figures,
    unit)]}`` in document order — and each block's column legend. `want` limits the statements;
    None is every one. The one assembly `face_rows` sends and `face_index` resolves against."""
    stmt_of_page = {int(getattr(p, "index", -1)):
                    normalize_statement(str(getattr(p, "statement", "") or ""))
                    for p in (getattr(doc, "pages", None) or ())}
    notes_pages = _notes_pages(doc)
    supplement = supplement_pages(doc)
    framework_of = {int(getattr(p, "index", -1)): str((getattr(p, "evidence", None) or {})
                                                       .get("framework") or "")
                    for p in (getattr(doc, "pages", None) or ())}
    doc_unit = getattr(doc, "unit_context", None)
    # ONE BLOCK PER STATEMENT AND ENTITY. A mainland filing prints the group's statements and the
    # parent company's own, and the two blocks were one: 000709's 货币资金 arrived twice — 31.8bn
    # and 24.5bn — with nothing to say which was the group's. And an HKEX table printing Group and
    # Company side by side was ONE row whose two `current` figures shared a key, so the company's
    # overwrote the group's. Each figure now goes under its own entity.
    by_block: dict[tuple[str, str, str, str], list[tuple[int, object, dict, object]]] = {}
    legends: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in (getattr(doc, "line_items", None) or ()):
        page = _page_of(row)
        statement = _statement_of(row, stmt_of_page, page)
        if want is not None and statement not in want:
            continue
        if not _shown_on_face(row, page, notes_pages):
            continue
        unit = units.row_unit(row, doc_unit)
        for entity, (figures, legend) in _figures_by_entity(row).items():
            # THE CASH-FLOW SUPPLEMENT IS ITS OWN BLOCK. A mainland filing prints the indirect
            # reconciliation (现金流量表补充资料) in the notes, and the reader reads it as the cash
            # flow's — rightly, it is where the indirect lines' figures are. But sent inside the
            # cash-flow block it read as the statement's own continuation: 净利润, 固定资产折旧,
            # 财务费用 and 经营性应收项目的减少 appended to the face after 期末现金及现金等价物余额.
            part = supplement.get(page, "") if statement == "cash_flow" else ""
            # ONE BLOCK PER STATEMENT SET, too: see `stages.classify.statement_frameworks`.
            block_key = (statement, entity, part, framework_of.get(page, "") if page is not None
                         else "")
            by_block.setdefault(block_key, []).append(
                (page if page is not None else 0, row, figures, unit))
            seen = legends.setdefault(block_key, {})
            for label, heading in legend.items():
                seen.setdefault(label, heading)
    return by_block, legends


def _blocks_context(doc, by_block) -> dict[tuple[tuple, int], tuple[str, str]]:
    """``{(block key, id(row)): (under, subheading)}`` — what each row of each block is shown
    printed under (`block_context`), with the total an owners' / non-controlling row splits as its
    sub-heading (`attribution_headings`, `named_total_headings`)."""
    chrome = set(getattr(doc, "page_chrome", None) or ())
    titles = {_fold_title(str((getattr(p, "evidence", None) or {}).get("matched_title") or ""))
              for p in (getattr(doc, "pages", None) or ())}
    titles.discard("")
    printed = [(st, _page_of(r), r) for st, _c, r in _face_index_rows(doc)]
    headings = attribution_headings(printed)
    named = named_total_headings(printed)
    out: dict[tuple[tuple, int], tuple[str, str]] = {}
    for key, members in by_block.items():
        ordered = [row for _p, row, _f, _u in sorted(members, key=lambda m: m[0])]
        for row, (under, sub) in zip(ordered, block_context(ordered, key[0], chrome, titles)):
            # An owners' / non-controlling share names the total it splits, so the two pairs an
            # income statement prints are told apart and a citation can name one by its `group`.
            if id(row) in headings:
                sub = headings[id(row)]
            elif id(row) in named and not sub:
                sub = named[id(row)]
            out[(key, id(row))] = (under, sub)
    return out


# ── WHAT A STATEMENT ROW IS SHOWN PRINTED UNDER ──────────────────────────────────────────────
#
# `section_hint` is the banner in force when the row was read, and it stays in force until the
# next banner — the reader's right reading for MAPPING, which needs a section for every row, and
# the wrong one to SHOW: a balance sheet's grand total was sent "under" the last section printed
# before it (000883's 资产总计 under 非流动资产：, 负债合计 under 非流动负债：; Laurus's "Total assets"
# under "Current assets"), the rows printed after a section's own total were still "in" it ("NET
# CURRENT ASSETS" under CURRENT LIABILITIES, PetroChina's profit before tax under FINANCE COSTS),
# and a statement's title or a units line was given as a section (Hon Kwok's every row "under"
# CONSOLIDATED STATEMENT OF PROFIT OR LOSS; PetroChina's REVENUE under "RMB RMB"). What a row is
# SHOWN under is the section it is printed in: a section ends at its own total, a statement's
# grand and bottom-line totals are in no section, and a title or a unit is not a section. Request
# text only — the reader's `section_hint`, which the mapper reads, is unchanged.

# A statement's own title, as the reader may have taken it for a banner.
_STATEMENT_TITLE = re.compile(
    r"(?i)^\W*(?:(?:consolidated|company|standalone|parent)(?:\s+and\s+(?:consolidated|company|"
    r"standalone|parent))?\s+)?(?:statements?\s+of\b|balance\s+sheets?\b|income\s+statements?\b"
    r"|cash\s+flow\s+statements?\b)"
    r"|^\W*(?:合并|合併|母公司|公司)?(?:资产负债表|資產負債表|利润表|利潤表|损益表|損益表|现金流量表|現金流量表"
    r"|所有者权益变动表|股东权益变动表|综合收益表|綜合收益表|全面收益表|財務狀況表|财务状况表)\W*$")
# A units line: currencies and scales and nothing else ("RMB RMB", "HK$'000", 人民币元).
_UNITS_ONLY = re.compile(
    r"(?i)^(?:[\s\W]|rmb|hk\$|hkd|us\$|usd|cny|inr|₹|'?000|’000|thousands?|millions?|crores?"
    r"|lakhs?|人民币|人民幣|港元|港幣|港币|千元|万元|萬元|元)+$")
# A BALANCE SHEET'S TOTALS THAT SPAN ITS SECTIONS — the grand totals, and the net lines struck
# across two sections.
_GRAND_TOTAL = re.compile(
    r"(?i)^\W*(?:total\s+assets(?:\s+less\s+current\s+liabilities)?"
    r"|total\s+liabilities(?:\s+and\s+(?:(?:share|stock)holders['’]?\s+)?(?:equity|net\s+worth))?"
    r"|total\s*[-–—]?\s*(?:equity\s+and\s+liabilities|liabilities\s+and\s+equity)"
    r"|net\s+current\s+\(?(?:assets|liabilities)\)?(?:\s*/\s*\(?(?:assets|liabilities)\)?)?"
    r"|net\s+\(?(?:assets|liabilities)\)?(?:\s*/\s*\(?(?:assets|liabilities)\)?)?)"
    r"(?![\w\s]*\b(?:held|classified|directly|attributable)\b)(?=\W*(?:$|[^\x00-\x7f]))"
    r"|^\W*(?:资产总计|资产合计|资产总额|資產總值|資產總額|资产总值|负债合计|负债总计|负债总额|負債總額|負債總值"
    r"|负债和所有者权益(?:（或股东权益）)?总计|负债和股东权益(?:（或所有者权益）)?总计|负债及所有者权益总计"
    r"|負債及權益總額|权益及负债总额|流动资产净值|流動資產淨值|流动负债净额|流動負債淨額|资产净值|資產淨值"
    r"|总资产减流动负债|總資產減流動負債)\W*$")
# AN INCOME STATEMENT'S BOTTOM LINES, and a cash flow's, which close the section printed above
# them. Each statement's own: a cash flow's operating section OPENS with profit before tax.
_STATEMENT_TOTAL = {
    "profit_and_loss": re.compile(
        r"(?i)\b(?:profit|loss|earnings)(?:\s*/\s*\(?(?:loss|profit)\)?)?\s+(?:before|for\s+the\s+"
        r"(?:year|period))\b|\btotal\s+comprehensive\b|^\W*other\s+comprehensive\s+(?:income|"
        r"expense|loss)\S*(?:\s+\S+)?\s+for\s+the\s+(?:year|period)|^\W*gross\s+profit"
        r"|^\W*operating\s+(?:profit|loss)|^\W*(?:profit|loss)\s+from\s+operati"),
    "cash_flow": re.compile(
        r"(?i)^\W*net\s+(?:\(?(?:increase|decrease)\)?\s*/?\s*)+in\s+cash"
        r"|^\W*cash\s+and\s+cash\s+equivalents\s+at\s+(?:the\s+)?(?:end|beginning)"
        r"|^\W*effects?\s+of\s+(?:foreign\s+)?exchange\s+rate"
        r"|现金及现金等价物净增加额|現金及現金等價物淨增加額|期末现金及现金等价物余额|期初现金及现金等价物余额"
        r"|汇率变动对现金及现金等价物的影响"),
}
# A total's own words: "Total …", "Net …", "… 合计".
_TOTAL_WORDS = re.compile(r"(?i)^\W*(?:total|net)\b|(?:合计|合計|总计|總計|小计|小計|总额|總額|净额|淨額)\W*$"
                          r"|\bfor\s+the\s+(?:year|period)\b|\bnet\s+of\s+tax\b")
_STOP = frozenset({"of", "the", "and", "from", "in", "to", "used", "for", "a", "an", "by", "on"})


def _fold_title(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def _words_of(text: str) -> tuple[frozenset[str], str]:
    """A caption's Latin words less the connectives, and its Han text without numbering or
    punctuation — the two halves a section and its total are compared on."""
    t = (text or "").casefold()
    latin = frozenset(w for w in re.findall(r"[a-z]+", t) if w not in _STOP)
    han = re.sub(r"^[一二三四五六七八九十]+、|[^㐀-鿿]", "", text or "")
    return latin, han


def _totals_section(caption: str, section: str) -> bool:
    """Whether `caption` is the total of the section `section` names: "Total current assets" of
    CURRENT ASSETS, 非流动资产合计 of 非流动资产：, "Net cash flows used in investing activities" of
    CASH FLOWS FROM INVESTING ACTIVITIES, "OTHER COMPREHENSIVE INCOME FOR THE YEAR" of OTHER
    COMPREHENSIVE INCOME."""
    if not caption or not section or not _TOTAL_WORDS.search(caption):
        return False
    c_latin, c_han = _words_of(caption)
    s_latin, s_han = _words_of(section)
    if s_latin and s_latin <= c_latin and c_latin != s_latin:
        return True
    return bool(s_han) and not s_latin and s_han in c_han and c_han != s_han


def is_statement_title(text: str, titles=()) -> bool:
    """Whether `text` is a statement's title — one the classifier matched on a page, or one by its
    shape ("CONSOLIDATED STATEMENT OF PROFIT OR LOSS", 合并资产负债表)."""
    return bool(_STATEMENT_TITLE.search(text or "")) or (
        bool(titles) and _fold_title(text) in titles)


def block_context(rows, statement: str = "", chrome=(), titles=()) -> list[tuple[str, str]]:
    """``(under, subheading)`` for each row of one statement block, in the order given (printed
    order) — see the comment above. `subheading` is the reader's `group_hint`, which a grand total
    does not carry."""
    closed: set[str] = set()
    out: list[tuple[str, str]] = []
    for row in rows:
        caption = str(getattr(row, "source_label", "") or "")
        section = str(getattr(row, "section_hint", None) or "").strip()
        under = section
        if under and (is_identity_line(under, chrome) or is_statement_title(under, titles)
                      or _UNITS_ONLY.match(under)):
            under = ""
        if under in closed:
            under = ""
        sub = str(getattr(row, "group_hint", "") or "").strip()
        grand = statement == "balance_sheet" and bool(_GRAND_TOTAL.search(caption))
        bottom = statement in _STATEMENT_TOTAL and bool(_STATEMENT_TOTAL[statement].search(caption))
        if section and section not in closed and not getattr(row, "caption_borrowed", False):
            if _totals_section(caption, section):
                # The section's own total: printed in it, and the last row that is.
                closed.add(section)
            elif grand or bottom:
                closed.add(section)
                under = ""
        if grand:
            under, sub = "", ""
        heading = _joined_heading(caption)[0]
        if heading and not sub:
            sub = heading
        out.append((under, sub))
    return out


#: Said of a row whose caption the reader took from the heading above it (`caption_borrowed`).
BORROWED_CAPTION = "{heading} (total, printed without a caption)"


# A SCHEDULE III HEADING JOINED TO THE CAPTION BENEATH IT: "Financial Assets Investments",
# "Financial liabilities Borrowings". The face reader splits the heading off itself now
# (`row_reconstruct._FACE_GROUP_HEADING`), so a row reaches here with its own caption and the
# heading as its `group_hint`. This stays for a row the reader still joins — a heading clustered
# with a caption that does not open one of its own — so the request shows it by its own caption,
# with the heading as its `subheading`.
_JOINED_HEADING = re.compile(
    r"^\W*((?i:financial\s+(?:assets|liabilities)))\s+(?=[A-Z])"
    # …not a caption that merely begins with the words, in a title-case filing: "Financial Assets
    # At Fair Value Through Profit Or Loss", "Financial Liabilities Measured At Amortised Cost".
    r"(?!(?i:at|of|for|in|on|to|with|under|through|measured|designated|held|carried|other|and)\b)")


def _joined_heading(caption: str) -> tuple[str, str]:
    """``(heading, caption)`` for a caption opening with a joined Schedule III heading, else
    ``("", caption)``."""
    m = _JOINED_HEADING.match(caption or "")
    if not m or not caption[m.end():].strip():
        return "", caption
    return m.group(1), caption[m.end():]


def shown_caption(row) -> str:
    """The caption a face row is SHOWN and cited by: its printed caption, or — for a bare total the
    filing printed with no caption of its own, which the reader captioned with the heading over
    the rows it totals ("CURRENT ASSETS", "EQUITY", "Attributable to:") — that heading, saying it
    is not printed there. Sent as the heading alone it read as the heading, given figures: Lai
    Sun's "CURRENT ASSETS" 13,991,585 beside its "Total current assets", its total equity
    captioned "EQUITY"."""
    caption = str(getattr(row, "source_label", "") or "")
    if not getattr(row, "caption_borrowed", False) or not caption.strip():
        return _joined_heading(caption)[1]
    return BORROWED_CAPTION.format(heading=caption.strip().rstrip(":：").strip())


def named_total_headings(rows) -> dict[int, str]:
    """``{id(row): total}`` for an owners' / non-controlling row whose caption names which total
    it is a share of — CAS 1.归属于母公司股东的净利润 and 2.少数股东损益 (of 五、净利润), 归属于母公司所有者的
    综合收益总额 (of 七、综合收益总额) — giving the row printed above them that it splits. The caption
    says which KIND of total; the request did not say which row, and the line's definition speaks
    of the share "printed under the profit for the year". A share of other comprehensive income
    (归属母公司所有者的其他综合收益的税后净额) is a part of that line, not a split of a total, and gets none.

    Separate from `attribution_headings` on purpose: that one also scopes the MAPPING of the rows it
    names (`stages.map_ontology`), and these captions map by their own words."""
    by_statement: dict[str, list[tuple[int, int, object]]] = {}
    for at, (statement, page, row) in enumerate(rows or ()):
        if statement == "profit_and_loss" and (getattr(row, "source_label", "") or ""):
            by_statement.setdefault(statement, []).append(
                (page if page is not None else 0, at, row))
    out: dict[int, str] = {}
    for seq in by_statement.values():
        ordered = [r for _p, _a, r in sorted(seq, key=lambda t: (t[0], t[1]))]
        for i, row in enumerate(ordered):
            caption = getattr(row, "source_label", "") or ""
            if not _ATTRIBUTION_ROW.search(caption) or is_attribution_row(caption):
                continue
            if re.search(r"(?i)other\s+comprehensive|其他综合收益|其他綜合收益|其他全面收益|per\s+share"
                         r"|每股", caption):
                continue
            kind = _total_kind(caption) or (
                "profit" if re.search(r"(?i)\bprofit|\bloss|损益|損益|利润|利潤|溢利|虧損|亏损", caption)
                else "")
            if not kind:
                continue
            for j in range(i - 1, -1, -1):
                prior = getattr(ordered[j], "source_label", "") or ""
                if _ATTRIBUTION_ROW.search(prior):
                    continue
                if _total_kind(prior) == kind:
                    out[id(row)] = prior.strip()
                    break
    return out


# THE TOTAL AN ATTRIBUTION ROW SPLITS. An HKFRS / IFRS income statement prints "Owners of the
# Company" and "Non-controlling interests" twice: once under the profit for the year and once under
# the total comprehensive income, and the second pair is a different figure. Lai Sun prints both
# pairs under a bare "Attributable to:", BYD's second pair arrived under the OCI sub-heading carried
# on from above it, and nothing in the request told the two pairs apart — a citation of "Owners of
# the Company" matched two rows (-1,675,589 and -1,562,216). A CAS statement names the total in the
# caption itself (归属于母公司股东的净利润 / 归属于母公司股东的综合收益总额) and needs nothing.
_ATTRIBUTION_ROW = re.compile(
    r"(?i)owners?\s+of\s+the\s+(?:company|parent)|equity\s+holders|shareholders\s+of\s+the\s+"
    r"(?:company|parent)|non[-\s]?controlling\s+interests?|minority\s+interests?"
    # 远东宏信 splits its profit three ways: owners, perpetual securities' holders, NCI.
    r"|holders\s+of\s+perpetual\s+(?:capital\s+)?securities"
    r"|擁有人應佔|拥有人应占|非控股權益|非控股权益|非控制性權益|少數股東|少数股东|母公司股东|母公司股東"
    r"|母公司所有者|母公司擁有人|母公司拥有人")
_COMPREHENSIVE_TOTAL = re.compile(
    r"(?i)total\s+comprehensive\s+(?:income|expense|loss)|綜合收益總額|综合收益总额|全面收益總額"
    r"|全面收益总额|全面收入總額|綜合收入總額")
_PROFIT_TOTAL = re.compile(
    r"(?i)\b(?:profit|loss|earnings)(?:\s*/\s*\(?(?:loss|profit)\)?)?\s+for\s+the\s+(?:year|period)"
    r"|^\W*net\s+(?:profit|loss)\b|年度溢利|年內溢利|期內溢利|年度虧損|年內虧損|本年度溢利"
    r"|净利润|淨利潤|净亏损|淨虧損")
# A caption that already names which total it is a share of — and earnings per share.
_NAMES_ITS_TOTAL = re.compile(
    r"(?i)profit|loss|comprehensive|earnings|per\s+share|损益|損益|利润|利潤|溢利|虧損|亏损"
    r"|综合收益|綜合收益|全面收益|每股")
_ATTRIBUTABLE = re.compile(r"(?i)attributable|ownership|應佔|应占|归属|歸屬|所有权|所有權")


def _total_kind(caption: str) -> str:
    """"comprehensive" / "profit" for a row that is one of the two totals an attribution splits."""
    text = caption or ""
    if _COMPREHENSIVE_TOTAL.search(text):
        return "comprehensive"
    # Not the profit before tax, and not the continuing / discontinued split printed under the
    # net profit (CAS 按经营持续性分类), which is a part of the total rather than the total.
    if _PROFIT_TOTAL.search(text) and not re.search(
            r"(?i)before\s+(?:income\s+)?tax|除稅前|除税前|continu|持续经营|持續經營|终止经营|終止經營",
            text):
        return "profit"
    return ""


def is_attribution_row(caption: str) -> bool:
    """Whether a face row is a share of a total — owners / non-controlling interests — whose
    caption does not itself say which total it is a share of."""
    text = caption or ""
    return bool(_ATTRIBUTION_ROW.search(text)) and not _NAMES_ITS_TOTAL.search(text)


def attribution_headings(rows) -> dict[int, str]:
    """``{id(row): heading}`` for every attribution row of a statement, naming the total it splits.

    `rows` is ``(statement, page, row)`` in document order. ONE DEFINITION FOR BOTH SIDES OF THE
    CITATION: `face_rows` sends the heading as the row's `subheading` and
    `note_sourced.resolve_sources` reads a citation's `group` against it, so the row a model names
    by its heading is the row the answer resolves to.

    The heading is the banner the row was printed under (`section_hint`) where that banner names
    the total — PetroChina's "PROFIT FOR THE YEAR ATTRIBUTABLE TO:" — and no total of the other
    kind has been printed since the banner's first row: a banner carried past the total
    comprehensive income onto the second pair would name the wrong total. Otherwise it is the
    nearest total printed before the row, in printed order, with the banner after it: "LOSS FOR
    THE YEAR — Attributable to:".
    """
    by_statement: dict[str, list[tuple[int, int, object]]] = {}
    for at, (statement, page, row) in enumerate(rows or ()):
        # The income statement only: a cash flow's "Dividends paid to non-controlling interests"
        # and an equity statement's NCI column are not shares of a total.
        if statement == "profit_and_loss" and (getattr(row, "source_label", "") or ""):
            by_statement.setdefault(statement, []).append(
                (page if page is not None else 0, at, row))
    out: dict[int, str] = {}
    for seq in by_statement.values():
        ordered = [r for _p, _a, r in sorted(seq, key=lambda t: (t[0], t[1]))]
        first_under: dict[str, int] = {}
        for i, row in enumerate(ordered):
            under = (getattr(row, "section_hint", None) or "").strip()
            if under:
                first_under.setdefault(under, i)
            if not is_attribution_row(getattr(row, "source_label", "") or ""):
                continue
            last_total, last_kind = None, ""
            for j in range(i - 1, -1, -1):
                caption = getattr(ordered[j], "source_label", "") or ""
                if is_attribution_row(caption):
                    continue
                kind = _total_kind(caption)
                if kind:
                    last_total, last_kind = j, kind
                    break
            under_kind = _total_kind(under) if under else ""
            if under_kind:
                since = first_under.get(under, i)
                stale = (last_total is not None and last_total > since
                         and last_kind != under_kind)
                if not stale:
                    out[id(row)] = under
                    continue
            if last_total is None:
                continue
            total = (getattr(ordered[last_total], "source_label", "") or "").strip()
            group = (getattr(row, "group_hint", "") or "").strip()
            tail = next((t for t in (under, group) if t and _ATTRIBUTABLE.search(t)),
                        "attributable to:")
            out[id(row)] = f"{total} — {tail}"
    return out


_ENTITY_ORDER = {"consolidated": 0, "standalone": 1}
# The standards the HK template is built on — see `face_rows` and `services.statement_sets`.
PREFERRED_FRAMEWORK = _PREFERRED


# The supplement's own title. The classifier marks the supplement only where it also finds the
# reconciliation's closing row; 000685 titles it "79、现金流量表补充资料" and the mark was not set, so
# its standalone cash-flow block ran on into 现金的期末余额 and the foreign-currency rows after it.
_SUPPLEMENT_TITLE = re.compile(r"现金流量表补充资料|現金流量表補充資料")


def supplement_pages(doc) -> dict[int, str]:
    """``{page index: printed title}`` for every page of a cash-flow supplement printed in the notes.

    The classifier marks the page that TITLES the supplement (`evidence["supplement"]`, see
    `stages.classify._cf_supplement_extent`); the pages after it that it continues onto are the
    face pages of the same statement up to the next titled page.
    """
    from app.core.models.enums import PageKind

    out: dict[int, str] = {}
    title = ""
    last = None
    for p in sorted(getattr(doc, "pages", None) or (), key=lambda p: int(getattr(p, "index", -1))):
        ev = getattr(p, "evidence", None) or {}
        index = int(getattr(p, "index", -1))
        face = getattr(p, "kind", None) is PageKind.FACE
        printed = str(ev.get("matched_title") or "")
        if face and (ev.get("supplement") or _SUPPLEMENT_TITLE.search(printed)):
            title = printed or "现金流量表补充资料"
        elif not face or ev.get("matched_title") or last is None or index != last + 1:
            title = ""
        if title:
            out[index] = title
        last = index
    return out

# A COMPANY IDENTITY LINE — the CIN, the registered office, the name banner, "Annual Report
# 2024-25". It is printed at the top of every statement page, so the nearest-banner rule made it
# the `under` of every row above the first real heading: f1's whole standalone profit and loss
# arrived "under" its CIN. It names the company, not a section.
_IDENTITY_LINE = re.compile(
    r"(?i:^\W*CIN\b|\bregistered\s+office\b|\bannual\s+report\b)"
    r"|^[A-Z0-9&.,'()\s-]*\b(?:LIMITED|LTD\.?|PLC|INC\.?)\W*$")


def is_identity_line(text: str, chrome=()) -> bool:
    """Whether `text` is a company identity line or a line of the page template.

    `chrome` is `DocumentModel.page_chrome`, the lines the reader found printed at the head or
    foot of page after page; a line the filing repeats that way is furniture whatever it says.
    """
    from app.services.pdf_extract import _chrome_key

    t = (text or "").strip()
    return bool(t) and (bool(_IDENTITY_LINE.search(t)) or _chrome_key(t) in set(chrome or ()))


# THE SECOND LINE OF A TWO-LINE COLUMN HEADING. A face prints "Note | Year ended 31 March | Year
# ended 31 March" over "No. | 2025 | 2024", and the reader took the lower line for a row: f1's
# standalone profit and loss opened with a row "No." whose figures were 2025 and 2024. The caption
# is a heading word of the note-reference or particulars column, and every figure is a year — the
# two together, because neither alone says the row is a heading.
_HEADING_CAPTION = re.compile(
    r"(?i)^\W*(?:note\s*no|note|notes|no|sr\.?\s*no|s\.?\s*no|particulars|year|years)\W*"
    r"(?P<unit>(?:(?:hk|us|s|nt)?\$|rmb|hkd|usd|人民[币幣]|港[币幣元])\s*[’'`]?\s*(?:0{3})?\W*)?$")
_YEAR = re.compile(r"^(?:19|20)\d\d$")
# THE "'000" OF A UNITS HEADING, read as a figure. 0160 prints its column heading as "Notes HK$'000
# HK$'000", the reader split "HK$'" from "000", and every balance-sheet and cash-flow block of the
# filing opened with a row "Notes HK$'" whose current figure was 0.
_UNIT_ZEROS = re.compile(r"^0+(?:\.0+)?$")


def is_column_heading_row(row) -> bool:
    """Whether `row` is a line of the statement's column heading read as though it were a row.

    The caption is a heading word of the note-reference or particulars column, and every figure is
    a year — or, where the caption carries the currency of a units heading, the "000" that heading
    printed after it.
    """
    m = _HEADING_CAPTION.match(getattr(row, "source_label", "") or "")
    if not m:
        return False
    figures = [str(getattr(ev, "value", "")).strip()
               for ev in (getattr(row, "values", None) or {}).values()
               if getattr(ev, "value", None) is not None]
    return bool(figures) and all(_YEAR.match(f) or (m.group("unit") and _UNIT_ZEROS.match(f))
                                 for f in figures)


def _shown_on_face(row, page, notes_pages) -> bool:
    """Whether a statement block may carry `row` — the one rule `face_rows` and `face_index` share.

    WHAT THE RUN ITSELF MARKS AS A NOTE STAYS OUT of a statement block: a row read off a page the
    classifier called notes, or one `buckets` placed as printed in the notes. The statement block
    is that statement as printed, and a note row in it reads as a line of the face. (A notes page
    the classifier read AS a statement is not second-guessed here.)

    THE STATEMENT'S FOOTER AND SIGNATURE BLOCK — "The accompanying notes …", "As per our report of
    even date", the signatories, place and date — is not a row of the statement. The pattern is
    `gap_explanation`'s, which already refuses these as missing lines.

    A LINE OF THE COLUMN HEADING read as a row is not one either (`is_column_heading_row`).
    """
    if page in notes_pages or _printed_in_notes(row):
        return False
    if _BOILERPLATE.search(getattr(row, "source_label", "") or ""):
        return False
    return not is_column_heading_row(row) and not is_signature_date_row(row)


# THE DATE OF SIGNING, read as a row. A statement's signature block ends "Mumbai | Mumbai | 29th May
# | 2026": the place under each signatory and the date, whose year stands in a figure column. Asian
# Paints' balance sheet sent it as a row "Mumbai Mumbai 29th May" with the figure 2026. A row is
# one when its caption ENDS on a month (and its day) and its every figure is a year — the day may
# have been read off as a figure of its own.
_MONTH_WORD = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
               r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DAY_MONTH_END = re.compile(rf"(?i)\b(?:{_MONTH_WORD}|{_MONTH_WORD}[\s,]+\d{{1,2}}(?:st|nd|rd|th)?)\W*$")


def is_signature_date_row(row) -> bool:
    """Whether `row` is the signature block's place-and-date line read as though it were a row."""
    if not _DAY_MONTH_END.search(getattr(row, "source_label", "") or ""):
        return False
    figures = [str(getattr(ev, "value", "")).strip()
               for ev in (getattr(row, "values", None) or {}).values()
               if getattr(ev, "value", None) is not None]
    return bool(figures) and all(_YEAR.match(f) for f in figures)


def _notes_pages(doc) -> set[int]:
    from app.core.models.enums import PageKind

    return {int(getattr(p, "index", -1)) for p in (getattr(doc, "pages", None) or ())
            if getattr(p, "kind", None) is PageKind.NOTES}


def _printed_in_notes(row) -> bool:
    return str(getattr(getattr(row, "printed_in", None), "value",
                       getattr(row, "printed_in", None)) or "").lower() == "notes"


def _figures_by_entity(row) -> dict[str, tuple[dict[str, str], dict[str, str]]]:
    """``{entity: (figures by period label, printed heading by period label)}`` for one row.

    The heading is the column's own (`ExtractedValue.column_heading`), else the date the page
    prints over it (`period_display`). Matrix values are skipped, as in :func:`_figures`.
    """
    out: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    for ev in (getattr(row, "values", None) or {}).values():
        if getattr(ev, "column_index", None) is not None or getattr(ev, "value", None) is None:
            continue
        basis = getattr(ev, "basis", None)
        entity = str(getattr(basis, "value", basis) or "consolidated")
        figures, legend = out.setdefault(entity, ({}, {}))
        label = str(getattr(ev, "period_label", "") or "?")
        figures[label] = printed_figure(ev)
        heading = getattr(ev, "column_heading", None) or getattr(ev, "period_display", None)
        if heading:
            legend[label] = str(heading)
    return out


class FaceIndex(list):
    """`face_index`'s rows, carrying what each row is shown printed under (`headings`: ``{id(row):
    [under, subheading, …]}``, over every block it is shown in) — the headings a citation's
    `group` is read against, so the heading a model copies back is the one it was shown."""

    headings: dict[int, list[str]]


def face_index(doc) -> FaceIndex:
    """`(statement, caption, row)` for every printed face row, for resolving a citation against.

    THE CAPTION IS THE ONE THE ROW IS SHOWN BY (`shown_caption`), and `headings` what it is shown
    under (`block_context`): a citation copies back what the request carried.

    THE RESOLVER'S SIDE OF THE BLOCK ABOVE. `face_rows` builds what the model is SHOWN; this builds
    what a citation is looked up IN, and the two must agree about which rows exist or the model can
    be shown a row it is then told does not resolve.

    NO STATEMENT FILTER HERE, deliberately, where `face_rows` takes one. A citation names the
    statement itself, so filtering twice would mean a model that cited the right caption under the
    wrong statement got "no such row" instead of a statement mismatch — and those are different
    facts to report. `resolve_sources` compares the statement and says which it was.

    A ROW WITH NO CAPTION IS OMITTED, because a citation is matched BY caption and an empty one
    would match every citation whose own caption normalised to nothing.

    THE SAME ROWS `face_rows` SENDS, apart from the statement filter: a row it leaves out — a notes
    page's, one printed in the notes, a footer or signature line, a column-heading line, one with
    no figure — is left out here too (`_shown_on_face`), so a citation can only land on a row the
    model was shown. Before, the lookup still held every row, and a citation of a caption the
    request never carried could resolve to a note row of the same wording.
    """
    out = FaceIndex((st, shown_caption(row), row) for st, _caption, row in _face_index_rows(doc))
    by_block, _legends = _face_blocks(doc)
    headings: dict[int, list[str]] = {}
    for (_key, rid), pair in _blocks_context(doc, by_block).items():
        seen = headings.setdefault(rid, [])
        for heading in pair:
            if heading and heading not in seen:
                seen.append(heading)
    out.headings = headings
    return out


def _face_index_rows(doc) -> list[tuple[str, str, object]]:
    """`(statement, printed caption, row)` for every printed face row — `face_index` before the
    caption a row is shown by."""
    # FOLDED, for the reason `face_rows` gives at length: the two sides of this join spell the
    # equity statement differently, and a citation naming the spelling its own line declares must
    # resolve against the spelling the page carries.
    stmt_of_page = {int(getattr(p, "index", -1)):
                    normalize_statement(str(getattr(p, "statement", "") or ""))
                    for p in (getattr(doc, "pages", None) or ())}
    notes_pages = _notes_pages(doc)
    out: list[tuple[str, str, object]] = []
    for row in (getattr(doc, "line_items", None) or ()):
        caption = getattr(row, "source_label", "") or ""
        if not caption:
            continue
        page = _page_of(row)
        if not _shown_on_face(row, page, notes_pages):
            continue
        out.append((_statement_of(row, stmt_of_page, page), caption, row))
    return out


def _pages_that_are_neither(doc) -> dict[int, object]:
    """The pages that are NEITHER a note NOR a classified statement face, by index.

    THE COMPLEMENT OF WHAT IS NORMALLY READ, stated as the same predicate
    `services.pdf_extract` selects its targets with, inverted — a notes page, or a page whose
    classifier verdict is one of the active statements, is read on every run; everything else is
    read only because a line declares `route: anywhere`. Keeping the two in one shape matters: a
    page that drifted between the two definitions would either be offered as an "other" page while
    already being in the statement block, or be reconstructed and then shown to nobody.
    """
    from app.core.models.enums import PageKind
    from app.services.statements import ACTIVE_STATEMENTS

    out: dict[int, object] = {}
    for page in (getattr(doc, "pages", None) or ()):
        if getattr(page, "kind", None) is PageKind.NOTES:
            continue
        if str(getattr(page, "statement", "") or "") in ACTIVE_STATEMENTS:
            continue
        out[int(getattr(page, "index", -1))] = page
    return out


def _is_printed(row) -> bool:
    """Whether this row was READ OFF A PAGE, rather than synthesised by the requesting stage.

    `stages.line_item_llm._write` CREATES A ROW for a line that had none, and gives it the cited
    row's provenance — so it lands on the cited page and, with nothing excluding it, would appear
    in the next request's block as though the filing had printed it there. Its caption is the
    LINE'S LABEL, not a printed caption, so a later citation could match a row nobody typeset.

    `confidence.method` is the test because this stage is the only writer of `llm` onto a face row
    at this point in the pipeline, and it is the same field `_llm_holds` and the export already
    read to mean "the model answered this".

    ASKED ONLY OF THE OTHER-PAGE BLOCK, and `face_rows` has the same exposure. It is left alone
    here rather than fixed in passing: the statement blocks are what every run sends, so changing
    which rows they carry is a behaviour change to measure against filings rather than to slip in
    beside a new route.
    """
    return not str(getattr(getattr(row, "confidence", None), "method", "") or "").lower().endswith(
        "llm")


#: At most this many pages go out in a request's `other_pages` when the line names its rows.
OTHER_PAGES_CAP = 4


def other_page_rows(doc, names=None, *, cap: int = OTHER_PAGES_CAP) -> list[dict]:
    """The printed rows of every page that is neither a statement nor a note. One entry per page.

    WHAT `route: anywhere` IS FOR, and what it could not reach until the extractor was widened to
    read these pages at all (`services.pdf_extract`). A five-year summary, a directors' report
    table, a schedule the classifier could not name — each prints captions with figures beside
    them, and no other route can see any of it.

    KEYED BY PAGE, because a page is all the identity such a row has. It sits on no statement (its
    page carries no statement verdict, or one outside the active set) and inside no note, so the
    two join keys every other block uses are both absent. `page` is the page's POSITION IN THE
    FILE, 1-based, and `printed_page` is the folio the page prints when it prints one — both,
    because they routinely differ by several pages and a reader checking the work needs the folio
    while the citation is resolved against the position.

    A ROW WITH NO FIGURE OR NO CAPTION IS OMITTED, for the same two reasons `face_rows` omits
    them: a caption with nothing beside it cannot be where a figure is printed, and a figure with
    no caption cannot be cited.

    NO DETERMINISTIC PROPOSAL TRAVELS WITH THESE ROWS, and there is none to travel. `stages.
    map_ontology` gates a caption match on the page's statement, so a page with no statement
    reaches no alias index — these rows are unclaimed by construction, which is why the block
    carries no `line` key where `face_rows` does.

    THE PAGES THAT PRINT THE LINE, when `names` is given — the line's printed names in every
    locale (`line_item_notes.printed_names`). Every such page of a 300-page annual report is the
    directors' report, the ESG tables, the five-year summary: sent whole, the block was the request.
    A page goes out only when a row on it is captioned with one of the names, every word of it
    present, compared as `line_item_notes.named_row_notes` compares a note row ("报告期末在职员工的
    数量合计（人）" for 在职员工的数量合计); the pages with most such rows first, at most `cap`. A
    name of one word is not used, for the reason given there. `names=None`, or names none of which
    has two words, is every page, as before.
    """
    others = _pages_that_are_neither(doc)
    wanted = None
    if names is not None:
        from app.services.line_item_notes import _folded
        # A line that names no row of two words or more is sent every page, as before.
        wanted = [n for n in (_folded(str(x)) for x in names) if len(n) >= 2] or None
    by_page: dict[int, list[dict]] = {}
    hits: dict[int, int] = {}
    for row in _rows_off_the_statements(doc):
        page = _page_of(row)
        if page is None or page not in others:
            continue
        if not _is_printed(row):
            continue
        caption = (getattr(row, "source_label", "") or "").strip()
        if not caption:
            continue
        figures = _figures(row)
        if not figures:
            continue
        by_page.setdefault(page, []).append({"caption": caption, "figures": figures})
        if wanted:
            folded = _folded(caption)
            if any(n <= folded for n in wanted):
                hits[page] = hits.get(page, 0) + 1
    if wanted is not None:
        chosen = sorted(hits, key=lambda pg: (-hits[pg], pg))[:max(cap, 0)]
        by_page = {pg: by_page[pg] for pg in chosen}
    out: list[dict] = []
    for page in sorted(by_page):
        entry: dict = {"page": page + 1, "rows": by_page[page]}
        if folio := str(getattr(others[page], "printed_page", "") or "").strip():
            entry["printed_page"] = folio
        out.append(entry)
    return out


def _rows_off_the_statements(doc) -> list:
    """`line_items` and the rows held apart for an `anywhere` line (`DocumentModel.
    other_page_items`) — every row the two functions here may show or resolve."""
    return [*(getattr(doc, "line_items", None) or ()),
            *(getattr(doc, "other_page_items", None) or ())]


def other_page_index(doc) -> list[tuple[int, str, object]]:
    """`(page, caption, row)` for every row `other_page_rows` shows — what a citation resolves in.

    `page` IS THE SAME 1-BASED NUMBER THE BLOCK SHOWS, so the model copies back what it was given
    and no side converts. A citation whose page is off by one resolves to nothing rather than to
    the neighbouring page's row, because the caption has to match as well — the same belt-and-
    braces the statement arm uses, and the reason a mis-cited page is reported instead of silently
    publishing the wrong figure.
    """
    others = _pages_that_are_neither(doc)
    out: list[tuple[int, str, object]] = []
    for row in _rows_off_the_statements(doc):
        if not _is_printed(row):
            continue
        caption = (getattr(row, "source_label", "") or "").strip()
        if not caption:
            continue
        page = _page_of(row)
        if page is None or page not in others:
            continue
        out.append((page + 1, caption, row))
    return out
