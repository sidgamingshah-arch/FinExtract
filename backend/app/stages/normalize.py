"""Sign & unit normalization stage — and what the rulebook expects the fact to be.

Produces the sign-normalized ``ExtractedValue.value`` from the printed ``value_raw`` using
signals the printed magnitude alone doesn't carry:

* ``Less:`` / ``Add:`` label cues — a line prefixed "Less:" is a deduction (negative);
  "Add:" is an addition (positive).
* the ontology's ``sign_rule.flip_if_label_matches`` regexes for the mapped concept — the
  ontology author's targeted sign corrections.

The printed-sign tier (parentheses / trailing minus) is already decoded into ``value_raw``
by ``services.numbers`` at extraction; this stage layers the label-driven corrections on top.
Values with no applicable cue keep ``value == value_raw``.

WHERE PARENTHESES ARE DECIDED, and why the global switch is reported rather than honoured.
``parse_number`` reads the LOCALE's ``number_format.negative`` (``["paren", "minus"]``), which is
the live switch and the right altitude for it: parentheses-as-negative is a convention of how a
filing is printed. ``global_rules.paren_means_negative`` is a second copy of the same switch that
the parser never sees, and it cannot be honoured after the fact — only the signed magnitude
survives extraction, so nothing downstream could tell "(600)" from "-600" in order to undo one
and not the other. The duplicate switch that pretended otherwise has been removed from
``GlobalRules``: parentheses are decided by ``number_format_by_locale`` alone, at the one point
where the printed text is still in hand.

Once a value is normalised, two v2 declarations say what the resulting FACT is supposed to look
like, and both are checked here because this is the stage that finishes the number:

* ``sign_convention`` (``positive_expected`` / ``negative_expected`` / ``either``) is an
  EXPECTATION, not a transformation — the rulebook says so in as many words: "a concept whose
  sign_convention is positive_expected or negative_expected but whose loaded value carries the
  opposite sign is a review trigger, not an auto-correction". So a figure arriving with the wrong
  sign is flagged and its sign confidence drops; the value is left exactly as reported.
  ``sign_rule`` keeps doing the normalising — the two are different jobs on purpose, since a
  concept can legitimately want a targeted flip AND an expected sign.

  ONE EXCEPTION, and the rulebook states it as a separate rule beside that one:
  ``sign_convention.unsigned_source`` — "where a filing prints expenses as unsigned positives in a
  by-nature list, negate on load and set sign_normalised: true on the fact so the transformation is
  auditable". That is a different claim about a different thing. The expectation sentence is about
  ONE row disagreeing with its siblings, which is a mis-mapping and must not be papered over by a
  flip. This one is about a whole statement's expenses being printed without signs, which is a house
  style with no row to suspect — and leaving it alone is not neutral, because the template's
  subtotals are signed sums (``pl_gross_profit = sum(revenue, cost_of_goods_sold)``), so every
  subtotal on such a filing comes out wrong and every ratio built on one with it.
  ``_negate_unsigned_expenses`` implements it, and the test that separates the two cases is
  unanimity across the statement — see that method and ``_UNSIGNED_COHORT_MIN``.

* ``temporality`` (instant / duration) and ``unit_of_account`` (balance / flow / subtotal) say
  whether the concept is a position at a date or a movement over a period. The balance-sheet
  non-controlling-interests BALANCE and the profit-attribution FLOW share their caption exactly,
  and the filing prints them in different statements; a caption match alone will happily file the
  P&L figure on the balance sheet, where it is individually plausible and every subtotal still
  ties. The two are different facts, so the figure is NOT merged into the concept: the row keeps
  its value and provenance and loses the concept, which puts it in the review queue instead of on
  the balance sheet. The statement each temporality belongs to is read from the rulebook too — an
  ontology that declares its balance-sheet sections ``duration`` gets no such finding.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.models import DocumentModel
from app.core.models.line_item import UnitContext
from app.core.stage import PipelineContext
from app.services.buckets import statement_resolver

_LESS = re.compile(r"^\s*(less|deduct)\b|less:", re.IGNORECASE)
_ADD = re.compile(r"^\s*add\b|add:", re.IGNORECASE)

# "Amounts in ₹ crore", "(RMB'000)", "in thousands of USD", "figures in HK$ million" …
_UNIT_SCALE = {"thousand": Decimal(1_000), "lakh": Decimal(100_000), "lac": Decimal(100_000),
               "ten thousand": Decimal(10_000),
               "million": Decimal(1_000_000), "mn": Decimal(1_000_000),
               "hundred million": Decimal(100_000_000),
               "crore": Decimal(10_000_000), "cr": Decimal(10_000_000),
               "billion": Decimal(1_000_000_000), "bn": Decimal(1_000_000_000)}
# Most real filings never spell the scale out: the statement column head simply reads
# "RMB'000" / "HK$’000" / "人民幣千元". Those idioms ARE the declaration, so they are matched
# alongside the spelled-out words — including the curly apostrophe, which is what a PDF text
# layer almost always yields. Patterns are tried in order at the leftmost match, so the longer
# idiom wins where two overlap ('000,000 over '000, 百萬 over 萬).
_SCALE_PATTERNS = [
    (re.compile(r"(?<![\d.])['’`]0{3}[,.]0{3}"), "million"),        # RMB'000,000
    (re.compile(r"(?<![\d.])['’`]0{3}"), "thousand"),               # RMB'000, HK$’000, ₹'000
    (re.compile(r"百萬|百万"), "million"),
    (re.compile(r"千元"), "thousand"),        # 人民幣千元 — bare 千 is too weak to trust
    # …and a bare 亿/万 is weaker still, so the same caution has to apply to them. These used to
    # match ANYWHERE in the scanned text, and Chinese narrative prose is full of them — a business
    # review reading "营业收入10.5亿元" is not a units declaration. Measured on a 210-page CSRC
    # filing whose statements are presented in plain 元: detection returned scale_factor
    # 100,000,000 ("hundred million") from the front matter, i.e. every figure in the document
    # declared 100-million-fold too large. A units declaration writes the 元: 单位：万元,
    # 人民币亿元. The explicit 单位/金额单位 prefix is honoured without it, because that phrasing
    # IS the declaration and leaves no room for doubt.
    # What separates a DECLARATION from a MEASUREMENT is a figure in front of the unit: "单位：亿元"
    # and "人民币亿元" declare the whole statement's scale, while "营业收入10.5亿元" is a sentence
    # about one number. Requiring 元 was not enough on its own — narrative prose writes 元 as
    # readily as a column head does.
    #
    # A single-character lookbehind is not enough either, and that is what a PDF text layer
    # punishes: it breaks these strings apart, so the same sentence arrives as "营业收入 10.5 亿元"
    # and the character before 亿 is a SPACE. `_NO_FIGURE_BEFORE` therefore looks back past
    # whitespace for a digit, and is applied to these two entries only — see `_scan_scale`. The
    # '000 patterns above keep their own inline guard, since "'000" cannot be spaced apart.
    (re.compile(r"(?:億|亿)元|(?:金额|金額)?单位\s*[:：]\s*(?:億|亿)"), "hundred million"),
    (re.compile(r"(?:萬|万)元|(?:金额|金額)?单位\s*[:：]\s*(?:萬|万)"), "ten thousand"),
    (re.compile(r"\b(thousands?|lakhs?|lacs?|millions?|mn|crores?|cr|billions?|bn)\b",
                re.IGNORECASE), None),                              # label taken from the match
]
# MATCHED IN LIST ORDER (see the `next(...)` below), not by position in the text — so every
# COMPOUND currency name has to precede the bare 元 at the end. 港元, 日元, 美元 and 新台币 all
# contain 元, and a PRC filing that declares only "单位：元" has nothing else to go on.
#
# That bare 元 is why this list needed touching: a CSRC filing states its unit as 元 or 人民币元,
# and with no entry for it detection returned currency "" — the front end then had no currency to
# show at all, so a ¥254,937,045 balance rendered as a bare number on screen. HKD came through on
# an HKEX filing because "HK$" is unmissable; RMB did not, because the character it hangs on was
# absent from this table.
_CCY = [("₹", "INR"), ("rs.", "INR"), ("inr", "INR"),
        ("hk$", "HKD"), ("hkd", "HKD"), ("港幣", "HKD"), ("港币", "HKD"), ("港元", "HKD"),
        ("rmb", "CNY"), ("cny", "CNY"), ("人民幣", "CNY"), ("人民币", "CNY"),
        ("us$", "USD"), ("usd", "USD"), ("美元", "USD"),
        ("新台幣", "TWD"), ("新台币", "TWD"), ("日圓", "JPY"), ("日元", "JPY"),
        ("歐元", "EUR"), ("欧元", "EUR"),
        ("$", "USD"), ("€", "EUR"), ("eur", "EUR"), ("£", "GBP"), ("gbp", "GBP"),
        # LAST, and only after every compound above has had its chance: on a Chinese filing a
        # lone 元 or ¥ means renminbi.
        ("¥", "CNY"), ("元", "CNY")]
# A statement-face count high enough to cover every face of a group annual report, but bounded
# so a mis-classified 270-page document can't turn detection into a full-text scan.
_MAX_FACE_SCAN = 12


# A figure standing in front of the unit, across any amount of whitespace the text layer inserted.
# "10.5 亿元" is a measurement; "单位： 亿元" is a declaration.
_NO_FIGURE_BEFORE = re.compile(r"[\d.]\s*$")
# The CJK amount-unit entries, by label — the only ones the guard applies to.
_FIGURE_SENSITIVE = {"hundred million", "ten thousand"}


def _scan_scale(text: str) -> str | None:
    """The scale label declared in `text`, preferring the earliest declaration in reading order.

    A CJK unit is accepted only where no figure precedes it. Without that, one sentence of a
    business review ("营业收入 10.5 亿元") declared an entire filing to be presented in
    hundred-millions — measured on a CSRC filing whose statements are in plain 元, which came out
    carrying scale_factor 100,000,000.
    """
    best: tuple[int, str] | None = None
    for rx, label in _SCALE_PATTERNS:
        for m in rx.finditer(text):
            word = (label or m.group(1).rstrip("s")).lower()
            # `finditer`, not `search`, because the FIRST occurrence may be a measurement while a
            # real declaration follows it — taking only the first would hide the true one.
            if word in _FIGURE_SENSITIVE and _NO_FIGURE_BEFORE.search(text[:m.start()]):
                continue
            if best is None or m.start() < best[0]:
                best = (m.start(), word)
            break
    return best[1] if best else None


def _detect_units(ctx: PipelineContext, fmt: str, doc: DocumentModel | None = None
                  ) -> UnitContext | None:
    """Detect a source scale + currency from a units declaration (e.g. "Amounts in ₹ crore").
    Returns None when nothing is declared — the caller then treats values as reported rather
    than guessing a scale.

    In an annual report the declaration lives on the *statement* pages (p.100+), not the cover,
    so the front matter is scanned first (a cover banner still wins when present) and then each
    statement face. A chunk only ends the search once it yields a scale: a stray currency
    mention in the front matter must not pre-empt the real "RMB'000" column head further in.

    A FACE PAGE WHOSE STATEMENT NEVER RESOLVED IS NOT A STATEMENT PAGE, and this scan used to
    treat it as one. `DocumentModel.face_pages` returns every page the classifier called FACE
    whatever became of its statement, and this reads the first twelve of them — so a page that
    merely LOOKED like a statement face could declare the scale for the whole filing.

    MEASURED, ON BOTH REFERENCE FILINGS. 000709: 50 FACE pages, and 4 of the 12 the scan saw had
    no resolved statement — three of them at indices 6-8, AHEAD of the first real statement page
    at 81. 688008: 22 FACE pages, 5 of the 12 unresolved (indices 144-148). Neither filing's
    unresolved pages happen to declare a scale, so both currently detect correctly — by luck
    rather than by design. A bond-section table, a five-year summary or a segment schedule that
    does declare one would set the scale for every figure in the document.

    SO THE SCAN NOW ASKS THE SAME QUESTION THE EXTRACTOR ALREADY ASKS. `services.pdf_extract`
    refuses exactly these pages when it chooses what to reconstruct (`p.statement in
    ACTIVE_STATEMENTS`), and a page not trusted to yield a ROW should not be trusted to declare
    the units every row is read in. It also buys coverage rather than costing it: all twelve
    slots now hold real statement pages instead of four or five being spent on pages that are
    not statements at all.

    AND IT FALLS BACK RATHER THAN STARVING. Where no face page resolved a statement — one filing
    in the corpus resolves none — the filter would leave only the front matter, so the unfiltered
    faces are used instead. That is the same convention `pdf_extract` applies to its own target
    set: absence of a signal means "unconstrained", never "nothing is allowed"."""
    from app.services.derived import document_text
    from app.services.statements import ACTIVE_STATEMENTS

    try:
        pages = document_text(ctx.raw_bytes or b"", fmt)
    except Exception:  # noqa: BLE001
        return None
    by_index = dict(pages)
    # (page index for provenance, text) chunks in scan order; front matter is one chunk so a
    # scale and currency split across the first two pages still combine, as they always have.
    chunks: list[tuple[int | None, str]] = [(None, " ".join(t for _, t in pages[:2]))]
    faces = doc.face_pages() if doc is not None else []
    resolved = [p for p in faces if str(getattr(p, "statement", "") or "") in ACTIVE_STATEMENTS]
    for page in (resolved or faces)[:_MAX_FACE_SCAN]:
        chunks.append((page.index, by_index.get(page.index, "")))

    currency: str | None = None
    for index, raw in chunks:
        text = raw.lower()
        if not text:
            continue
        # Remember the first currency seen: the face page declaring the scale may print only
        # "'000" with the currency stated once, elsewhere.
        currency = currency or next((code for tok, code in _CCY if tok in text), None)
        scale_word = _scan_scale(text)
        if scale_word is None:
            continue
        return UnitContext(currency=currency or "", units_label=scale_word,
                           scale_factor=_UNIT_SCALE.get(scale_word, Decimal(1)),
                           source_bbox_page=index)
    if currency is None:
        return None
    # Currency without a scale: record it, but keep scale 1 — figures are as reported.
    return UnitContext(currency=currency, scale_factor=Decimal(1), units_label=None)


_SIGN_EXPECTED = ("positive_expected", "negative_expected")

# ``global_rules.sign_convention.unsigned_source``: "Where a filing prints expenses as unsigned
# positives in a by-nature list, negate on load and set sign_normalised: true on the fact so the
# transformation is auditable." Matched on the instruction rather than the whole sentence, so an
# author may reword the explanation; delete the instruction and the transformation goes with it,
# which is the only way the sentence can be read as a specification.
_NEGATE_ON_LOAD = "negate on load"
# The row-level flag, one per line item however many columns were flipped.
_NORMALISED_FLAG = "sign_normalised:unsigned_source"
# A CAS cash-flow block: 经营活动现金流入小计 opens the operating outflows, 经营活动现金流出小计 closes them.
_CAS_FLOW_SUBTOTAL = re.compile(r"(经营|投资|筹资)活动现金流(入|出)小计")
_CAS_OUTFLOW_FLAG = "sign_normalised:cas_outflow"

# How many DISTINCT expense concepts must agree before an all-positive cohort is read as the
# filing's presentation rather than as mis-mapped rows.
#
# This is the number that keeps ``unsigned_source`` from swallowing ``validation`` — the sentence
# beside it, which says the opposite for the individual case: "a concept whose sign_convention is
# positive_expected or negative_expected but whose loaded value carries the opposite sign is a
# review trigger, NOT an auto-correction". Both are true, of different things. One expense row
# positive among negative siblings is the mis-mapped row that sentence is about, and flipping it
# would hide the defect behind a plausible figure. Every expense on the statement positive is not a
# row at all, it is a house style, and there is no sibling to suspect.
#
# Three, because the competing explanation has to be three independent mapping errors that all
# happen to fall the same way — far less likely than one presentation convention. Distinct
# CONCEPTS, not values: three rows summing into one "Others" bucket is one signal, not three.
_UNSIGNED_COHORT_MIN = 3


def _negate_on_load_declared(ontology) -> bool:
    """Whether the rulebook asks for an unsigned expense list to be negated as it loads."""
    rules = getattr(ontology, "global_rules", None)
    sentence = str((getattr(rules, "sign_convention", None) or {}).get("unsigned_source") or "")
    return _NEGATE_ON_LOAD in sentence.lower()


def _statement_shape(ontology) -> dict[str, tuple[str | None, frozenset[str]]]:
    """Per statement, the ``temporality`` and the units of account its concepts declare.

    Derived from the rulebook rather than stated here, because "the balance sheet is instant" is a
    fact about the rulebook's sections, not about this module: a v1 definition declares neither and
    gets no expectation at all, and one that changed its mind would change the finding.

    Only a UNANIMOUS temporality is used. A statement whose concepts disagree cannot say anything
    about a row printed on it, and guessing from a majority would flag the minority as wrong.
    ``subtotal`` is left out of the unit set — a subtotal is neither a balance nor a flow, so it
    would otherwise look foreign on every statement it appears on.
    """
    temporal: dict[str, set[str]] = {}
    units: dict[str, set[str]] = {}
    for m in getattr(ontology, "mappings", []) or []:
        statement = getattr(m.statement, "value", None) or m.statement
        if not statement:
            continue
        if m.temporality:
            temporal.setdefault(statement, set()).add(m.temporality)
        if m.unit_of_account and m.unit_of_account != "subtotal":
            units.setdefault(statement, set()).add(m.unit_of_account)
    return {st: (next(iter(temporal[st])) if len(temporal.get(st, ())) == 1 else None,
                 frozenset(units.get(st, ())))
            for st in set(temporal) | set(units)}


def _natural_negative_keys(template_def: dict | None) -> set[str]:
    """The template lines whose arithmetic values are negative contra-assets."""
    pending = [section for statement in (template_def or {}).get("statements", [])
               for section in statement.get("sections", [])]
    keys: set[str] = set()
    while pending:
        node = pending.pop()
        if node.get("sign") == "natural_negative" and node.get("canonical_key"):
            keys.add(node["canonical_key"])
        pending.extend(node.get("children") or [])
    return keys


class NormalizeStage:
    name = "normalize"

    def run(self, doc: DocumentModel, ctx: PipelineContext) -> DocumentModel:
        # Source units/currency ("Amounts in ₹ crore") — recorded so the UI/export can convert
        # to a chosen presentation unit knowing the base (and never guessing when undeclared).
        detected = _detect_units(ctx, doc.fmt.value, doc)
        if detected is not None:
            doc.unit_context = detected
            ctx.log(f"normalize:units={detected.units_label or 'as_reported'}"
                    f"/{detected.currency or 'unknown_ccy'}")

        ontology = getattr(ctx, "ontology", None)
        sign_by_key: dict[str, list] = {}
        expected_sign: dict[str, str] = {}
        temporality: dict[str, str] = {}
        unit_of_account: dict[str, str] = {}
        # BOTH ATTRIBUTE NAMES, and the model shape as well as the dict — the same four lines
        # `residual`, `gap_closing`, `map_ontology` and `contingent_liabilities` already use.
        # `services.documents._context` sets `ctx.template` and nothing has ever set
        # `ctx.template_def`, so reading only the latter made `_natural_negative_keys` return the
        # empty set on EVERY run: the 13 lines the template marks `sign: natural_negative` were
        # never negated, and each one is summed (`op: "sum"`) into a parent that therefore ADDED a
        # deduction. On 澜起科技 688008 库存股 published +427,557,874.81, so the statement screen
        # showed total equity 12,251,621,314.53 against a printed 11,396,505,564.91 and a balancing
        # total of 13,074,027,136.00 against a printed 12,218,911,386.38 — both out by exactly
        # twice the treasury holding, in both periods.
        template = getattr(ctx, "template_def", None) or getattr(ctx, "template", None)
        if hasattr(template, "model_dump"):
            template = template.model_dump(mode="json")
        natural_negative = _natural_negative_keys(template)
        if ontology is not None:
            for m in getattr(ontology, "mappings", []) or []:
                pats = getattr(getattr(m, "sign_rule", None), "flip_if_label_matches", None) or []
                if pats:
                    sign_by_key[m.canonical_key] = [re.compile(p, re.IGNORECASE) for p in pats]
                if m.sign_convention in _SIGN_EXPECTED:
                    expected_sign[m.canonical_key] = m.sign_convention
                if m.temporality:
                    temporality[m.canonical_key] = m.temporality
                if m.unit_of_account:
                    unit_of_account[m.canonical_key] = m.unit_of_account

        changed = 0
        for li in doc.line_items:
            label = li.source_label or ""
            less = bool(_LESS.search(label))
            add = bool(_ADD.search(label))
            flips = sign_by_key.get(li.canonical_key or "", [])
            flip = any(rx.search(label) for rx in flips)
            contra = li.canonical_key in natural_negative
            if not (less or add or flip or contra):
                continue
            for ev in li.values.values():
                raw = ev.value_raw
                if raw is None:
                    continue
                if ev.sign_normalised:
                    # ALREADY ORIENTED BY AN UPSTREAM DECISION, so re-deriving it from ``value_raw``
                    # would undo that decision silently. The note→face split publishes a component
                    # whose ``value_raw`` is the NOTE's figure and whose ``value`` carries the
                    # FACE's convention, and it only publishes at all once the components account
                    # for the aggregate in every column. Those rows keep the note row's caption, so
                    # a note that prints "Add: Mainland China" or "Less: ..." reached this pass and
                    # recomputed the figure back to the note's sign — leaving the children summing
                    # to the wrong total against a parent whose canonical key the split had already
                    # cleared, so nothing re-checked it, while ``sign_normalised`` and
                    # ``split_sign_flipped`` still claimed the flip had happened. The flag exists to
                    # say "the engine has settled this number's sign"; honouring it here is what
                    # makes that true.
                    continue
                v = raw
                if less:
                    v = -abs(raw)
                elif add:
                    v = abs(raw)
                if flip:
                    v = -v
                if contra:
                    v = -abs(raw)
                if v != ev.value:
                    ev.value = v
                    if contra:
                        ev.sign_normalised = True
                    changed += 1

        ctx.log(f"normalize:sign_adjusted={changed}")
        self._negate_unsigned_cas_outflows(doc, ctx)
        # AFTER the per-row adjustments above, so a "less:" prefix or a sign_rule flip has already
        # had its say and the cohort is judged on finished figures. BEFORE `_check_expectations`, so
        # a statement this negates is not then reported as carrying the wrong sign — the whole point
        # is that it no longer does.
        if _negate_on_load_declared(ontology):
            self._negate_unsigned_expenses(doc, ctx, expected_sign)
        self._check_expectations(doc, ctx, ontology, expected_sign, temporality, unit_of_account)
        return doc

    @staticmethod
    def _negate_unsigned_cas_outflows(doc: DocumentModel, ctx: PipelineContext) -> int:
        """Negate a CAS cash-flow outflow block that the filing printed unsigned.

        The CSRC layout prints each activity as an inflow block closed by 经营/投资/筹资活动现金流入
        小计 and an outflow block closed by the matching 现金流出小计, every figure a positive
        magnitude. The template's activity totals are SUMS of signed lines, so an unsigned outflow
        was ADDED: 000709's financing total computed to +259.7bn against a printed -2.9bn. Three
        operating captions carried a flip by caption; investing and financing carried none.

        The block is the evidence, not the caption: every row the layout prints between the inflow
        subtotal and the outflow subtotal is an outflow, whatever it is called — 支付其他与筹资活动
        有关的现金 and the 其中 rows included. The same unanimity rule as ``unsigned_source``: one
        negative printed figure in the block means the filing signs its outflows, and the block is
        left alone. ``value_raw`` keeps what the page printed; ``sign_normalised`` records the flip.
        """
        statement_of = statement_resolver(doc)
        blocks: list[list] = []
        open_for: str | None = None
        for li in doc.line_items:
            label = re.sub(r"\s+", "", li.source_label or "")
            hit = _CAS_FLOW_SUBTOTAL.search(label)
            if hit:
                activity, direction = hit.group(1), hit.group(2)
                if direction == "入":
                    open_for = activity
                    blocks.append([])
                elif open_for == activity:
                    open_for = None
                continue
            if open_for is not None and statement_of(li) in (None, "cash_flow"):
                blocks[-1].append(li)
        negated = 0
        for members in blocks:
            values = [ev for li in members for ev in li.values.values()
                      if (ev.value_raw if ev.value_raw is not None else ev.value) not in (None, 0)]
            raws = [ev.value_raw if ev.value_raw is not None else ev.value for ev in values]
            if not raws or any(v < 0 for v in raws):
                continue
            for li in members:
                for ev in li.values.values():
                    raw = ev.value_raw if ev.value_raw is not None else ev.value
                    if raw in (None, 0):
                        continue
                    ev.value = -abs(raw)
                    ev.sign_normalised = True
                    if _CAS_OUTFLOW_FLAG not in ev.confidence.flags:
                        ev.confidence.flags.append(_CAS_OUTFLOW_FLAG)
                    negated += 1
                if _CAS_OUTFLOW_FLAG not in li.confidence.flags:
                    li.confidence.flags.append(_CAS_OUTFLOW_FLAG)
        if negated:
            ctx.log(f"normalize:cas_outflows negated={negated} value(s) in {len(blocks)} block(s)")
        return negated

    @staticmethod
    def _negate_unsigned_expenses(doc: DocumentModel, ctx: PipelineContext,
                                  expected_sign: dict[str, str]) -> int:
        """Negate a statement's expenses where the filing printed all of them unsigned.

        ``global_rules.sign_convention.unsigned_source``. The one place this pipeline changes the
        sign of a reported figure, so what licenses it matters:

        * The decision is per STATEMENT, because printing costs unsigned is a presentational choice
          a filing makes for a statement — not per row (which is the mis-mapping case the
          ``validation`` sentence governs) and not per column (one filing does not print this year's
          costs in parentheses and last year's without).
        * The cohort must be UNANIMOUS. A single negative among the positives means the filing does
          use signs, and the positives are then rows to look at rather than a convention to follow.
          The same reasoning ``_statement_shape`` applies to temporality: a statement whose concepts
          disagree cannot say anything about a row printed on it.
        * The cohort must be ``_UNSIGNED_COHORT_MIN`` distinct concepts wide. See that constant.

        What it does NOT touch: ``positive_expected`` concepts (the rule speaks only of expenses and
        outflows), ``either`` concepts (subtotals, working-capital movements, fair-value changes — the
        rulebook says "retain the reported sign; never coerce"), and ``value_raw``, which keeps what
        the page printed so the flip can be audited against it.
        """
        # Asks the ROW before the page it was printed on. A mainland filing prints its
        # statements back to back and a boundary lands mid-page, so a cohort gathered by page
        # index mixes a balance sheet's rows in with the income statement's — and this cohort
        # decides which figures get their sign flipped. See `buckets.statement_resolver`.
        statement_of = statement_resolver(doc)

        cohorts: dict[str, list[tuple[object, object, Decimal]]] = {}
        for li in doc.line_items:
            if expected_sign.get(li.canonical_key or "") != "negative_expected":
                continue
            statement = statement_of(li)
            if statement is None:
                continue
            for ev in li.values.values():
                figure = ev.value if ev.value is not None else ev.value_raw
                if figure is None or figure == 0:
                    continue
                if ev.sign_normalised:
                    # Out of the cohort entirely, not merely spared the negation. A value an
                    # upstream stage has already oriented says nothing about how the FILING prints
                    # its expenses, which is the only question the unanimity test asks — and a
                    # split-published component is oriented to the face while its ``value_raw``
                    # still holds the note's sign, so counting it would let the note's presentation
                    # vote on the statement's.
                    continue
                cohorts.setdefault(statement, []).append((li, ev, figure))

        negated = 0
        for statement, members in sorted(cohorts.items()):
            concepts = {li.canonical_key for li, _ev, _v in members}
            if not all(figure > 0 for _li, _ev, figure in members):
                continue                    # the filing uses signs; `validation` governs each row
            if len(concepts) < _UNSIGNED_COHORT_MIN:
                # Logged rather than silent: "we saw two positive expenses and left them alone" is a
                # decision a reviewer chasing a failing subtotal needs to be able to find.
                ctx.log(f"normalize:unsigned_source({statement}) not applied — "
                        f"{len(concepts)} concept(s), below the {_UNSIGNED_COHORT_MIN} needed")
                continue
            for li, ev, figure in members:
                ev.value = -figure
                ev.sign_normalised = True
                ev.confidence.flags.append(f"{_NORMALISED_FLAG}:{statement}")
                if _NORMALISED_FLAG not in li.confidence.flags:
                    li.confidence.flags.append(_NORMALISED_FLAG)
            negated += len(members)
            ctx.log(f"normalize:unsigned_source({statement}) negated={len(members)} value(s) "
                    f"across {len(concepts)} concept(s): {sorted(concepts)[:4]}")
        return negated


    @staticmethod
    def _check_expectations(doc: DocumentModel, ctx: PipelineContext, ontology,
                            expected_sign: dict[str, str], temporality: dict[str, str],
                            unit_of_account: dict[str, str]) -> None:
        """Compare each finished figure with what its concept says it should be.

        Nothing here changes a number. The sign check records a finding; the balance-versus-flow
        check unfiles the row, which removes a WRONG figure rather than producing one — the value,
        the label and the provenance all stay on the row for the reviewer who has to place it.
        """
        if ontology is None:
            return
        shape = _statement_shape(ontology)
        statement_of = statement_resolver(doc)
        wrong_sign = confused = 0
        for li in doc.line_items:
            key = li.canonical_key
            if not key:
                continue

            want = expected_sign.get(key)
            if want:
                for ev in li.values.values():
                    val = ev.value if ev.value is not None else ev.value_raw
                    if val is None or val == 0:
                        continue
                    if (want == "positive_expected" and val > 0) or (
                            want == "negative_expected" and val < 0):
                        continue
                    # Flagged, never flipped: the opposite sign usually means the row is on the
                    # wrong concept, and flipping it would hide that behind a plausible figure
                    # while the template's subtotal identities quietly stopped meaning anything.
                    ev.confidence.sign = min(ev.confidence.sign, 0.35)
                    ev.confidence.flags.append(f"sign_opposite_to_expected:{want}")
                    flag = f"sign_opposite_to_expected:{key}"
                    if flag not in li.confidence.flags:
                        li.confidence.flags.append(flag)
                    wrong_sign += 1

            statement = statement_of(li)
            expect = shape.get(statement or "")
            if expect is None:
                continue
            want_temporality, want_units = expect
            got_temporality = temporality.get(key)
            got_unit = unit_of_account.get(key)
            reasons = []
            if want_temporality and got_temporality and got_temporality != want_temporality:
                reasons.append(f"temporality:{got_temporality}!={want_temporality}")
            # A subtotal is neither a balance nor a flow, so it is not compared on units.
            if want_units and got_unit and got_unit != "subtotal" and got_unit not in want_units:
                reasons.append(f"unit_of_account:{got_unit}!={'/'.join(sorted(want_units))}")
            if not reasons:
                continue
            li.canonical_key = None
            li.confidence.mapping = min(li.confidence.mapping or 0.3, 0.3)
            li.confidence.flags.append(f"balance_flow_confusion:{key}:{','.join(reasons)}")
            li.confidence.flags.append("low_mapping_confidence")
            confused += 1
            ctx.log(f"normalize:balance_flow_confusion {key} on {statement}"
                    f" ({';'.join(reasons)}) row={li.source_label!r}")
        if wrong_sign or confused:
            ctx.log(f"normalize:sign_unexpected={wrong_sign} balance_flow_confusion={confused}")
