"""Structural validation — the arithmetic the template and the rulebook declare, used to catch
mis-mapped values.

A label matcher can only ever be wrong about *which* concept a number belongs to, and no
amount of string evidence exposes that. Arithmetic does: if ``Gross profit != Revenue − Cost
of sales``, or a subtotal misses the sum of its declared children, then a value sits on the
wrong line or carries the wrong sign. This module evaluates four families of relation, none of
them hardcoded here:

* the TEMPLATE's ``rollup`` on subtotal/total nodes and its statement-level ``identities``;
* the RULEBOOK's ``validation.identities`` — one authored ``expr`` per relation, each with its
  own ``severity``, evaluated exactly as written (see ``ontology_identities``);
* the RULEBOOK's ``validation.cross_concept_guards`` — pairs that are individually plausible and
  jointly wrong, which no arithmetic can reach (see ``cross_concept_guards``);
* the RULEBOOK's ``validation.section_reconciliation`` — every section with a reported subtotal
  must account for its printed rows (see ``section_relations``).

Two rules keep it honest on partial extractions (the normal case for a 270-page filing):

* A relation is evaluated only when the total AND every declared component was actually
  extracted and mapped for that (basis, period) — *unless* the relation sums a statement
  section that owns a residual bucket. There, an absent child is genuinely nil and is taken as
  zero, so the relation is checked rather than skipped. The difference is completeness: once
  every printed line in a section is routed either to a specific concept or to that section's
  "Others" (see ``stages.residual``), a child that is still absent is a line the filing does
  not print — and a line a filing omits is zero, not unknown. Without that guarantee the same
  assumption would fail nearly every subtotal, which is why it is gated on the bucket existing
  rather than applied everywhere. Keys taken as zero are listed in ``assumed_zero`` on the
  result, so a pass is never mistaken for full extraction.
* No value is derived, defaulted or back-filled to make a relation balance. A concept mapped
  twice with conflicting values is likewise refused rather than resolved by picking one.

Sums are signed, so the arithmetic holds whichever way the filing states expenses (a cost of
sales printed in parentheses arrives negative and simply adds). When a relation fails, the same
signed arithmetic can say whether flipping exactly one participant's sign would satisfy it —
reported as ``sign_suspect``, because a mis-signed value is a different fix from a mis-mapped
one. It is a hypothesis attached to the finding, never an adjustment.

EVERY relation produces a row, including the ones that could not be run, and each skip carries a
``reason`` a caller can classify (:mod:`app.services.coverage`). That is what stops "3 relations
passed" from reading as "the statement is validated". A relation the RULEBOOK authored so that it
can never run is not a skip at all — it is reported with the distinct ``error`` status, see
``AUTHORING_REASONS``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from app.services.rollups import CARVED_FROM_FACE, prints_loss_with_minus
from app.core.models import LineItem, RuleResult, StructuralReport
from app.core.models.enums import LineRole
from app.schemas.template import TemplateDefinition
from app.services.reconcile import tolerance

# The tolerance convention used by DecompositionRule / the reconcile engine: an absolute floor
# with a relative band so large figures aren't held to sub-unit precision. Rollups carry no
# per-node tolerance; identities declare their own.
TOLERANCE_ABS = Decimal(1)
TOLERANCE_REL = Decimal("0.001")

Slot = tuple[str, str | None]          # (basis, period_label)

# A mapping method whose OUTPUT was chosen in order to close the very gap a relation measures.
# ``stages.gap_closing`` shows the model the failing subtotal and asks which unplaced rows belong
# in that section's bucket, explicitly so "a subtotal the routing fixes reports as tied". Any
# relation fed by such a value therefore cannot fail, and reporting it as a pass would be
# circular: the answer was derived from the question. It is reported as ``derived_input`` and
# classified TAUTOLOGICAL — not recoverable by better extraction, and never a pass.
DERIVED_METHODS = ("llm_gap_routing",)

# The ways a RULEBOOK relation or guard can be authored so that it never runs on any filing: an
# expression that does not parse, a term naming nothing the template declares, a term repeated, a
# guard sentence matching no predicate. These are reported with their own status, not as
# ``skipped``, because a skip is a coverage fact better extraction can recover and these never are.
#
# The one that shipped proves why the distinction has to be visible in the row: ``cf_movement`` is
# declared BLOCKING and named three terms (``net_operating``, ``net_investing``, ``net_financing``)
# the template has never declared, so it sat in the skip pile beside the input-absent rows on every
# filing — occupying the slot where a reviewer expects cash-flow assurance while proving nothing,
# and looking for all the world like thin extraction. ``coverage`` alarms on it (see
# ``ALARM_UNENFORCEABLE``); this is what stops it reading as an ordinary skip on the way there.
#
# ``unsupported_op`` is deliberately NOT here. It is not a rulebook defect: the template schema
# refuses the op at the upload gate (``schemas.template.Rollup``), so an op reaching evaluation is a
# definition stored before that gate existed. It already gets the authoring error the loud way — as
# a rejected upload — and the row here is the read-path backstop.
AUTHORING_REASONS = ("unparsable_expr", "unresolved_terms", "repeated_term",
                     "guard_unrecognised", "guard_components_unknown")
STATUS_AUTHORING_ERROR = "error"


@dataclass(frozen=True)
class Relation:
    """One arithmetic relation read off the template or the rulebook: ``target = op(components)``.

    ``signs`` carries a per-component multiplier for a relation authored as a signed expression
    (``a = b + c - d``); empty means the multiplier follows ``op``. ``severity`` is the rulebook's
    own consequence-of-a-break, defaulted to blocking for the template's relations so their
    existing treatment is unchanged. ``broken`` is set when the relation could not be BUILT at all
    (an unparsable expression, a term naming nothing in the template) — it is reported with that
    reason rather than dropped, because a declared relation the engine silently discards is
    indistinguishable from one that passed. Those reasons carry the ``error`` status, not
    ``skipped``: see ``AUTHORING_REASONS``.
    """

    id: str
    # rollup | identity | ontology_identity | section_reconciliation
    kind: str
    statement: str
    target: str                        # canonical_key
    components: tuple[str, ...]        # canonical_keys
    op: str
    tol_abs: Decimal = TOLERANCE_ABS
    tol_rel: Decimal = TOLERANCE_REL
    severity: str = "blocking"
    signs: tuple[int, ...] = ()
    # Components whose figure is a MAGNITUDE rather than a signed amount, declared by the
    # template's ``cost_magnitude_children``. Carried here so this engine and the calculated-line
    # engine spend such a component the same way: two readings of one relation is a spread whose
    # face and whose review queue contradict each other on the same subtotal.
    magnitudes: frozenset[str] = frozenset()
    note: str = ""
    # ``residual_framework.reconciliation.tolerance`` is authored as "one rounding unit per
    # contributing row", which is a per-relation quantity rather than a constant.
    tol_per_row: bool = False
    broken: str = ""
    broken_detail: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)   # merged into the result's details


@dataclass(frozen=True)
class Guard:
    """One ``validation.cross_concept_guards`` entry, resolved into something evaluable.

    The rulebook authors these as sentences, so both halves of a guard are read OUT of the
    sentence: the canonical keys it names, and the predicate its wording selects. Renaming a key
    in the sentence changes which concepts are compared; deleting the sentence deletes the check.

    ``precondition`` is what has to be true of the filing before the guard can say anything.
    ``"always"`` means nothing does — such a guard must always return pass or fail, so a skip
    from one is a defect in this module, not thin extraction (see :mod:`app.services.coverage`).
    """

    id: str
    predicate: str
    keys: tuple[str, ...]
    statement: str
    precondition: str
    text: str
    severity: str = "warning"
    broken: str = ""
    broken_detail: dict = field(default_factory=dict)


@dataclass
class MappedValues:
    """Mapped values per canonical_key per (basis, period).

    Several printed lines legitimately share one concept — three depreciation lines under
    "Depreciation and amortisation", two tax payments under "Income tax paid", and everything a
    section's residual bucket absorbs — so repeated mappings are ADDED, exactly as the statement
    view and the Excel export present them. ``contributors`` records how many lines each figure
    came from.

    Summing rather than refusing also detects more: if two lines wrongly land on one concept the
    subtotal stops tying and the rollup FAILS, which names the problem. Treating the key as
    unusable instead skipped the relation and said nothing at all.
    """

    values: dict[str, dict[Slot, Decimal]] = field(default_factory=dict)
    scales: dict[str, dict[Slot, Decimal]] = field(default_factory=dict)
    contributors: dict[str, dict[Slot, int]] = field(default_factory=dict)
    # canonical_key -> how its figure came to exist, for the keys that were not simply read off
    # the page. See ``DERIVED_METHODS``.
    derived: dict[str, str] = field(default_factory=dict)
    # Retained for callers that still ask; nothing populates it now that repeats are summed.
    ambiguous: set[str] = field(default_factory=set)
    # (key, slot) whose figure was read out of captions the face already carries — counted on its
    # own line, never again inside a relation. See ``rollups.CARVED_FROM_FACE``.
    carved: set = field(default_factory=set)
    # (key, slot) whose every row is a face row printed SIGNED by its own caption — a CAS
    # 信用减值损失（损失以“-”号填列）. A magnitude relation follows the printed sign for it, as the
    # template rollup does. See ``rollups.prints_loss_with_minus``.
    signed: set = field(default_factory=set)

    def get(self, key: str, slot: Slot) -> Decimal | None:
        return self.values.get(key, {}).get(slot)

    def slots(self, key: str) -> list[Slot]:
        return list(self.values.get(key, {}))

    def scale(self, key: str, slot: Slot) -> Decimal:
        return self.scales.get(key, {}).get(slot, Decimal(1))

    def sources(self, key: str, slot: Slot) -> int:
        return self.contributors.get(key, {}).get(slot, 0)

    def all_slots(self) -> list[Slot]:
        seen = {slot for per_key in self.values.values() for slot in per_key}
        return sorted(seen, key=lambda s: (s[0], s[1] or ""))


def _printed(ev) -> Decimal | None:
    """The value as extracted. Deliberately not ``reconciled``: the template's rollups describe
    the figures as presented, and the note→face subtraction is a different question."""
    v = ev.value if ev.value is not None else ev.value_raw
    return None if v is None else Decimal(v)


def _is_restatement(ev) -> bool:
    """Whether this value is the statement of changes in equity restating a balance-sheet figure.

    `services.equity_matrix` raises the flag on every closing balance it transposes out of the
    matrix, and `periods.summable` — the reader the grid, the export and the KPIs use — drops such a
    value wherever the balance sheet printed the same line itself.
    """
    from app.services.equity_matrix import TRANSPOSED_FLAG

    return TRANSPOSED_FLAG in tuple(getattr(getattr(ev, "confidence", None), "flags", None) or ())


def collect_values(items: Iterable[LineItem]) -> MappedValues:
    items = list(items)
    # A RESTATEMENT NEVER ADDS TO A SLOT THE STATEMENTS ALREADY FILL — `periods.summable`'s rule,
    # applied here so the checks validate the figure the grid publishes rather than a different
    # one. Without it the equity-matrix closing balance of a line the balance sheet also prints was
    # counted twice: on 佳明 share capital entered `section_reconciliation:bs_equity` as 28,404 for
    # a printed 14,202, and on China SCE 1966 non-controlling interests as 21,517,154 for a printed
    # 10,758,577. Where the face printed nothing, the restatement is the only statement of the
    # figure and still counts, exactly as it does on the grid.
    printed_slots = {(li.canonical_key, (ev.basis.value, ev.period_label))
                     for li in items if li.canonical_key
                     for ev in li.values.values()
                     if _printed(ev) is not None and not _is_restatement(ev)}
    out = MappedValues()
    unsigned_slots: set = set()
    for li in items:
        key = li.canonical_key
        if not key:
            continue
        printed_signed = (getattr(li.printed_in, "value", li.printed_in) == "face"
                          and prints_loss_with_minus(li.source_label))
        for ev in li.values.values():
            val = _printed(ev)
            if val is None:
                continue
            slot: Slot = (ev.basis.value, ev.period_label)
            if _is_restatement(ev) and (key, slot) in printed_slots:
                continue
            if CARVED_FROM_FACE in (ev.confidence.flags or ()):
                out.carved.add((key, slot))
            (out.signed if printed_signed else unsigned_slots).add((key, slot))
            seen = out.values.setdefault(key, {})
            seen[slot] = val if slot not in seen else seen[slot] + val
            out.contributors.setdefault(key, {})[slot] = out.sources(key, slot) + 1
            out.scales.setdefault(key, {})[slot] = ev.unit_ctx.scale_factor
        # ``confidence.method`` is the winning mapping method (what the API serves as
        # ``mapping_method``); ``is_computed`` marks a figure this pipeline calculated rather than
        # read. Either makes every relation the key participates in circular.
        if li.confidence.method in DERIVED_METHODS:
            out.derived[key] = li.confidence.method or ""
        elif li.is_computed:
            out.derived.setdefault(key, "computed")
    out.signed -= unsigned_slots
    return out


# The row flags each writer of a computed figure leaves beside ``is_computed`` (note_sourced's
# table and sentence paths, the model's prose path, the component assembler). A run stored before
# the served row carried ``is_computed`` is read through them.
_COMPUTED_ROW_FLAGS = ("note_sourced:", "note_sourced_prose:", "prose_sourced_value:",
                       "assembled_from:")
# Rows added to the served list after the pipeline, which no relation ever read.
_ADDED_AFTER_THE_RUN = ("supplemental",)


def _row_amount(v: dict) -> Decimal | None:
    raw = v.get("value")
    if raw is None:
        return None
    try:
        return Decimal(str(raw).replace(",", ""))
    except (ArithmeticError, ValueError):
        return None


def _row_is_computed(row: dict) -> bool:
    if "is_computed" in row:
        return bool(row["is_computed"])
    return any(str(f).startswith(_COMPUTED_ROW_FLAGS) for f in row.get("flags") or ())


def _replaced_by_edits(rows: list[dict]) -> set[int]:
    """The value dicts an analyst's edit replaces, by ``id``.

    An edit is the analyst's figure for the CONCEPT in that (basis, period), not one more
    contributor beside the printed ones: ``periods.concept_value`` shows the edited row's figure
    alone, so every other row's figure for the same concept and slot is left out here too. The
    edited row is the first in the group that carries the edit, as there.
    """
    from app.services.periods import CURRENT, PRIOR, edited_for, slot_for

    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("canonical_key"):
            groups.setdefault(r["canonical_key"], []).append(r)
    out: set[int] = set()
    for group in groups.values():
        if not any(r.get("edited") for r in group):
            continue
        bases = {v.get("basis") or "consolidated" for r in group for v in r.get("values") or []}
        periods = {CURRENT, PRIOR} | {str(s).partition("/")[2] for r in group
                                       for s in r.get("edited_slots") or ()}
        for basis in bases:
            for period in periods:
                editor = next((r for r in group if edited_for(r, basis, period)), None)
                if editor is None:
                    continue
                kept = slot_for(editor, basis, period)
                for r in group:
                    slot = slot_for(r, basis, period)
                    if slot is not None and slot is not kept:
                        out.add(id(slot))
    return out


def items_of_rows(rows: Iterable[dict]) -> list[LineItem]:
    """The SERVED rows as the line items this module evaluates, so a stored run can be evaluated
    again after an edit or a remap.

    The served row is what every screen and export reads (``routes/extractions._serialize_rows``),
    and an edit or a remap changes it and nothing else: ``canonical_key`` is retagged by a remap,
    a slot's ``value`` is overwritten by an edit. Each row becomes one line item carrying what the
    evaluation reads — the concept, where it was printed, the mapping method, whether the pipeline
    computed it, and per value its basis, period label, figure, scale and flags. Values keep their
    order and are never merged, so two printed figures for one slot still add.

    An edited slot replaces its concept's figure in that slot (``_replaced_by_edits``); a slot
    cleared by an edit has no figure. Rows added to the list after the run (the supplemental
    fields) are left out: the run never evaluated them.
    """
    from app.core.models import Basis, ConfidenceVector, ExtractedValue, UnitContext
    from app.core.models.enums import PrintedIn

    rows = [r for r in rows if (r.get("mapping_method") or "") not in _ADDED_AFTER_THE_RUN]
    replaced = _replaced_by_edits(rows)
    out: list[LineItem] = []
    for r in rows:
        key = r.get("canonical_key")
        if not key:
            continue
        try:
            printed_in = PrintedIn(r["printed_in"]) if r.get("printed_in") else None
        except ValueError:
            printed_in = None
        li = LineItem(source_label=str(r.get("source_label") or ""), canonical_key=key,
                      printed_in=printed_in, is_computed=_row_is_computed(r))
        li.confidence.method = r.get("mapping_method")
        for i, v in enumerate(r.get("values") or []):
            amount = _row_amount(v)
            if amount is None or id(v) in replaced:
                continue
            try:
                basis = Basis(v.get("basis") or "consolidated")
                scale = Decimal(str(v["scale"])) if v.get("scale") else Decimal(1)
            except (ValueError, ArithmeticError):
                continue
            flags = list(((v.get("confidence") or {}).get("flags")) or ())
            li.values[str(i)] = ExtractedValue(
                value=amount, value_raw=amount, basis=basis, period_label=v.get("period_label"),
                unit_ctx=UnitContext(scale_factor=scale),
                confidence=ConfidenceVector(flags=flags))
        out.append(li)
    return out


def _statement_index(template: TemplateDefinition) -> dict[str, str]:
    """canonical_key → the statement it is printed on, so a relation can be told apart from one
    belonging to a statement this filing does not contain at all."""
    out: dict[str, str] = {}
    for st in template.statements:
        for node in template._walk(st.sections):
            if node.canonical_key:
                out[node.canonical_key] = st.type.value
    return out


def relations(template: TemplateDefinition) -> list[Relation]:
    """Every relation the template declares, as canonical keys.

    Rollup children and identity terms are node_ids (identities may also name a canonical_key),
    so both are resolved through the node table before anything is looked up by key.
    """
    key_of = {n.node_id: n.canonical_key for n in template.all_nodes()}
    known = template.all_canonical_keys()

    def resolve(term: str) -> str | None:
        return key_of.get(term) or (term if term in known else None)

    out: list[Relation] = []
    for st in template.statements:
        stype = st.type.value
        for node in template._walk(st.sections):
            if node.rollup is None or not node.rollup.children:
                continue
            # A ROLLUP THAT NAMES A `reported_total_key` IS A RESIDUAL, NOT AN EQUATION.
            #
            # 30 of the output-CSV template's 61 rollups point `reported_total_key` at THEMSELVES
            # with `reported_total_op: diff`: the line's published figure is its own printed total
            # LESS the children that found a more specific home. `services/rollups.evaluate`
            # implements exactly that subtraction, and `bs_ca__other_current_assets` (36 children),
            # `bs_cl__other_current_liabilities` (48) and `bs_ca__trade_and_other_receivables` (2)
            # are the section and family sweep buckets.
            #
            # Reading one as `target == sum(children)` asserts THE REMAINDER IS ALWAYS ZERO, which
            # is the opposite of what the node is for — the same mistake
            # `test_no_residual_bucket_was_turned_into_a_sum` already forbids on the config side,
            # where writing `terms` for a residual "would turn the unexplained remainder into the
            # sum of the parts, which is a different figure and a plausible-looking one".
            #
            # It reported 1,525% on 000709's `bs_ca__trade_and_other_receivables` (printed
            # 410,968,833, children 6,678,873,137) and 809,297,783 on China SCE's — a break on
            # every filing, being the residual itself. No relation is emitted: the template does
            # not assert an equality here, so there is nothing to answer. What these buckets hold
            # is still checked, by the section reconciliation they are a member of.
            if node.rollup.reported_total_key:
                continue
            children = [resolve(c) for c in node.rollup.children]
            if node.canonical_key is None or any(c is None for c in children):
                continue               # a dangling reference; the loader reports it on upload
            magnitudes = {resolve(c) for c in node.rollup.cost_magnitude_children}
            out.append(Relation(
                id=f"rollup:{node.canonical_key}", kind="rollup", statement=stype,
                target=node.canonical_key, components=tuple(children), op=node.rollup.op,
                magnitudes=frozenset(m for m in magnitudes if m is not None),
            ))
        for ident in st.identities:
            lhs, rhs = resolve(ident.lhs), [resolve(c) for c in ident.rhs.children]
            if lhs is None or any(c is None for c in rhs):
                continue
            out.append(Relation(
                id=f"identity:{ident.id}", kind="identity", statement=stype,
                target=lhs, components=tuple(rhs), op=ident.rhs.op,
                tol_abs=Decimal(str(ident.tolerance_abs)),
                tol_rel=Decimal(str(ident.tolerance_rel)),
            ))
    return out


def _validation_of(source):
    """The validation rules, whichever shape they arrive in.

    `source` may be a rulebook carrying a nested `validation` block, or a `ValidationRuleSet` —
    the separate master those rules now live in. They are the SAME rules; where they are stored is
    a packaging decision this evaluator has no reason to care about, and accepting both is what
    lets the master be adopted without a flag day.

    Duck-typed on `.identities` rather than isinstance-checked, so this module need not import the
    schema and the schema need not import this one.
    """
    if source is None:
        return None
    if hasattr(source, "identities"):
        return source
    return getattr(source, "validation", None)


# --- validation.identities ---------------------------------------------------------------------
# The rulebook authors each identity as one expression over canonical keys. Only ``+`` and ``-``
# over bare identifiers are accepted: anything else (a literal, a product, a parenthesised group)
# is reported as ``unparsable_expr`` rather than approximated, because an identity evaluated
# differently from how it reads is worse than one that is visibly not evaluated.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_EXPR_RE = re.compile(rf"^\s*({_IDENT})\s*=\s*([+-]?\s*{_IDENT}(?:\s*[+-]\s*{_IDENT})*)\s*$")
_TERM_RE = re.compile(rf"([+-]?)\s*({_IDENT})")


def ontology_identities(template: TemplateDefinition, ontology) -> list[Relation]:
    """The rulebook's ``validation.identities``, as evaluable relations.

    Evaluated exactly as authored, including the ones that hold only under a stated sign
    convention: ``pl_gross_profit = revenue + cost_of_goods_sold`` is correct arithmetic *because*
    ``global_rules.sign_convention`` stores expenses negative. Rewriting it to a subtraction to
    make it "look right" would pass on a filing whose costs were loaded with the wrong sign, which
    is the single defect this identity exists to catch.
    """
    # ACCEPTS EITHER SHAPE — see `_validation_of`.
    rules = _validation_of(ontology)
    if rules is None:
        return []
    key_of = {n.node_id: n.canonical_key for n in template.all_nodes()}
    known = template.all_canonical_keys()
    stmt_of = _statement_index(template)

    def resolve(term: str) -> str | None:
        return key_of.get(term) or (term if term in known else None)

    out: list[Relation] = []
    for ident in rules.identities:
        rid = f"ontology_identity:{ident.id}"
        match = _EXPR_RE.match(ident.expr or "")
        if match is None:
            out.append(Relation(id=rid, kind="ontology_identity", statement="", target="",
                                components=(), op="signed_sum", severity=ident.severity,
                                note=ident.note, broken="unparsable_expr",
                                broken_detail={"expr": ident.expr}))
            continue
        lhs = resolve(match.group(1))
        unresolved = [] if lhs else [match.group(1)]
        comps: list[str] = []
        signs: list[int] = []
        for sign, name in _TERM_RE.findall(match.group(2)):
            key = resolve(name)
            if key is None:
                unresolved.append(name)
                continue
            comps.append(key)
            signs.append(-1 if sign == "-" else 1)
        base = dict(id=rid, kind="ontology_identity", statement=stmt_of.get(lhs or "", ""),
                    target=lhs or "", components=tuple(comps), op="signed_sum",
                    signs=tuple(signs), severity=ident.severity, note=ident.note)
        if unresolved:
            out.append(Relation(**base, broken="unresolved_terms",
                                broken_detail={"terms": sorted(set(unresolved))}))
            continue
        if len(set(comps)) != len(comps):
            # ``_check`` keys the component values by canonical_key, so a term appearing twice
            # would silently be counted once and the identity would evaluate to the wrong figure.
            out.append(Relation(**base, broken="repeated_term",
                                broken_detail={"terms": sorted({c for c in comps
                                                                if comps.count(c) > 1})}))
            continue
        out.append(Relation(**base))
    return out


# --- validation.section_reconciliation ---------------------------------------------------------

# WHERE ORIENTATION IS NEVER IN DOUBT. An unoriented cost breaks a sum only on a statement that
# NETS — revenue against costs, proceeds against purchases. The balance sheet does not net: its
# asset and liability sections are magnitudes, and its equity section carries the signs the filing
# itself prints (an accumulated deficit, treasury shares), which `normalize` retains rather than
# coerces. So a balance-sheet sum is the template's arithmetic whatever its section declares.
#
# THIS EXEMPTION IS LOAD-BEARING, not a convenience: both shipped rulebooks declare their equity
# section `either`, and `total equity = equity attributable to owners + non-controlling interests`
# is plain addition that has to stay checked — `tests/test_review_suppression` is built on exactly
# that relation breaking, and gating it took a whole file's fixture down with it.
_SIGNS_NEVER_IN_DOUBT = ("balance_sheet",)


def _signs_established(rel: "Relation", section_of: dict[str, str],
                       one_signed: frozenset[str] | None,
                       costs: frozenset[str] = frozenset()) -> bool:
    """Whether this relation's signs are established, so a plain sum of it is the arithmetic.

    A plain SUM asserts that its components add to its target, which needs their signs. They are
    established three ways, and any one is enough:

    * the RULEBOOK declares every section the keys sit in one-signed (`positive_expected`: the
      members are magnitudes however the filing prints them);
    * the FILING signs those sections — `one_signed` also carries the sections whose costs this
      filing prints in parentheses (see `_signed_on_filing`), because a section that prints its
      costs negative adds up as printed;
    * the TEMPLATE spends the relation's costs as magnitudes (`cost_magnitude_children`): every cost
      among its components is declared a magnitude and the target is not itself a cost, so the sum
      takes each cost off whatever sign it was printed with.

    `either` alone establishes nothing: it says "retain the reported sign; never coerce", and on a
    statement that nets (revenue against costs, proceeds against purchases) a filing printing costs
    unsigned then makes the sum something other than the template's arithmetic.

    A RULEBOOK THAT DECLARES NOTHING GATES NOTHING. `evaluate_structure`'s `ontology` is optional
    and additive — "without it only the template's own relations are evaluated, which is what a
    caller validating a spread against a template alone wants" — so with no section conventions in
    hand there is no declaration to act on, and the template's own arithmetic stands as the
    assertion. The gate is a refinement a rulebook buys, not a precondition it has to satisfy.
    """
    if one_signed is None or rel.statement in _SIGNS_NEVER_IN_DOUBT:
        return True
    # A STATEMENT'S OWN TOTALS (`*_top_level`) are no section's costs: the cash at the end of the
    # year is the roll-forward's result, not a figure a filing prints unsigned or in parentheses by
    # choice. Its relation is established by the sections its components sit in.
    sections = {s for s in (section_of.get(k, "") for k in (rel.target, *rel.components))
                if not s.endswith("top_level")}
    if sections and sections <= one_signed:
        return True
    return _signed_by_convention(rel, costs)


def _signed_by_convention(rel: "Relation", costs: frozenset[str]) -> bool:
    """Whether the template's `cost_magnitude_children` make this sum sign-safe on any filing.

    A magnitude component is spent as `-|figure|`, so its printed sign does not matter. That settles
    the relation when every cost among its components is one, and the TARGET is not a cost: a
    total of costs is printed negative by one filing and unsigned by the next, and the magnitudes do
    not decide which. `is_pl__gross_profit` qualifies (its one cost, total cost of sales, is a
    magnitude); `is_pl__total_cost_of_sales` does not (it is itself a cost).
    """
    if not rel.magnitudes or rel.target in costs:
        return False
    return all(c in rel.magnitudes for c in rel.components if c in costs)


def declared_costs(template: TemplateDefinition, ontology=None) -> frozenset[str]:
    """The concepts the configuration itself calls costs.

    Two declarations name them: the template's `cost_magnitude_children` (every concept a rollup
    spends as a cost, on any rollup) and the rulebook's `negative_expected` sign convention. Nothing
    is inferred from a caption.
    """
    out: set[str] = set()
    key_of = {n.node_id: n.canonical_key for n in template.all_nodes()}
    for node in template.all_nodes():
        rollup = getattr(node, "rollup", None)
        for child in (getattr(rollup, "cost_magnitude_children", None) or ()):
            key = key_of.get(child) or child
            if key:
                out.add(key)
    for m in (getattr(ontology, "mappings", None) or ()):
        if str(getattr(m, "sign_convention", "") or "") == "negative_expected":
            out.add(str(getattr(m, "canonical_key", "") or ""))
    out.discard("")
    return frozenset(out)


# The mapping methods that place a row in a section without a concept claiming it: the sweep and
# the gap-closer that routes leftovers into a section's Others.
_NOT_EVIDENCE_METHODS = ("residual", *DERIVED_METHODS)


def _signed_on_filing(items: Iterable[LineItem], section_of: dict[str, str],
                      costs: frozenset[str]) -> frozenset[str]:
    """The sections whose signs THIS FILING establishes: its costs are printed negative.

    A section qualifies when a figure printed on the face in it is negative — a cost in
    parentheses, an outflow with a minus, or a line `normalize` oriented on load (a "Less:" caption,
    a CAS outflow block) — and none of its declared costs (`declared_costs`) is printed positive.
    The second half is what keeps a CAS income statement out: it prints credit-impairment losses
    with a minus and its cost of sales unsigned, so one negative is not evidence that the costs are
    signed.

    Read across both columns and both bases at once, because printing costs in parentheses is a
    choice a filing makes for a statement, not for a column (the same reading
    `normalize._negate_unsigned_expenses` makes). Face rows only: a note prints its own detail its
    own way, and a figure this pipeline computed was not printed at all.

    AND ONLY ROWS A CONCEPT CLAIMED. A row the residual stage swept into a section's Others line (or
    a model routed there) sits in that section by inference, so its sign says nothing about how the
    filing prints the section. Measured on a stored CAS run whose cash-flow outflows were never
    oriented: a swept investing subtotal printed "-63,077,961.11" was the only negative in the
    operating section, and counting it reported the operating total out by 7.6bn.
    """
    from app.core.models.enums import PrintedIn

    negative: set[str] = set()
    unsigned_cost: set[str] = set()
    for li in items:
        key = li.canonical_key or ""
        section = section_of.get(key, "")
        if not section or li.is_computed or li.printed_in == PrintedIn.NOTES:
            continue
        if (li.confidence.method or "") in _NOT_EVIDENCE_METHODS:
            continue
        for ev in li.values.values():
            figure = _printed(ev)
            if figure is None or figure == 0:
                continue
            if figure < 0:
                negative.add(section)
            elif key in costs:
                unsigned_cost.add(section)
    return frozenset(negative - unsigned_cost)


def _rollup_reach(template: TemplateDefinition) -> dict[str, frozenset[str]]:
    """Every key a subtotal's rollup pulls in, transitively — what it "reaches".

    Used to tell a SECTION's own subtotal from a STATEMENT total nested among its children: the
    right one reaches the section and (almost) nothing else. See `section_relations`.
    """
    kids: dict[str, tuple[str, ...]] = {}
    for st in template.statements:
        for node in template._walk(st.sections):
            key = node.canonical_key
            rollup = getattr(node, "rollup", None)
            if key and rollup is not None:
                kids[key] = tuple(getattr(rollup, "children", None) or ())

    def walk(key: str, seen: frozenset[str]) -> frozenset[str]:
        if key not in kids or key in seen:
            return frozenset({key})
        out: frozenset[str] = frozenset()
        for child in kids[key]:
            out |= walk(child, seen | {key})
        return out

    return {key: walk(key, frozenset()) for key in kids}


def _one_signed_sections(ontology) -> frozenset[str] | None:
    """The sections whose members the rulebook says are all positive magnitudes.

    ``None`` when the rulebook declares NO section conventions at all, which is not the same
    answer as "declared, and none of them one-signed". A caller validating a spread against a
    template alone passes no rulebook, and a rulebook silent on sections has told the gate nothing
    — both leave the template's own arithmetic standing. A rulebook that declares every section
    ``either`` HAS spoken, and its plain sums stay unasserted.

    `section_defaults[...].sign_convention` is the declaration: `positive_expected` on the four
    asset and liability sections, `either` everywhere else. Only the first kind can be reconciled
    by a flat signed sum against its printed subtotal — see `section_relations`.

    READ FROM THE SECTION'S OWN DECLARATION, not from unanimity across its concepts. A section
    declared `positive_expected` still holds a few `either` members — the contra-asset allowances,
    6 of bs_ca's 52 — and those carry their own sign and add correctly; the declaration is about
    the section's presentation, which is the question. Testing unanimity instead disqualified
    bs_ca and bs_nca, whose checks are the sound ones (bs_nca ties to 2,486 on 49,227,234).
    """
    defaults = getattr(ontology, "section_defaults", None) or {}
    if not defaults:
        return None
    out = set()
    for section, spec in defaults.items():
        want = (spec.get("sign_convention") if isinstance(spec, dict)
                else getattr(spec, "sign_convention", None))
        if str(want or "") == "positive_expected":
            out.add(str(section))
    return frozenset(out)


def section_relations(template: TemplateDefinition, ontology,
                      signed_on_filing: frozenset[str] = frozenset(),
                      section_of: dict[str, str] | None = None) -> list[Relation]:
    """One reconciliation per template section, when the rulebook asks for it.

    ``signed_on_filing`` names the sections whose costs this filing prints negative
    (``_signed_on_filing``), keyed like ``section_of`` (concept -> ``section_scope``). A netting
    section whose every key sits in one of them is reconciled; see the sign test below.

    The rulebook states the rule in prose and states its consequence in the same sentence, so both
    are read from it: the check exists while the sentence does, it emits the fact the sentence
    names (``unallocated_gap``), and it blocks what the sentence says it blocks. Empty the field
    and no section reconciliation is emitted at all — which is the honest behaviour, since nothing
    else in the pipeline claims a section is complete.

    The arithmetic is ``residual_framework.reconciliation.identity``: reported subtotal minus every
    concept printed in the section (its residual bucket included, holding the swept rows) is zero.
    Absent children are taken as nil for the same reason the residual-bucket rollups take them as
    nil — the sweep gives every printed row a home, so a still-absent child is a row the filing
    does not print.
    """
    # ACCEPTS EITHER SHAPE — see `_validation_of`.
    rules = _validation_of(ontology)
    framework = getattr(ontology, "residual_framework", None)
    recon = getattr(framework, "reconciliation", None)
    text = ((rules.section_reconciliation if rules else "") or "").strip()
    if not text and rules is None:
        # …OR THE FRAMEWORK'S OWN RECONCILIATION BLOCK, which is where this rulebook states it.
        #
        # ONLY WHEN THERE IS NO VALIDATION OBJECT AT ALL. A master that exists and leaves the
        # field EMPTY has switched the check off deliberately, and
        # `test_emptying_the_section_reconciliation_rule_removes_the_check` holds that: the
        # sentence is a kill switch and stays one. What this reaches is the other case — no master
        # in the picture, which is every run.
        #
        # WHY BOTH, and it is the same argument `_validation_of` makes about the identities: where
        # a rule is STORED is a packaging decision this evaluator has no reason to care about. The
        # validation master is one place; `residual_framework.reconciliation` is the other, and it
        # is the one that reaches a run — `api/routes/extractions` hands the stage
        # `build_working_view(line_item_set)`, which carries `residual_framework` and no
        # `validation`. So on the configuration that drives the product the sentence was read from
        # an artefact nothing loads, and `output_csv_hk_validation.json` ships it empty
        # (`section_reconciliation: ""`, 0 identities, 0 guards) besides.
        #
        # MEASURED: not one section_reconciliation relation was emitted on any of the five filings
        # in the corpus. Every structural result was a template `rollup`, so the check designed for
        # exactly this — "a section that owns a residual bucket has a home for every printed row,
        # which is what makes the section subtotals checkable at all" — never ran, and China SCE
        # reported `structural:alarm code=UNVALIDATED statement=balance_sheet skipped=32`.
        #
        # The framework says the same three things the sentence does: `identity` is the arithmetic
        # this function implements, `on_failure` names the fact to emit and the consequence, and
        # `tolerance` is read below either way.
        text = " ".join(t for t in ((getattr(recon, "identity", "") or ""),
                                    (getattr(recon, "on_failure", "") or "")) if t).strip()
    if not text:
        return []
    lowered = text.lower()
    # "blocks auto-approval" is the master's wording and "route the section to review" is the
    # framework's; they are one consequence, so either reads as blocking.
    blocks = ("auto-approval" in lowered or "auto approval" in lowered
              or "to review" in lowered)
    emits = "unallocated_gap" if "unallocated_gap" in text else ""
    tol_text = ((recon.tolerance if recon else "") or "").lower()
    per_row = "per contributing row" in tol_text

    subtotal_roles = (LineRole.SUBTOTAL, LineRole.TOTAL)
    reach = _rollup_reach(template)
    one_signed = _one_signed_sections(ontology)
    out: list[Relation] = []
    for st in template.statements:
        for section in st.sections:
            kids = [c for c in section.children if c.canonical_key]
            if not kids:
                continue                      # a statement-level total, not a section
            candidates = [c.canonical_key for c in kids if c.role in subtotal_roles]
            members = tuple(c.canonical_key for c in kids
                            if c.canonical_key not in candidates
                            and c.role not in subtotal_roles
                            and c.role != LineRole.HEADER)
            if not members:
                continue
            # THE SUBTOTAL THAT TOTALS THIS SECTION, which is not simply the last one printed in
            # it. A STATEMENT total can be nested among a section's children — the template prints
            # `bs_ca__total_assets` inside the current-assets section and
            # `bs_cl__total_equity_and_liabilities` inside current liabilities — and the positional
            # rule took those, so the check compared the 42 current-asset members against TOTAL
            # ASSETS. Measured on 000709: it reported current assets as 269,103,744,306.53 printed
            # against 44,039,467,051.23 extracted, an 84% shortfall that was really the
            # non-current side of the balance sheet missing from the sum.
            #
            # Chosen by REACH — what the candidate's own rollup pulls in, transitively — and
            # COVERAGE FIRST: the most of THIS section's members, then the least from outside it.
            # `bs_ca__total_current_assets` covers 42 of 42; `bs_ca__total_assets` covers them too
            # but drags in 48 keys that are not in the section, so coverage ties and reach decides.
            #
            # Both halves are load-bearing. Minimising reach ALONE picks the tightest rollup in
            # the section rather than the one that totals it — on bs_ca that is
            # `bs_ca__net_trade_receivables`, which strays nowhere and covers two of the 42.
            #
            # Ties go to the LAST in printed order, which keeps the reason the positional rule
            # existed at all: the operating cash-flow section prints two subtotals ("cash generated
            # from operations", then "net cash from operating activities") and only the closing one
            # is the figure every row in the section feeds. Reconciling to the first would report a
            # phantom gap equal to interest and tax paid.
            inside = set(members)
            subtotal = ""
            best: tuple[int, int] | None = None
            for cand in candidates:
                pulled = reach.get(cand, frozenset({cand}))
                rank = (len(pulled & inside), -len(pulled - inside))
                if best is None or rank >= best:
                    best, subtotal = rank, cand
            members = tuple(m for m in members if m != subtotal)
            if not members:
                continue
            extra = {"section": section.node_id, "blocks_auto_approval": blocks, "emits": emits}
            # AND ONLY WHERE THE MEMBERS ARE ALL ADDITIVE WITH ONE SIGN, which the rulebook
            # declares per section. "reported subtotal − Σ(members) = 0" is an identity when every
            # member is a positive magnitude, as the four asset and liability sections declare
            # (`sign_convention: positive_expected`). It is not one where the section nets: the
            # profit-and-loss section holds revenue AND costs, the investing section holds
            # purchases AND proceeds, and the rulebook says so by declaring those `either`
            # ("retain the reported sign; never coerce").
            #
            # WHAT IT COST TO ASSERT IT ANYWAY. `global_rules.sign_convention.expenses_and_outflows`
            # says expenses are "Stored NEGATIVE ... required by the template", and
            # `normalize._negate_unsigned_expenses` implements the negation — but its cohort is
            # concepts declaring `negative_expected`, and the output-CSV rulebook declares that on
            # NONE of its 462 (the hkfrs rulebook it was projected from declares 20). So a CAS
            # filing's unsigned costs are never oriented: on 000709 `is_pl__cost_of_sales` arrives
            # +109,877,789,991.32 beside revenue +116,590,734,451.51, and this relation reported
            # the section short by 361,237,582,413.82 against a printed profit of 800,840,003.25 —
            # 45,107% of it. An unusable number for a real defect, which is now named in the skip
            # reason instead of asserted as a break.
            # …OR WHERE THE FILING SIGNS THEM. A section whose costs are printed in parentheses
            # adds up as printed, so the identity holds there whatever the rulebook declares.
            scopes = {(section_of or {}).get(k, "") for k in (subtotal, *members)}
            evidenced = bool(scopes) and scopes <= signed_on_filing
            if (st.type.value not in _SIGNS_NEVER_IN_DOUBT and one_signed is not None
                    and section.node_id not in one_signed and not evidenced):
                out.append(Relation(
                    id=f"section_reconciliation:{section.node_id}", kind="section_reconciliation",
                    statement=st.type.value, target=subtotal, components=members, op="sum",
                    severity="blocking", tol_per_row=per_row, note=text, extra=extra,
                    broken="section_signs_not_declared_one_way"))
                continue
            out.append(Relation(
                id=f"section_reconciliation:{section.node_id}", kind="section_reconciliation",
                statement=st.type.value, target=subtotal, components=members, op="sum",
                severity="blocking", tol_per_row=per_row, note=text, extra=extra,
                # A section the template gives no subtotal node can never be reconciled; the
                # rulebook says so itself ("flagged unreconciled"), so it is reported as that
                # rather than as an extraction gap.
                broken="" if subtotal else "no_reported_subtotal",
            ))
    return out


# --- validation.cross_concept_guards -----------------------------------------------------------
# Each guard is a sentence. The predicate is selected by the wording, checked in this order (the
# first phrase that appears wins), and the operands are the canonical keys the sentence names —
# so editing a key in the rulebook changes what is compared, and deleting the sentence removes
# the check. A sentence matching no phrase is reported as ``guard_unrecognised``: the alternative
# is a rulebook stating a guard that quietly does nothing.
#
# The third column is the precondition, and ``"always"`` is a promise ``_guard_slot`` has to keep:
# :mod:`app.services.coverage` calls a skip from an unconditional guard a defect in this module, so
# a guard listed "always" that CAN legitimately have nothing to say would make that alarm cry wolf
# on ordinary filings — and an alarm that fires on healthy runs is one nobody reads. Only
# ``mutually_exclusive`` answers on every column; the other two each need something of the filing
# and say so, which is why their skips land in the recoverable bucket instead.
_GUARD_PHRASES: tuple[tuple[str, str, str], ...] = (
    ("sign_convention", "sign_expectation", "a concept with a declared sign convention extracted"),
    ("resolved as consolidated", "consolidation_eliminated", "a consolidated column"),
    ("populated together with", "mutually_exclusive", "always"),
    # "A equal to B while C is non-zero" — three operands, and C decides whether A == B is even
    # a finding, so the guard has something to say only once C is extracted.
    ("non-zero", "equal_while_third_non_zero", "all three concepts extracted"),
    ("equal to", "equal_values", "both concepts extracted and non-zero"),
)
_GUARD_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")


def _unique(gid: str, taken: dict[str, int]) -> str:
    """``gid``, suffixed only when this rulebook already declared a rule under it.

    Used for both families that can collide: guards here, and the declared relations in
    ``evaluate_structure`` (an authored identity id is free text, so two entries can repeat one).

    TWO GUARD SENTENCES MUST NOT SHARE AN ID. The id is built from the predicate and the first
    concept the sentence names, so two ``validation.cross_concept_guards`` sentences that share a
    predicate and a first key — legal, and ontologies are admin-uploadable — used to collapse into
    one identity. Everything downstream keys on it: the review card's DOM id
    (``chk-guard-{rule_id}-{scope_key}``, which the client also uses as its expand key), the
    coverage report's per-rule alarms, and ``report.unenforceable()``. One id over two guards means
    one card for two different assertions, and a reviewer's verdict landing on a sentence they
    never read.

    The judgement layer does NOT rely on this: a guard's subject carries the sentence and the
    operands it names, so identity holds even against a rulebook that repeats an id (see
    api/routes/documents.py::_guard_check). Both are wanted. Uniqueness here is what the SCREEN
    and the coverage report need — they have only the id — while the subject is what a stored human
    judgement is pinned to, and that must not depend on a rulebook author getting ids right.

    The suffix is the sentence's 1-based ordinal among those sharing the base id, so it is
    deterministic for a given rulebook: reading the same definition twice yields the same ids.
    Editing the rulebook can move it, which is the same acceptance-withdrawing event as editing
    the sentence itself.
    """
    taken[gid] = taken.get(gid, 0) + 1
    return gid if taken[gid] == 1 else f"{gid}#{taken[gid]}"


def cross_concept_guards(ontology) -> list[Guard]:
    """The rulebook's ``validation.cross_concept_guards``, resolved into evaluable guards.

    These catch what no arithmetic can. A balance sheet whose ``investments_in_subsidiaries`` is
    populated on a consolidated column still ties perfectly — consolidation eliminates that line,
    so its presence does not break a subtotal, it means the column was read from the company-only
    statement. Same for an aggregate loaded alongside the components it contains: the total is
    right, the equity is double-counted.

    Every guard leaves here with an id no other guard in the same rulebook carries — see
    ``_unique``.
    """
    # ACCEPTS EITHER SHAPE — see `_validation_of`.
    rules = _validation_of(ontology)
    if rules is None:
        return []
    concept_keys = {m.canonical_key for m in getattr(ontology, "mappings", [])}
    groups = {g.aggregate: tuple(g.components)
              for g in getattr(getattr(ontology, "global_rules", None),
                               "mutually_exclusive_groups", [])}

    out: list[Guard] = []
    taken: dict[str, int] = {}
    for text in rules.cross_concept_guards:
        lowered = text.lower()
        predicate = precondition = ""
        for phrase, name, pre in _GUARD_PHRASES:
            if phrase in lowered:
                predicate, precondition = name, pre
                break
        keys = tuple(dict.fromkeys(k for k in _GUARD_KEY_RE.findall(text) if k in concept_keys))
        if not predicate:
            out.append(Guard(id=f"guard:unrecognised:{len(out)}", predicate="", keys=keys,
                             statement="cross_statement", precondition="always", text=text,
                             broken="guard_unrecognised"))
            continue
        gid = _unique(f"guard:{predicate}" + (f":{keys[0]}" if keys else ""), taken)
        # "…together with its listed components" names the aggregate and points at the list, which
        # lives in ``global_rules.mutually_exclusive_groups``. Editing that list changes what the
        # guard compares; an aggregate with no group is an authoring gap, reported as one.
        if predicate == "mutually_exclusive":
            components = groups.get(keys[0] if keys else "", ())
            named = tuple(k for k in keys[1:] if k not in components)
            keys = (keys[0], *components, *named) if keys else ()
            if not keys or len(keys) < 2:
                out.append(Guard(id=gid, predicate=predicate, keys=keys,
                                 statement="cross_statement", precondition=precondition,
                                 text=text, broken="guard_components_unknown",
                                 broken_detail={"aggregate": keys[0] if keys else ""}))
                continue
        needed = {"equal_while_third_non_zero": 3, "equal_values": 2,
                  "consolidation_eliminated": 1}.get(predicate, 0)
        if len(keys) < needed:
            out.append(Guard(id=gid, predicate=predicate, keys=keys, statement="cross_statement",
                             precondition=precondition, text=text,
                             broken="guard_components_unknown",
                             broken_detail={"named": list(keys), "needs": needed}))
            continue
        prefixes = {k.split("_", 1)[0] for k in keys}
        statement = {"bs": "balance_sheet", "pl": "profit_and_loss", "cf": "cash_flow"}.get(
            next(iter(prefixes)), "cross_statement") if len(prefixes) == 1 else "cross_statement"
        out.append(Guard(id=gid, predicate=predicate, keys=keys, statement=statement,
                         precondition=precondition, text=text))
    return out


# ``weighted_sum`` is no longer expressible in the template schema (see ``schemas.template``), but
# a definition stored before that change still validates loosely enough to reach here, and an op
# this module cannot evaluate must never be treated as a plain sum — that would report a subtotal
# as tying against arithmetic nobody declared.
SUPPORTED_OPS = ("sum", "diff", "signed_sum")


def _contributions(rel: Relation, parts: dict[str, Decimal],
                   signed: frozenset[str] | set[str] = frozenset()) -> dict[str, Decimal]:
    """Each component's SIGNED contribution to the expected figure.

    Kept separate from the raw values because the sign-suspect hypothesis needs it: flipping a
    term that already enters negatively moves the total the other way, and diagnosing the wrong
    line is worse than diagnosing none.
    """
    keys = list(parts)
    if rel.signs:
        out = {k: Decimal(s) * parts[k] for k, s in zip(keys, rel.signs)}
    elif rel.op == "diff":
        out = {k: (parts[k] if i == 0 else -parts[k]) for i, k in enumerate(keys)}
    else:
        out = dict(parts)
    # A magnitude component is spent, whatever sign it arrived with and wherever the expression
    # puts it: the absolute amount first, then the deduction. See ``Relation.magnitudes``.
    # A component the filing PRINTS signed (``MappedValues.signed``) keeps its printed sign.
    return {k: (-abs(v) if k in rel.magnitudes and k not in signed else v)
            for k, v in out.items()}


def _sign_suspect(target: str, actual: Decimal, expected: Decimal,
                  contributions: dict[str, Decimal], tol: Decimal,
                  magnitudes: frozenset[str] = frozenset()) -> str | None:
    """The one participant whose sign, flipped, would satisfy the relation — or None.

    Only an unambiguous single candidate is named: with two equal-magnitude candidates the
    diagnosis would be a coin toss, and a wrong pointer is worse than none.
    """
    candidates = []
    if abs(-actual - expected) <= tol:
        candidates.append(target)
    for key, contribution in contributions.items():
        # A magnitude component cannot BE the sign suspect: its sign was taken out of the
        # arithmetic, so flipping it on the page moves the total not at all. Naming it would
        # send the analyst to the one line whose sign the relation does not depend on.
        if key in magnitudes:
            continue
        if contribution and abs(actual - (expected - 2 * contribution)) <= tol:
            candidates.append(key)
    return candidates[0] if len(candidates) == 1 else None


def _scope(slot: Slot) -> str:
    return f"{slot[0]}/{slot[1] or '—'}"


def _residual_namespaces(template: TemplateDefinition, ontology=None) -> set[str]:
    """The key namespaces a residual bucket sweeps, from the rulebook's own declaration.

    A RESIDUAL IS A DECLARATION, NOT A SPELLING, and reading it as a spelling is what left the
    whole structural layer unenforceable on the shipped configuration. The test was
    ``k.endswith("__others")`` — the hkfrs rulebook's convention — and the output-CSV rulebook that
    drives the product names its eleven residuals after what they hold:
    ``bs_ca__other_current_assets``, ``bs_equity__other_reserves``,
    ``cf_financing__other_financing_cash_flows``. Not one ends in ``__others``, so ``swept`` came
    back EMPTY, every absent component counted as unknown rather than nil, and every section
    rollup was skipped.

    MEASURED, before this read the declaration: of 61 balance-sheet-and-onwards checks on China
    SCE, 34 were skipped ``target_not_extracted`` and 27 ``components_not_mapped``, with
    ``structural:alarm code=UNVALIDATED statement=balance_sheet skipped=32`` — none of the section
    subtotals was checked on any of the five filings in the corpus. That is how a related-party
    receivable republished into the wrong section (see ``sub__rp_find_1``) could overstate total
    non-current assets by 3,339,974 with nothing reporting it.

    ``value_scope: exclusive_residual`` is the field ``stages/residual`` itself reads to know which
    concept a section's unclaimed rows are swept into, so it is the same answer by construction.
    The ``__others`` convention is kept beside it: a rulebook using that spelling and not declaring
    the field keeps working, and one declaring both gets the same namespace twice.
    """
    swept = {k.rsplit("__", 1)[0] for k in template.all_canonical_keys()
             if k.endswith("__others")}
    for m in (getattr(ontology, "mappings", None) or ()):
        key = str(getattr(m, "canonical_key", "") or "")
        if getattr(m, "value_scope", None) == "exclusive_residual" and "__" in key:
            swept.add(key.rsplit("__", 1)[0])
    return swept


def _nil_when_absent(template: TemplateDefinition, ontology=None) -> dict[str, str]:
    """Concepts whose absence from a filing means NIL, not unknown — mapped to their statement.

    A relation is normally skipped when any component was not extracted, because a figure nobody
    read is not a figure worth asserting against. The exception the module already made is the
    section subtotal: a section that owns a residual bucket has a home for every printed row, so a
    concept in it that is STILL absent is one the filing does not print.

    That reasoning is a property of the residual's REACH, not of one rollup's child list — which is
    all the original test (``any(c.endswith("__others"))``) could see. It is generalised here because
    the revised statements strike several relations over leaves the residual covers without naming
    the residual: ``pl_gross_profit`` is struck from revenue and the two cost-of-sales leaves the
    expenses residual covers, and the cash flow's two intermediate operating subtotals are the same
    shape. Without this, the most-used check on the income statement — gross profit against revenue
    and cost of sales — would be permanently skipped on every filing that does not print a purchases
    of stock-in-trade line, which is most of them.

    THE REACH IS THE KEY NAMESPACE, not the template's section node. ``stages/residual`` sweeps
    every row it places in a residual, so a namespace that HAS one has a home for all of it. The
    template may present one namespace as two sections — the revision prints ``pl_expenses__*`` as
    both Cost of sales and Operating expenses — and a rule keyed on the presentation would call a
    leaf unknown that the sweep in fact covers. Which namespaces those are is
    :func:`_residual_namespaces`, which reads the rulebook's declaration rather than a spelling.

    TWO EXCLUSIONS, both of which cause a FALSE FAILURE rather than a missed one:

    * A CALCULATED node never qualifies. Its absence means the filing printed no subtotal there,
      and its figure is computable, so taking it as nil would compare a reported total against a
      knowingly incomplete sum. ``pl_operating_profit_ebit = total_income + total_operating_cost``
      would then fail by the whole operating cost on any filing that prints no operating-cost
      subtotal — and ``total_operating_cost`` sits in the very namespace the expenses residual
      sweeps, so nothing but this exclusion keeps it out.
    * The caller applies this only to components on the relation's own statement. A cross-statement
      tie compares two independently extracted statements, and an operand missing there says the
      other statement is incomplete — ``cf_to_bs_cash`` would otherwise assert closing cash equals
      zero on a filing whose balance-sheet cash line was not found.
    """
    swept = _residual_namespaces(template, ontology)
    out: dict[str, str] = {}
    for st in template.statements:
        for node in template._walk(st.sections):
            key = node.canonical_key
            if key and not node.rollup and "__" in key and key.rsplit("__", 1)[0] in swept:
                out[key] = st.type.value
    return out


def evaluate_structure(template: TemplateDefinition,
                       items: Iterable[LineItem],
                       ontology=None, not_read: Iterable[str] = ()) -> StructuralReport:
    """Check every declared relation whose participants were all extracted, per (basis, period).

    ``ontology`` is optional and additive: without it only the template's own relations are
    evaluated, which is what a caller validating a spread against a template alone wants. With it,
    the rulebook's identities, cross-concept guards and section reconciliation are evaluated too.

    ``not_read`` names the statements whose pages this run did not read
    (``services.unread_pages.statements_not_read``). A relation on such a statement with nothing
    extracted is skipped as ``statement_not_read``, not ``statement_absent``: the filing may well
    print the statement, and only the second reason leaves the coverage denominator.
    """
    items = list(items)
    vals = collect_values(items)
    report = StructuralReport()
    stmt_of = _statement_index(template)
    nil_absent = _nil_when_absent(template, ontology)
    # Which sections the rulebook declares one-signed, and which section each key belongs to —
    # read by the sign gate below. See `_one_signed_sections` and `_signs_established`.
    signed_sections = _one_signed_sections(ontology)
    section_of = {str(getattr(m, "canonical_key", "")):
                  (tuple(getattr(m, "section_scope", None) or ("",)) or ("",))[0]
                  for m in (getattr(ontology, "mappings", None) or ())}
    # …and which sections THIS FILING signs, by printing their costs negative. Evidence, read off
    # the face rows; it widens the declared set and never narrows it.
    costs = declared_costs(template, ontology)
    signed_here = _signed_on_filing(items, section_of, costs)
    established = (None if signed_sections is None
                   else frozenset(signed_sections | signed_here))
    # A statement no key was extracted for is not thin coverage, it is a statement this filing
    # does not contain (a standalone-only filing has no cash flow). Its relations are still
    # reported — silence would be indistinguishable from a pass — but they must not sit in the
    # denominator of a coverage rate, so they are marked apart from every other skip.
    present = {stmt_of[k] for k in vals.values if k in stmt_of}
    unread = set(not_read or ())

    declared = relations(template)
    if ontology is not None:
        declared += ontology_identities(template, ontology)
        declared += section_relations(template, ontology, signed_on_filing=signed_here,
                                      section_of=section_of)
    # TWO RELATIONS MUST NOT SHARE AN ID, for the same reason two guards must not (see ``_unique``):
    # the review card's DOM id is ``chk-structural-{rule_id}-{scope_key}`` and the coverage report
    # keys its per-rule alarms on the id, so one id over two relations is one card for two
    # assertions. An authored ``validation.identities``/``identities`` id is a free-text field, so
    # two entries can share one — and then run 1 failing entry A and run 2 failing entry B served B
    # under A's reviewer and A's reason. The judgement layer does not rely on this either: a
    # relation's subject carries its operands (api/routes/documents.py::_structural_checks).
    taken: dict[str, int] = {}
    declared = [replace(rel, id=_unique(rel.id, taken)) for rel in declared]

    for rel in declared:
        keys = (rel.target, *rel.components)
        if rel.broken:
            # A SECTION WITH NO PRINTED SUBTOTAL SAYS SO FIRST. The rulebook names that case
            # itself — "itemised, flagged unreconciled, never a gap invented against a subtotal
            # that was not printed" — and it is a fact about the FILING, while everything a
            # relation can be `broken` for is a fact about the RULE. Whether the section's signs
            # are declared is moot when there is nothing to reconcile against, and reporting the
            # premise instead told a reader to go fix a declaration that would change nothing.
            reason = ("no_reported_subtotal"
                      if rel.kind == "section_reconciliation" and not vals.slots(rel.target)
                      else rel.broken)
            # …except on a statement whose pages were not read: whether it prints the subtotal is
            # exactly what nobody looked at.
            if reason == "no_reported_subtotal" and rel.statement in unread \
                    and rel.statement not in present:
                reason = "statement_not_read"
            detail = {} if reason != rel.broken else dict(rel.broken_detail)
            report.results.append(_skip(rel, [], reason, detail))
            continue
        if rel.statement and rel.statement not in present:
            report.results.append(_skip(rel, [], ("statement_not_read" if rel.statement in unread
                                                  else "statement_absent"), {}))
            continue
        if rel.op not in SUPPORTED_OPS:
            report.results.append(_skip(rel, [], "unsupported_op", {"op": rel.op}))
            continue
        # A SIGNED SUM NEEDS THE SIGNS, and on a statement that nets they are not established.
        #
        # `global_rules.sign_convention.expenses_and_outflows` says expenses are "Stored NEGATIVE
        # ... required by the template", and `normalize._negate_unsigned_expenses` implements the
        # negation for a filing that prints them unsigned — but its cohort is concepts declaring
        # `negative_expected`, and the output-CSV rulebook declares that on NONE of its 462 (the
        # hkfrs rulebook it was projected from declares 20). So a CAS filing's costs are never
        # oriented: on 000709 `is_pl__cost_of_sales` arrives +109,877,789,991.32 beside revenue
        # +116,590,734,451.51.
        #
        # A plain SUM over such components is then not the template's arithmetic, and asserting it
        # reported breaks of 9,098% and 4,396% — an unusable number for a real defect. The premise
        # is reported instead, naming what is unestablished, so the coverage layer counts it as
        # unverified rather than as a filing that fails its own arithmetic.
        #
        # ESTABLISHED BY EVIDENCE, not by the section's `either` alone (see `_signs_established`): a
        # filing that prints its costs in parentheses has signed them, and a rollup that spends its
        # costs as magnitudes needs no signs. An HKEX income statement and an Ind AS cash flow are
        # checked; a CAS income statement, whose cost of sales is printed unsigned, still is not.
        #
        # THE BALANCE SHEET IS EXEMPT BY CONSTRUCTION, not by what its sections happen to declare
        # — see `_SIGNS_NEVER_IN_DOUBT`. A balance sheet does not net, so nothing on it is at risk
        # from an unoriented cost, and those are exactly the relations that tie: all eight sections
        # on China SCE, and 8 of 12 relations on 000709 where 4 were passing before.
        if (rel.kind == "rollup" and rel.op == "sum" and not rel.signs
                and not _signs_established(rel, section_of, established, costs)):
            report.results.append(_skip(rel, [], "component_signs_not_established", {
                "sections": sorted({section_of.get(k, "?") for k in keys})}))
            continue
        # A participant whose figure was derived to close this very gap makes the relation
        # incapable of failing. That is a property of the rule, not of coverage, so it is decided
        # before anything about what was extracted.
        circular = sorted(k for k in keys if k in vals.derived)
        if circular:
            report.results.append(_skip(rel, [], "derived_input", {
                "derived": circular, "methods": sorted({vals.derived[k] for k in circular})}))
            continue
        blocked = sorted(k for k in keys if k in vals.ambiguous)
        if blocked:
            report.results.append(_skip(rel, [], "ambiguous_mapping", {"keys": blocked}))
            continue
        target_slots = sorted(vals.slots(rel.target), key=lambda s: (s[0], s[1] or ""))
        if not target_slots:
            # For a section reconciliation the missing figure IS the subtotal, and the rulebook
            # names that case separately: the section is unreconciled, not under-extracted.
            reason = ("no_reported_subtotal" if rel.kind == "section_reconciliation"
                      else "target_not_extracted")
            report.results.append(_skip(rel, [], reason, {}))
            continue

        # A section that owns a residual bucket has a home for every printed line, so a child
        # still absent is one the filing does not print — nil, not unknown. See the module
        # docstring; this is what makes the section subtotals checkable at all.
        zero_fill = rel.kind == "section_reconciliation" or (
            rel.kind == "rollup" and rel.op == "sum"
            and any(c.endswith("__others") for c in rel.components))

        unmapped: dict[Slot, list[str]] = {}
        mixed: list[Slot] = []
        for slot in target_slots:
            missing = [c for c in rel.components if vals.get(c, slot) is None]
            # A component the template declares as a leaf of a section that owns a residual bucket
            # is nil when absent, not unknown — see ``_nil_when_absent``, including why the test is
            # restricted to this relation's own statement.
            unknown = ([] if zero_fill
                       else [c for c in missing if nil_absent.get(c) != rel.statement])
            if unknown:
                unmapped[slot] = unknown
                continue
            # Every component missing means nothing was extracted for this section at all;
            # "0 == 0" would be a vacuous pass, so it stays a skip.
            if missing and len(missing) == len(rel.components):
                unmapped[slot] = missing
                continue
            # Values are not unit-normalized, so a relation may only be evaluated where every
            # participant shares one scale — otherwise the sum compares thousands to millions.
            present_keys = [k for k in keys if vals.get(k, slot) is not None]
            if len({vals.scale(k, slot) for k in present_keys}) > 1:
                mixed.append(slot)
                continue
            report.results.append(_check(rel, slot, vals, assumed_zero=missing))

        if unmapped:
            missing_keys = sorted({k for ks in unmapped.values() for k in ks})
            report.results.append(_skip(
                rel, list(unmapped), "components_not_mapped",
                {"missing": missing_keys, "missing_count": len(missing_keys),
                 "component_count": len(rel.components)}))
        if mixed:
            report.results.append(_skip(rel, mixed, "mixed_scale", {}))

    if ontology is not None:
        report.results.extend(_evaluate_guards(cross_concept_guards(ontology), vals, ontology,
                                               present, unread))

    for res in report.failures():
        report.failed_assertions.append(_assertion(res))
    return report


def _assertion(res: RuleResult) -> str:
    d = res.details
    if res.kind == "guard":
        return (f"cross-concept guard {d.get('guard')} violated on "
                f"{', '.join(d.get('violations_keys') or [d.get('target', '')])} "
                f"({res.scope_key}): {d.get('rule_text', '')}")
    if res.kind == "section_reconciliation":
        return (f"section {d.get('section')} does not reconcile ({res.scope_key}): "
                f"reported subtotal {res.actual} vs {res.expected} accounted for, "
                f"unallocated gap {res.difference}")
    return (f"{d['target']} does not equal {d['op']} of its "
            f"{len(d['components'])} template component(s) "
            f"({res.scope_key}): expected {res.expected}, got {res.actual}")


def _check(rel: Relation, slot: Slot, vals: MappedValues,
           assumed_zero: Sequence[str] = ()) -> RuleResult:
    """Evaluate one relation for one (basis, period).

    ``assumed_zero`` names components the filing does not print, taken as nil. They are
    recorded on the result so a reader can see the relation was checked against a section the
    filing states partially — a pass is a genuine pass, but not evidence every line was found.
    """
    zeroed = set(assumed_zero)
    parts = {c: (Decimal(0) if c in zeroed or (c, slot) in vals.carved else vals.get(c, slot))
             for c in rel.components}
    contributions = _contributions(rel, parts,
                                   {c for c in rel.components if (c, slot) in vals.signed})
    actual = vals.get(rel.target, slot)
    expected = sum(contributions.values(), Decimal(0))
    diff = actual - expected
    tol_abs = rel.tol_abs
    if rel.tol_per_row:
        # "one rounding unit per contributing row": a section spread over 20 printed rows may be
        # a rounding unit out on each of them without anything being wrong.
        rows = sum(vals.sources(k, slot) for k in (rel.target, *rel.components))
        tol_abs = Decimal(max(rows, 1))
    tol = tolerance(expected, tol_abs, rel.tol_rel)
    ok = abs(diff) <= tol
    return RuleResult(
        rule_id=rel.id, kind=rel.kind, scope_key=_scope(slot),
        status="pass" if ok else "fail",
        expected=expected, actual=actual, difference=diff,
        details={
            "target": rel.target, "components": list(rel.components), "op": rel.op,
            "statement": rel.statement, "basis": slot[0], "period_label": slot[1],
            "tolerance": str(tol), "severity": rel.severity,
            "component_values": {k: str(v) for k, v in parts.items()},
            "assumed_zero": sorted(zeroed),
            "sign_suspect": (None if ok else _sign_suspect(rel.target, actual, expected,
                                                           contributions, tol, rel.magnitudes)),
            **({"note": rel.note} if rel.note and rel.kind != "section_reconciliation" else {}),
            **rel.extra,
            # The rulebook names the fact a break emits; it is attached only where there is a
            # break, so a reader never sees a gap that isn't one.
            **({rel.extra["emits"]: str(diff)}
               if not ok and rel.extra.get("emits") else {}),
        },
    )


def _not_evaluable_status(reason: str) -> str:
    """``skipped`` for a fact about the filing, ``error`` for a defect in the rule as authored.

    Both are "no answer", but only one of them is recoverable, and a reader who cannot tell them
    apart has no way to notice that a rule declared BLOCKING is unenforceable. See
    ``AUTHORING_REASONS``.
    """
    return STATUS_AUTHORING_ERROR if reason in AUTHORING_REASONS else "skipped"


def _skip(rel: Relation, slots: list[Slot], reason: str, extra: dict) -> RuleResult:
    """One 'not evaluable' row per relation+reason (not per period), so a partial extraction
    reports its coverage compactly instead of flooding the report."""
    return RuleResult(
        rule_id=rel.id, kind=rel.kind,
        scope_key=(";".join(_scope(s) for s in slots) if slots else "—"),
        status=_not_evaluable_status(reason),
        details={"target": rel.target, "components": list(rel.components), "op": rel.op,
                 "statement": rel.statement, "reason": reason, "severity": rel.severity,
                 **({"authoring_defect": True} if reason in AUTHORING_REASONS else {}),
                 **rel.extra, **extra},
    )


# --- guard evaluation --------------------------------------------------------------------------

def _evaluate_guards(guards: Sequence[Guard], vals: MappedValues, ontology,
                     present: set[str], unread: set[str] | None = None) -> list[RuleResult]:
    out: list[RuleResult] = []
    expectations = {m.canonical_key: m.sign_convention
                    for m in getattr(ontology, "mappings", [])
                    if m.sign_convention in ("positive_expected", "negative_expected")}
    for guard in guards:
        if guard.broken:
            out.append(_guard_skip(guard, guard.broken, dict(guard.broken_detail)))
            continue
        if guard.statement not in ("cross_statement", "") and guard.statement not in present:
            out.append(_guard_skip(guard, ("statement_not_read" if guard.statement in (unread or ())
                                           else "statement_absent"), {}))
            continue
        results = [r for r in (_guard_slot(guard, slot, vals, expectations)
                               for slot in vals.all_slots()) if r is not None]
        if results:
            out.extend(results)
        else:
            out.append(_guard_skip(guard, "precondition_absent",
                                   {"precondition": guard.precondition}))
    return out


def _guard_slot(guard: Guard, slot: Slot, vals: MappedValues,
                expectations: dict[str, str]) -> RuleResult | None:
    """One guard for one column, or None when its precondition is not met there."""
    keys = guard.keys
    violations: list[dict] = []

    if guard.predicate == "sign_expectation":
        scanned = [k for k in expectations if vals.get(k, slot) is not None]
        if not scanned:
            return None
        for key in sorted(scanned):
            value = vals.get(key, slot)
            want = expectations[key]
            if (want == "positive_expected" and value < 0) or (
                    want == "negative_expected" and value > 0):
                violations.append({"key": key, "expected": want, "value": str(value)})
        primary = violations[0]["key"] if violations else ""
        others = [v["key"] for v in violations[1:]]
    elif guard.predicate == "consolidation_eliminated":
        if slot[0] != "consolidated":
            return None
        value = vals.get(keys[0], slot)
        if value is not None and value != 0:
            violations.append({"key": keys[0], "value": str(value), "basis": slot[0]})
        primary, others = keys[0], []
    elif guard.predicate == "mutually_exclusive":
        aggregate, components = keys[0], list(keys[1:])
        agg = vals.get(aggregate, slot)
        loaded = [c for c in components
                  if vals.get(c, slot) is not None and vals.get(c, slot) != 0]
        if agg is not None and agg != 0 and loaded:
            # Each loaded component's figure too: they are inputs of the finding as much as the
            # aggregate is, so an acceptance must move when one of them does.
            violations.append({"aggregate": aggregate, "components": loaded,
                               "aggregate_value": str(agg),
                               "component_values": {c: str(vals.get(c, slot)) for c in loaded}})
        primary, others = aggregate, loaded
    elif guard.predicate == "equal_while_third_non_zero":
        first, second, third = (vals.get(k, slot) for k in keys[:3])
        if first is None or second is None or third is None:
            return None
        if first == second and third != 0:
            violations.append({"equal": [keys[0], keys[1]], "value": str(first),
                               "non_zero": keys[2], "non_zero_value": str(third)})
        primary, others = keys[0], [keys[1], keys[2]]
    elif guard.predicate == "equal_values":
        first, second = (vals.get(k, slot) for k in keys[:2])
        if first is None or second is None or first == 0 or second == 0:
            return None
        if first == second:
            violations.append({"equal": [keys[0], keys[1]], "value": str(first)})
        primary, others = keys[0], [keys[1]]
    else:                                          # pragma: no cover — _GUARD_PHRASES is closed
        return None

    return RuleResult(
        rule_id=guard.id, kind="guard", scope_key=_scope(slot),
        status="fail" if violations else "pass",
        # ``target``/``components`` are DERIVED FROM THE VIOLATIONS for most predicates (primary is
        # violations[0]["key"] under sign_expectation, `others` is the loaded subset under
        # mutually_exclusive). They are display and diagnosis only. ``guard_keys`` is the operand
        # set the guard DECLARES — unchanged by which of them broke — and it is what a stored human
        # judgement is keyed on: a subject built from a violation-derived field changes when a
        # figure moves, so a still-failing finding gets reported as corrected. Empty for
        # sign_expectation, whose sentence names no concept and which scans every concept carrying
        # a declared sign convention.
        details={"target": primary, "components": others, "op": guard.predicate,
                 "statement": guard.statement, "basis": slot[0], "period_label": slot[1],
                 "guard": guard.predicate, "severity": guard.severity,
                 "guard_keys": list(keys),
                 "precondition": guard.precondition, "rule_text": guard.text,
                 "violations": violations,
                 "violations_keys": sorted({k for v in violations
                                            for k in _violation_keys(v)}),
                 "sign_suspect": None},
    )


def _violation_keys(violation: dict) -> list[str]:
    out = [violation[f] for f in ("key", "aggregate", "non_zero") if violation.get(f)]
    for field_name in ("components", "equal"):
        out += list(violation.get(field_name) or [])
    return out


def _guard_skip(guard: Guard, reason: str, extra: dict) -> RuleResult:
    return RuleResult(
        rule_id=guard.id, kind="guard", scope_key="—", status=_not_evaluable_status(reason),
        details={"target": guard.keys[0] if guard.keys else "", "components": list(guard.keys[1:]),
                 "op": guard.predicate, "statement": guard.statement, "reason": reason,
                 "guard": guard.predicate, "severity": guard.severity,
                 # Carried on every guard row, evaluated or not, so a reader of a skip sees which
                 # operands the sentence names without re-parsing the sentence.
                 "guard_keys": list(guard.keys),
                 **({"authoring_defect": True} if reason in AUTHORING_REASONS else {}),
                 "precondition": guard.precondition, "rule_text": guard.text, **extra},
    )
