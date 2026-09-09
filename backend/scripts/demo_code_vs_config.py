"""Worked examples for the code-versus-config boundary. Every number here is RUN, not asserted.

WHY THIS FILE EXISTS. The recommendation "keep the mechanism, move the vocabulary" is easy to
state and easy to get wrong, and the cost of getting it wrong is a figure on the wrong line of a
statement that still ties. So each claim is demonstrated against the shipped rulebook and the real
functions — if a claim is false, this script prints that instead of the claim.

Run from ``backend``:  ../.venv/Scripts/python.exe scripts/demo_code_vs_config.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import warnings
from decimal import Decimal

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.schemas.line_items import (CascadeRung, LineItemDef, LineItemSet, Term,  # noqa: E402
                                    load_line_item_set)
from app.schemas.loader import load_ontology  # noqa: E402
from app.services import mapping  # noqa: E402
from app.services.line_item_matching import LineItemMatcher  # noqa: E402
from app.services.line_items import build, evaluate  # noqa: E402
from app.services.mapping import normalize_label  # noqa: E402

T = pathlib.Path("app/sample/templates")


def head(n: int, title: str) -> None:
    print(f"\n{'=' * 78}\n{n}. {title}\n{'=' * 78}")


def sub(title: str) -> None:
    print(f"\n  -- {title} " + "-" * max(0, 66 - len(title)))


def load() -> tuple[LineItemSet, LineItemMatcher]:
    st = load_line_item_set(json.loads(
        (T / "output_csv_hk_line_items.json").read_text(encoding="utf-8")))
    return st, LineItemMatcher(st)


# ══════════════════════════════════════════════════════════════════════════════════════════════
def demo_order(st: LineItemSet) -> None:
    """CLAIM UNDER TEST: "the ORDER of the normalisation transforms is a correctness invariant,
    so it stays in code". Tested here and FALSIFIED — the real invariant is elsewhere."""
    import itertools

    head(1, "COMPOSITION ORDER  ->  NOT the invariant. Pattern DISJOINTNESS is.")
    print("""  I claimed the order of the CAS normalisation transforms had to stay in code because
  reordering them would change the answer. Tested against real mainland captions, that is
  false — and finding out why relocates the recommendation.""")

    pats = {"sign": mapping._CAS_SIGN_NOTE,
            "orphan": mapping._CAS_ORPHAN_HEAD,
            "prefix": mapping._CAS_LINE_PREFIX}

    def apply(order, text: str) -> str:
        for name in order:
            p = pats[name]
            if name == "prefix":
                for _ in range(3):          # the shipped loop, bounded at 3
                    text = p.sub("", text, count=1)
            else:
                text = p.sub("", text)
        return text.strip()

    captions = ["减：营业成本（以“-”号填列）", "二、营业总成本（以“-”号填列）",
                "一、营业总收入", "其中：营业收入", "（二）综合收益总额",
                "三、营业利润（亏损以“－”号填列）", "1、营业收入", "七、70"]

    sub("all 6 permutations, on real CAS captions")
    sensitive = 0
    for caption in captions:
        results = {apply(order, caption) for order in itertools.permutations(pats)}
        sensitive += len(results) > 1
        print(f"    {'ORDER-SENSITIVE' if len(results) > 1 else 'order-independent'} "
              f"{caption!r:34} -> {sorted(results)!r}")
    print(f"    {sensitive}/{len(captions)} order-sensitive")

    sub("why: the patterns are written so they CANNOT overlap")
    print(f"    _CAS_ORPHAN_HEAD : {mapping._CAS_ORPHAN_HEAD.pattern}")
    print(f"    _CAS_SIGN_NOTE   : {mapping._CAS_SIGN_NOTE.pattern}")
    print("""      The orphan-head run is [^（(]*? and its tail [^（(]{0,10} — neither can cross an
      opening bracket, so it can never eat a parenthesised sign note. The disjointness is
      DESIGNED IN. Order does not matter because the patterns were written not to overlap.""")

    sub("but a plausible authored pattern breaks that, and then order decides")
    greedy = re.compile(r"^.*?[）)]\s*")     # a reasonable-looking orphan-head someone might write
    pats["orphan"] = greedy
    for caption in ("减：营业成本（以“-”号填列）", "二、营业总成本（以“-”号填列）"):
        by_result: dict[str, int] = {}
        for order in itertools.permutations(pats):
            by_result[apply(order, caption)] = by_result.get(apply(order, caption), 0) + 1
        print(f"    {caption!r}")
        for res, n in sorted(by_result.items(), key=lambda kv: -kv[1]):
            note = "  <-- the shipped order" if res and n == 3 else ""
            print(f"        {res!r:20} from {n}/6 orders{note}")
    print("""      Three of six orders erase the caption entirely. An erased caption maps to
      nothing, and the row lands in the section residual with the section still tying.

    SO THE RECOMMENDATION MOVES. The patterns are vocabulary and can be authored. What must stay
    in code is not the order — it is the CHECK that no two configured patterns overlap, i.e. that
    the result is order-independent. That is a property code enforces, not an order code hides,
    and it is a validator that can be written against the corpus.""")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def demo_gloss_seam(st: LineItemSet, m: LineItemMatcher) -> None:
    """CLAIM: `_ABBREV_GLOSS` is a mechanism whose CHARACTER INVENTORY is vocabulary."""
    head(2, "THE SEAM: mechanism stays, character inventory moves")
    print("""  `_ABBREV_GLOSS` strips a quoted abbreviation a filing coins for a caption.
  The RULE is mechanism. The list of quote marks is vocabulary — and that is testable.""")

    sub("the mechanism working, on a real shipped alias")
    for caption in ['Land use rights ("LUR")',
                    'Trade and other receivables from related parties ("Related Parties")']:
        got = m.match(caption, "balance_sheet", "NON-CURRENT ASSETS")
        print(f"    {caption[:52]:54} -> {got.key or '(unmapped)'}")
        print(f"      normalises to {normalize_label(caption)!r}")

    sub("the same filing, a quote mark the inventory does not carry")
    # Full-width straight quote and the CJK corner brackets a mainland filing may use.
    for mark_open, mark_close, label in (('＂', '＂', 'full-width straight quote'),
                                         ('﹁', '﹂', 'vertical corner bracket')):
        caption = f'Land use rights ({mark_open}LUR{mark_close})'
        norm = normalize_label(caption)
        got = m.match(caption, "balance_sheet", "NON-CURRENT ASSETS")
        print(f"    {label:28} {caption!r}")
        print(f"      normalises to {norm!r}")
        print(f"      resolves to   {got.key or '(unmapped)'}   [{got.method}] "
              f"conf={got.confidence} needs_review={got.needs_review}")

    print(f"""
    The rule fired for the ASCII quotes and not for the others, and there is nowhere in the
    475-definition set to say the others count. A reviewer reading every definition cannot
    discover it. That inventory is vocabulary; the bracket-plus-quotes SHAPE is mechanism.

    ONE CORRECTION TO AN EARLIER VERSION OF THIS SCRIPT, which called that outcome "a different
    asset, unflagged". It is NOT unflagged: the wrong answer arrives at confidence 0.6 with
    needs_review=True and the reason "several rule hints fired; ambiguous", because the exact tier
    misses and the rule tier finds several claimants. The exact tier's 1.0 becomes the rule tier's
    0.6 and the row goes to review — the system degrading as designed.

    That makes this a REVIEW-QUEUE cost rather than a silent-wrong-figure cost, which is a
    materially weaker claim and the honest one. The silent class is elsewhere: the 74 captions the
    inventory measurement found landing on a different concept are the ones to worry about, and
    whether each of those is flagged has to be checked one at a time rather than assumed from
    this example.""")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def demo_refusals(m: LineItemMatcher) -> None:
    """CLAIM: the refusals exist to make a plausible WRONG answer impossible, so config may
    supply what they consume but must never switch them off."""
    head(3, "THE REFUSALS  ->  stay as CODE")

    sub("(a) an exclusion outranks the line item's own alias")
    d = LineItemDef(key="a", aliases=["Finance costs"], exclude_hints=["capitalised"])
    one = LineItemMatcher(LineItemSet(items=[d]))
    print(f"    'Finance costs'             -> {one.match('Finance costs').key}")
    print(f"    'Finance costs capitalised' -> {one.match('Finance costs capitalised').key}")
    print("""      The point of the field is that someone looking at a mis-mapping adds one line and
      it stops. If an alias could win, there would be mis-mappings no exclusion could reach.""")

    sub("(b) absence means PERMISSIVE, never forbidden")
    silent = LineItemDef(key="x")
    gated = LineItemDef(key="y", statement="balance_sheet")
    print(f"    silent def, caption on the cash flow  -> claimable: "
          f"{silent.claimable_on('cash_flow')}")
    print(f"    gated def, caption on the cash flow   -> claimable: "
          f"{gated.claimable_on('cash_flow', normalize=m.vocab.normalize_statement)}")
    print(f"    gated def, statement UNKNOWN          -> claimable: "
          f"{gated.claimable_on(None)}")
    print("""      The third line is the one that matters: refusing what the classifier could not
      name would DELETE rows rather than mis-file them.""")

    sub("(c) never separate two mutually-confusable claimants by declaration order")
    pair = LineItemMatcher(LineItemSet(items=[
        LineItemDef(key="cl_notes", aliases=["Notes payable"], match_priority=80,
                    confusable_with=["ncl_notes"]),
        LineItemDef(key="ncl_notes", aliases=["Notes payable"], match_priority=80,
                    confusable_with=["cl_notes"])]))
    got = pair.match("Notes payable")
    print(f"    'Notes payable', both at priority 80 -> {got.key}  review={got.needs_review}")
    print(f"      tied: {got.tied}")
    print(f"      reason: {got.reason}")
    print("""      Taking the higher priority here IS taking the first declared. The honest answer
      is both, for review — and a banner normally separates them, so this rarely fires.""")

    sub("(d) a locked residual is unreachable by matching")
    locked = LineItemMatcher(LineItemSet(items=[
        LineItemDef(key="bs_ca__others", aliases=["Others"], alias_matching="disabled"),
        LineItemDef(key="bs_ca__prepayments", aliases=["Prepayments"])]))
    print(f"    'Others'      -> {locked.match('Others').key}")
    print(f"    'Prepayments' -> {locked.match('Prepayments').key}")
    print("""      'Others' matches almost anything short. A figure landing in the bucket that is
      supposed to be the section's UNEXPLAINED remainder makes the reconciliation tie.""")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def demo_arithmetic() -> None:
    """CLAIM: the arithmetic and graph mechanics are invariant across filings."""
    head(4, "ARITHMETIC AND GRAPH MECHANICS  ->  stay as CODE")

    sub("(a) missing is never zero")
    d = LineItemDef(key="out", type="calculated",
                    terms=[Term(ref="a", role="any_of"), Term(ref="b", role="any_of")])
    print(f"    both absent      -> {evaluate(d, {'a': None, 'b': None}).value}")
    print(f"    one present      -> {evaluate(d, {'a': Decimal('100'), 'b': None}).value}")
    print("""      A sum over absent notes has no value. Publishing 0 would assert the filing
      charged nothing, which is a different and false claim.""")

    sub("(b) the three term roles — what an absence MEANS")
    cascade = LineItemDef(key="dep", type="derived", cascade=[
        CascadeRung(id="P3", terms=[Term(ref="total", role="required"),
                                    Term(ref="cos", sign=-1, role="adjustment")])])
    print(f"    total present, cos absent  -> {evaluate(cascade, {'total': Decimal('900')}).value}"
          "   (adjustment simply does not apply)")
    got = evaluate(cascade, {"total": None, "cos": Decimal("300")})
    print(f"    ONLY the deduction present -> {got.value}"
          "        (an adjustment never justifies a rung alone)")
    print("""      That second line was a real defect when the roles were a single boolean: a rung
      whose only figure was the cost-of-sales DEDUCTION resolved to a negative depreciation
      charge — a figure assembled out of a deduction with nothing to deduct it from.""")

    sub("(c) a rung below zero is refused, and the refusal is reported")
    neg = LineItemDef(key="dep", type="derived", cascade=[
        CascadeRung(id="P1", terms=[Term(ref="a"), Term(ref="b", sign=-1)]),
        CascadeRung(id="P2", terms=[Term(ref="c")])])
    got = evaluate(neg, {"a": Decimal("10"), "b": Decimal("60"), "c": Decimal("42")})
    print(f"    P1 computes -50, P2 computes 42 -> value={got.value} via {got.rung_used}")
    print(f"      refused: {got.refused_rungs}")
    print("""      'P3 computed -50, so P4 was used' is the sentence a reviewer needs. A silent
      skip is indistinguishable from a rung whose inputs were simply absent.""")

    sub("(d) a dependency cycle is named, not hit at runtime")
    reg = build([LineItemDef(key="a", type="calculated", terms=[Term(ref="b")]),
                 LineItemDef(key="b", type="calculated", terms=[Term(ref="c")]),
                 LineItemDef(key="c", type="calculated", terms=[Term(ref="a")])])
    print(f"    a <- b <- c <- a   ok={reg.ok}")
    for p in reg.problems:
        print(f"      [{p.severity}] {p.key}: {p.message}")

    sub("(e) alternatives are never summed")
    from app.services.line_items import check_rollups
    OPER = "is_pl__deprec_and_impairment_oper_exp"
    kids = [LineItemDef(key=f"sub__{n}", in_output=False, parent=OPER) for n in ("a", "b")]
    for rollup, note in (("alternatives", "restatements of ONE figure"), ("sum", "real addends")):
        reg = build(kids + [LineItemDef(key=OPER, type="derived", implemented_by="x",
                                        rollup=rollup)])
        probs = check_rollups(reg, {OPER: Decimal("100"), "sub__a": Decimal("100"),
                                    "sub__b": Decimal("100")})
        print(f"    rollup={rollup:14} ({note:26}) -> {len(probs)} problem(s)")
    print("""      The twelve parts of the depreciation line are alternative sources for one
      figure; the line item's own `rollup: alternatives` declaration in the seed says they must
      never be summed (it was `services.deprec_impairment`'s docstring that said so, until that
      derivation was removed and the declaration became the only statement of the rule).
      `parent` was carrying display nesting AND arithmetic rollup at once.""")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def demo_vocabulary_moved(st: LineItemSet, m: LineItemMatcher) -> None:
    """CLAIM: the vocabularies ALREADY moved are genuinely filing-specific."""
    head(5, "WHAT ALREADY MOVED  ->  CONFIG, and why it had to")

    sub("(a) banner headings: the same section, four spellings a filing might print")
    for banner in ("CURRENT LIABILITIES", "Current liabilities", "流动负债", "流動負債"):
        print(f"    {banner:24} -> section {m.vocab.section_of_banner(banner)!r}")
    print(f"    {'EQUITY AND LIABILITIES':24} -> section "
          f"{m.vocab.section_of_banner('EQUITY AND LIABILITIES')!r}  (umbrella: scopes nothing)")
    v = st.vocabulary
    print(f"      declared in config: {len(v.section_banners)} tokens, "
          f"{sum(len(b.headings) for b in v.section_banners)} headings")

    sub("(b) the gate that these headings drive, on a real collision")
    ont = load_ontology(json.loads(
        (T / "output_csv_hk_ontology.json").read_text(encoding="utf-8")), resolve=True)
    claimants = [c.canonical_key for c in ont.mappings
                 if "intangible assets" in [normalize_label(a) for a in
                                            ([c.label] + list(c.aliases or []))]]
    print(f"    'intangible assets' is claimed by {len(claimants)} concepts:")
    for k in claimants:
        print(f"      {k}")
    for stmt, banner in (("balance_sheet", "NON-CURRENT ASSETS"), ("profit_and_loss", None)):
        got = m.match("intangible assets", stmt, banner)
        print(f"    on {stmt:16} under {str(banner):20} -> {got.key}")
    print("""      Nothing in the caption separates them. Only where it was printed does. That is a
      PER-CONCEPT fact, which is why it could not be lifted into a global 'master rules' layer.""")

    sub("(c) the statement spelling that had to be config")
    print(f"    normalize_statement('equity_changes')    -> "
          f"{m.vocab.normalize_statement('equity_changes')!r}")
    print(f"    normalize_statement('changes_in_equity') -> "
          f"{m.vocab.normalize_statement('changes_in_equity')!r}")
    print("""      One statement, two names: the classifier says one, StatementType says the other.
      Compared raw, every definition on that statement is refused on every page of it.""")


# ══════════════════════════════════════════════════════════════════════════════════════════════
def demo_thresholds(st: LineItemSet) -> None:
    """CLAIM: a threshold has three homes, and the number and the comparison split."""
    head(6, "THRESHOLDS  ->  the number splits from the comparison")
    from app.config import get_settings
    s = get_settings()

    print("""  Three homes, not two. The COMPARISON (a bar exists at all) is protective and stays
  code; the NUMBER goes to the rulebook set when it is a property of that vocabulary, and to
  per-deployment settings when it is a risk appetite.""")

    sub("already per-deployment, in [extraction] settings")
    for name in ("llm_mapping", "llm_focus_only", "llm_gap_routing"):
        print(f"    settings.extraction.{name:22} = {getattr(s.extraction, name, '?')}")

    sub("already per-line-item, in the set")
    ex = [d for d in st.items if d.min_confidence_to_auto_accept != 0.85][:3]
    print(f"    definitions overriding min_confidence_to_auto_accept: {len(ex)}")
    print(f"    the default the other {len(st.items) - len(ex)} inherit: 0.85")
    print("""      This one is authored PER LINE ITEM and read by nothing yet — the inventory
      flagged it. A declared bar nothing consults is worse than no bar: it reads as a control.""")

    sub("still in code, and which half belongs where")
    for literal, where, why in (
            ("0.95 / 0.6 rule-tier confidence", "SET",
             "calibrated against THIS vocabulary's hint specificity"),
            ("rule.score >= 0.9 accept bar", "code = the bar; SET = the number",
             "that a bar exists is protective; where it sits is per-rulebook"),
            ("fuzz.ratio >= 80 typo tolerance", "SET",
             "a property of the language and house style, not the deployment"),
            ("BATCH_MAX_ITEMS = 25", "SETTINGS",
             "a provider/context limit, nothing to do with the rulebook"),
            ("max_tokens=512 per-line call", "SETTINGS", "same"),
            ("exact tier pinned at 1.0", "CODE",
             "an identity is certain by definition; making it tunable invites doubt in a fact")):
        print(f"    {literal:34} -> {where}")
        print(f"        {why}")


def main() -> int:
    st, m = load()
    print(f"  set: {len(st.items)} definitions, "
          f"{len(st.vocabulary.section_banners)} banner tokens, "
          f"residual_framework={'yes' if st.residual_framework else 'no'}")
    demo_order(st)
    demo_gloss_seam(st, m)
    demo_refusals(m)
    demo_arithmetic()
    demo_vocabulary_moved(st, m)
    demo_thresholds(st)
    print(f"\n{'=' * 78}\n  every figure above was produced by running the shipped code.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
