"""How a computed figure was reached, in the shape the statement inspector already renders.

A figure assembled out of note-level datasets rather than read off a caption is not checkable on its
own, so whatever assembles it records which rule won and which note lines it consumed. This module
is the READER of that trail: it shapes a stored ``derivation`` payload for the statement inspector,
the Line Items sheet and the trail invariant. The trail used to end at the stage — only the FIRST
evidence item's page reference survived, as the value's provenance, and the rest was dropped, so a
reviewer saw a rule name and one page number but not the figures it won with.

THE WRITERS. `stages.note_sourced` writes the cascade trail, the face settlement's rebuild of it,
the note-row trail and the deterministic prose trail; `stages.line_item_llm` writes the trail of the
rows the model cited and of the sentences it quoted. Rows in ``extraction_runs.result`` from earlier
runs also carry payloads written by services that are gone, and the reader still folds those.

TWO SHAPES OF PAYLOAD.

  * LEGACY — no ``version``: method, formula, result, flags, and inputs carrying ``value``,
    ``counted`` and ``deducted``. Shown exactly as it always was and NEVER JUDGED: it does not say
    how its inputs combine, and measured on the stored Lai Sun and SunCreate runs half its slots do
    not add up by the naive counted-and-signed rule for reasons the payload cannot express.
  * VERSION 2 — ``build(..., display=...)``. Every input also records what it put into the figure
    (``used``) and why (``status``, ``reason``), and the trail records how its inputs combine
    (``op``), the divisor applied to a prose amount (``scale``), the terms the filing did not print
    (``missing``), the rungs that were passed over (``refused``), whether the model's answer was a
    whole figure or a sum of components (``combine``) and the terms the model stated that no row
    confirmed (``unlocated``). These are ADDED fields. ``value``, ``counted``, ``deducted``,
    ``label``, ``note``, ``canonical_key`` and ``provenance`` are what the engine reads — the face
    settlement, the shared-figure detector, the note pruner — and they are written exactly as
    before, so recording the arithmetic moves no figure.

`working` reads one slot's arithmetic and says whether it adds up to the figure the line shows; the
inspector, the traces, the export and `audit` all read it, so they cannot disagree.

A row's derivation is stored per ``basis:period``, because that is the granularity these figures
are computed at: Oper Exp for the current year and for the prior year are two separate cascades
that may resolve at different priorities from different notes. :func:`merge_for_basis` folds the two periods
back into one contributions list, so a reader sees each input once with both of its figures beside
it — the same shape `item_row` builds for a concept combined from several printed lines, and
therefore the same rendering, with no new UI.

Inputs are identified by (dataset, note, line item, key, position) rather than by position alone.
The current and prior cascades can draw on different numbers of notes, so zipping two lists would
pair unrelated inputs and print last year's figure against this year's caption.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# THE STATUSES THAT PUT SOMETHING INTO THE FIGURE. Everything else — the same row read twice, a
# candidate a `max` did not take, a citation that corroborates the first or disagrees with it, a term
# the filing did not print, an amount the model stated that no note contains — is shown and
# contributes nothing.
ENTERED = ("used", "deducted")
# Half a cent: every trail figure is a printed amount or a sum of them, so anything wider than this
# is a different number, not rounding.
TOL = Decimal("0.005")

# THE EXPORT'S PHRASES, keyed by their English text. The inspector builds its sentences client-side
# (so they follow the units selector) from `frontend/src/i18n/screens/trail.ts`; the Line Items
# sheet is written here, so it carries its own copy of the same wording.
_WORDS: dict[str, dict[str, str]] = {
    "{arith} = {result} — adds up.": {
        "zh": "{arith} = {result} —— 相符。", "ar": "{arith} = {result} — متطابق.",
        "fr": "{arith} = {result} — concorde."},
    "{arith} = {reached} ≠ {result} — does not add up.": {
        "zh": "{arith} = {reached} ≠ {result} —— 不相符。",
        "ar": "{arith} = {reached} ≠ {result} — غير متطابق.",
        "fr": "{arith} = {reached} ≠ {result} — ne concorde pas."},
    "{arith} = {result}; now shows {shown}.": {
        "zh": "{arith} = {result}；现列示 {shown}。",
        "ar": "{arith} = {result}؛ ويعرض البند الآن {shown}.",
        "fr": "{arith} = {result} ; la ligne affiche désormais {shown}."},
    "Entered by an analyst; the extraction reached {result}: {arith}.": {
        "zh": "由分析师录入；提取过程得出 {result}：{arith}。",
        "ar": "أدخله محلل؛ وصل الاستخراج إلى {result}: {arith}.",
        "fr": "Saisi par un analyste ; l'extraction aboutissait à {result} : {arith}."},
    "Withheld; the extraction reached {result}: {arith}.": {
        "zh": "未列示；提取过程得出 {result}：{arith}。",
        "ar": "محجوب؛ وصل الاستخراج إلى {result}: {arith}.",
        "fr": "Non publié ; l'extraction aboutissait à {result} : {arith}."},
    "How the inputs combine was not recorded.": {
        "zh": "未记录各项的组合方式。", "ar": "لم يُسجَّل كيف تتجمّع المدخلات.",
        "fr": "La façon dont les éléments se combinent n'a pas été enregistrée."},
    "Not found: {lines}.": {
        "zh": "未找到：{lines}。", "ar": "غير موجود: {lines}.", "fr": "Introuvable : {lines}."},
    "Optional parts not printed: {lines}.": {
        "zh": "未列示的可选组成部分：{lines}。", "ar": "أجزاء اختيارية لم تُطبع: {lines}.",
        "fr": "Parties facultatives non imprimées : {lines}."},
    "Rule {rung} came to {value}, below zero, and was passed over.": {
        "zh": "规则 {rung} 计算得 {value}，低于零，已跳过。",
        "ar": "بلغت القاعدة {rung} {value}، أي دون الصفر، فتم تجاوزها.",
        "fr": "La règle {rung} donnait {value}, sous zéro, et a été écartée."},
    "Rule {rung} was passed over: a deduction it names is the row its total already reads.": {
        "zh": "规则 {rung} 已跳过：其扣减项正是其合计已读取的那一行。",
        "ar": "تم تجاوز القاعدة {rung}: الخصم الذي تسمّيه هو الصف الذي يقرؤه إجماليها أصلًا.",
        "fr": "La règle {rung} a été écartée : une déduction qu'elle nomme est la ligne que son "
              "total lit déjà."},
    "Replaces the printed {figures}.": {
        "zh": "取代原文列示的 {figures}。", "ar": "يحلّ محل الرقم المطبوع {figures}.",
        "fr": "Remplace le chiffre imprimé {figures}."},
    # What each trail row did, for the sheet's How column.
    "added": {"zh": "已计入", "ar": "مضاف", "fr": "ajouté"},
    "deducted": {"zh": "已扣减", "ar": "مخصوم", "fr": "déduit"},
    "same row, added once": {"zh": "同一行，只计一次", "ar": "الصف نفسه، يُحتسب مرة واحدة",
                             "fr": "même ligne, comptée une fois"},
    "not used": {"zh": "未采用", "ar": "غير مستخدم", "fr": "non retenu"},
    "not found": {"zh": "未找到", "ar": "غير موجود", "fr": "introuvable"},
    "not used by the winning rung": {"zh": "最终采用的规则未使用此项",
                                     "ar": "لم تستخدمه القاعدة المعتمدة",
                                     "fr": "non utilisé par la règle retenue"},
    "stated by the model, not found in the note": {
        "zh": "模型给出，附注中未找到", "ar": "ذكره النموذج، وغير موجود في الإيضاح",
        "fr": "énoncé par le modèle, introuvable dans la note"},
    "the cited row was not found in the note": {
        "zh": "附注中未找到所引的行", "ar": "لم يُعثر على الصف المستشهد به في الإيضاح",
        "fr": "la ligne citée est introuvable dans la note"},
    # Why a row reads the way it does, for the trail row's comment.
    "Counted on the face in “{caption}”": {
        "zh": "已在报表正表“{caption}”中计入", "ar": "محتسب في صلب القائمة ضمن «{caption}»",
        "fr": "Compté au recto dans « {caption} »"},
    "already added as “{line}”": {"zh": "已作为“{line}”计入", "ar": "أُضيف بالفعل بوصفه «{line}»",
                                  "fr": "déjà ajouté comme « {line} »"},
    "the rule took another figure": {"zh": "规则采用了另一个数", "ar": "اعتمدت القاعدة رقمًا آخر",
                                     "fr": "la règle a retenu un autre chiffre"},
    "another row was taken": {"zh": "已采用另一行", "ar": "اعتُمد صف آخر",
                              "fr": "une autre ligne a été retenue"},
    "states the same whole figure; the first is taken": {
        "zh": "列示的是同一总数；取第一个", "ar": "يذكر الرقم الكلي نفسه؛ ويُعتمد الأول",
        "fr": "indique le même chiffre global ; le premier est retenu"},
    "states a different whole figure; the first is taken": {
        "zh": "列示的总数不同；取第一个", "ar": "يذكر رقمًا كليًا مختلفًا؛ ويُعتمد الأول",
        "fr": "indique un autre chiffre global ; le premier est retenu"},
    "prints no figure for this year": {"zh": "本年度未列示数字", "ar": "لا يورد رقمًا لهذه السنة",
                                       "fr": "n'indique aucun chiffre pour cet exercice"},
    "the rule that answers no longer uses this part": {
        "zh": "最终采用的规则不再使用这一部分", "ar": "لم تعد القاعدة المعتمدة تستخدم هذا الجزء",
        "fr": "la règle retenue n'utilise plus cette partie"},
    "{amount} of it is counted on the face": {
        "zh": "其中 {amount} 已在报表正表中计入", "ar": "منه {amount} محتسب في صلب القائمة",
        "fr": "dont {amount} est compté au recto des états"},
    "the face prints this whole line, {amount}; the parts it was built from are counted there": {
        "zh": "报表正表列示了本行全额 {amount}；其组成部分已在该处计入",
        "ar": "يطبع صلب القائمة هذا البند كاملًا، {amount}؛ والأجزاء التي بُني منها محتسبة هناك",
        "fr": "le recto imprime cette ligne entière, {amount} ; les parties dont elle est faite "
              "y sont comptées"},
    "only {amount} of it was still in the part": {
        "zh": "该部分中仅剩 {amount}", "ar": "لم يبقَ منه في الجزء سوى {amount}",
        "fr": "il n'en restait que {amount} dans la partie"},
    "refused: this line excludes derivatives": {
        "zh": "已拒绝：此项目不含衍生工具", "ar": "مرفوض: هذا البند يستثني المشتقات",
        "fr": "refusé : cette ligne exclut les dérivés"},
    "printed as {amount} in units of {from}; converted to the statement's units of {to}": {
        "zh": "附注以 {from} 为单位列示为 {amount}；已换算为报表单位 {to}",
        "ar": "مطبوع {amount} بوحدات {from}؛ وحُوِّل إلى وحدات القائمة {to}",
        "fr": "imprimé {amount} en unités de {from} ; converti dans les unités de l'état, {to}"},
    "As quoted by the model": {"zh": "模型引文", "ar": "كما اقتبسه النموذج",
                               "fr": "Cité par le modèle"},
}

_STATUS_WORDS = {"used": "added", "deducted": "deducted", "same_row": "same row, added once",
                 "not_used": "not used", "missing": "not found"}

_REASON_WORDS = {
    "not_selected": "the rule took another figure",
    "alternative": "another row was taken",
    "corroborates": "states the same whole figure; the first is taken",
    "disagrees": "states a different whole figure; the first is taken",
    "no_figure": "prints no figure for this year",
    "not_needed": "the rule that answers no longer uses this part",
    "not_located": "the cited row was not found in the note",
    "stated_not_located": "stated by the model, not found in the note",
    "refused": "refused: this line excludes derivatives",
}

# The flags that explain a slot whose figure is not the one its trail reached — see `working`.
_WITHHELD_BY = ("shared_figure_removed:", "llm_printed_row_claimed_by:", "llm_note_row_claimed_by:",
                "superseded_by_cascade:")


def _say(text: str, locale: str) -> str:
    if not locale or locale == "en":
        return text
    return _WORDS.get(text, {}).get(locale, text)


def _slot(basis: str, period_label: str) -> str:
    return f"{basis}:{period_label}"


def _identity(item: dict) -> tuple:
    """Which input of one period is which input of the other. The key and the citation's position
    belong to it as well as the caption: two cascade parts can carry one label, and a model can cite
    two rows printed with the same caption. Legacy inputs carry neither, so they pair as before."""
    at = item.get("at")
    return (item.get("dataset") or "", str(item.get("note") or ""),
            str(item.get("label") or ""), str(item.get("canonical_key") or ""),
            str(at) if at is not None else "")


def _json_safe_provenance(prov: Any) -> dict | None:
    """A provenance as a PLAIN DICT, whatever the caller handed over.

    An evidence record's ``provenance`` is routinely a ``Provenance`` pydantic MODEL rather than a
    dict, and this is the boundary where that becomes fatal. A derivation is written to a row and
    the row is written to ``extraction_runs.result``, a JSON column, so a model object reaching
    this far ends the run: the flush raises ``TypeError: Object of type Provenance is not JSON
    serializable``, SQLAlchemy rolls the transaction back, and the ``status='succeeded'`` in the
    same UPDATE is rolled back with it — a run that did all 21 stages of work is left reading
    ``running`` with a null result, for ever.

    Coerced HERE rather than at the serialization boundary because this is where the object enters
    the derivation payload, so one conversion covers every caller and every consumer downstream
    gets the same shape. ``_src_label`` below already hedged with ``isinstance(prov, dict)``,
    which is the same problem noticed and worked around instead of fixed.
    """
    if prov is None or isinstance(prov, dict):
        return prov
    dump = getattr(prov, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return None


def input_from_evidence(evidence: dict) -> dict:
    """One evidence record as an input row: what it was, where it came from, what it said."""
    return {
        "dataset": evidence.get("dataset_key"),
        "note": evidence.get("note_number"),
        "note_heading": evidence.get("note_heading"),
        "label": evidence.get("line_item"),
        "value": evidence.get("value"),
        # THE FILING'S OWN WORDS, for an input that is prose rather than a table row. A printed
        # row is traceable by note, caption and page; a figure stated in a sentence is traceable
        # only by the sentence, so the service that read it hands it over. Absent for every
        # table-row input, where the caption already is the trace.
        "excerpt": evidence.get("excerpt"),
        # Whether the rule SUBTRACTED this input. A cascade priority spelled "the wider
        # disclosure less the cost-of-sales share" consumes one of its inputs negatively, and a
        # contributions list that shows it positive does not add up to the figure above it.
        "deducted": bool(evidence.get("deducted")),
        "provenance": _json_safe_provenance(evidence.get("provenance")),
        # An evidence record the restatement control kept as corroboration rather than as an
        # addend — see services.restatement. Shown, but never presented as part of the sum.
        "counted": not (evidence.get("duplicate_of_note") or
                        evidence.get("duplicate_of_earlier_source")),
        "duplicate_of_note": evidence.get("duplicate_of_note"),
    }


def build(*, method: str, formula: str | None, inputs: list[dict], result: Any,
          flags: list[str] | None = None, display: dict | None = None) -> dict:
    """One period's derivation: the rule that produced the figure, and what it consumed.

    `display` makes it a VERSION-2 trail: ``{"op", "missing", "refused", "scale", "combine",
    "unlocated"}`` as the writer knows them, each omitted when None — see the module docstring.
    Without it the payload is the legacy shape, byte for byte.
    """
    out = {
        "method": method,
        "formula": formula,
        "result": None if result is None else str(result),
        "inputs": inputs,
        "flags": list(flags or []),
    }
    if display is not None:
        out["version"] = 2
        out.update({k: v for k, v in display.items() if v is not None})
    return out


def record(store: dict | None, *, basis: str, period_label: str, derivation: dict) -> dict:
    """`store` with this (basis, period)'s derivation added — the row's own accumulator."""
    out = dict(store or {})
    out[_slot(basis, period_label)] = derivation
    return out


def _terms_of(trail: dict | None) -> list[dict]:
    """A trail's inputs, then the terms the model stated that no row confirmed. Those are kept out
    of `inputs` because the engine's readers walk `inputs` and must not see them.

    IN THE MODEL'S ORDER where the terms carry one (`at`), because a whole answer takes the FIRST
    figure in that order: a stated amount at position 0 listed after the row at position 1 read
    "first(120; 100) = 100". Terms with no position — every cascade and note-row input, and the
    face settlement's deductions — keep the order they were written in, after the ones that have
    one.
    """
    trail = trail or {}
    items = [i for i in [*(trail.get("inputs") or ()), *(trail.get("unlocated") or ())]
             if isinstance(i, dict)]

    def position(n_item: tuple[int, dict]) -> tuple:
        n, item = n_item
        try:
            return (0, int(item["at"]), n)
        except (KeyError, TypeError, ValueError):
            return (1, 0, n)

    return [item for _n, item in sorted(enumerate(items), key=position)]


def merge_for_basis(store: dict | None, basis: str) -> tuple[str | None, list[dict]]:
    """(display formula, contributions) for one basis, both periods folded together.

    The formula is the CURRENT period's, falling back to the prior period's when the current year
    produced no figure — a row showing only last year's number should still explain that number
    rather than nothing. Contributions keep first-seen order from the current period, then any
    input only the prior period used, so the list reads down the page in the order the inputs were
    found rather than in dictionary order.

    A VERSION-2 TRAIL'S FORMULA IS BUILT FROM ITS INPUTS' NAMES AND SIGNS ("A + B − C",
    "max(A; B; C)") — see `_formula_of`. A legacy trail's stored formula is served unchanged.
    """
    store = store or {}
    current = store.get(_slot(basis, "current")) or {}
    prior = store.get(_slot(basis, "prior")) or {}
    if current.get("version") or prior.get("version"):
        formula = _formula_of(current) or _formula_of(prior) or None
    else:
        formula = current.get("formula") or prior.get("formula")

    prior_by_identity = {_identity(i): i for i in _terms_of(prior)}
    contributions: list[dict] = []
    seen: set[tuple] = set()
    for item in _terms_of(current):
        identity = _identity(item)
        seen.add(identity)
        twin = prior_by_identity.get(identity)
        contributions.append(_contribution(item, twin, current, prior))
    for item in _terms_of(prior):
        identity = _identity(item)
        if identity in seen:
            continue
        contributions.append(_contribution(None, item, current, prior))
    return formula, contributions


def _to_num(text: Any) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _dec(text: Any) -> Decimal | None:
    if text is None or text == "":
        return None
    try:
        return Decimal(str(text).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _signed(item: dict | None) -> float | None:
    """The input's figure AS IT ENTERED the result: negated when the rule subtracted it.

    The services record magnitudes — a deduction is disclosed as a positive amount and read as
    one — so the sign is the rule's, not the page's. Applied here rather than left to the
    renderer because the contributions list is the one place the figure is checked against the
    total printed above it, and every consumer of that list has to see the same arithmetic.

    THE LEGACY READING. A version-2 trail says what each input added (`used`) and `_figure` reads
    that instead.
    """
    value = _to_num((item or {}).get("value"))
    if value is None:
        return None
    return -abs(value) if (item or {}).get("deducted") else value


def _status(item: dict | None, trail: dict | None) -> str | None:
    """What the input did to the figure — ``used``, ``deducted``, ``same_row``, ``not_used`` or
    ``missing`` — or None for a legacy trail, which never recorded it."""
    if not item or not (trail or {}).get("version"):
        return None
    if item.get("status"):
        return str(item["status"])
    # A version-2 trail whose input carries no status — one carried over from a legacy trail by the
    # face settlement. Read the way the legacy fields read.
    if item.get("counted") is False:
        return "not_used"
    value = _to_num(item.get("value"))
    return "deducted" if item.get("deducted") or (value is not None and value < 0) else "used"


def _figure(item: dict | None, trail: dict | None) -> float | None:
    """The figure a contribution row shows: what an entered input ADDED, and an input that added
    nothing at its own figure (signed by its term) so it can be shown greyed beside the others."""
    if item is None:
        return None
    if not (trail or {}).get("version"):
        return _signed(item)
    if _status(item, trail) in ENTERED:
        used = _to_num(item.get("used"))
        return used if used is not None else _signed(item)
    value = _to_num(item.get("value"))
    if value is None:
        return None
    return -value if int(item.get("sign") or 1) < 0 else value


def _used(item: dict, trail: dict) -> Decimal | None:
    used = _dec(item.get("used"))
    if used is not None:
        return used
    figure = _figure(item, trail)
    return None if figure is None else _dec(figure)


def _why(item: dict | None) -> dict | None:
    """Why an input reads the way it does, as a code the screen and the export put into words."""
    if not item:
        return None
    reason = item.get("reason")
    if item.get("status") == "same_row":
        return {"code": "same_row", "line": item.get("via")}
    if reason == "counted_on_face":
        partial = bool(item.get("partial"))
        # A PARTIAL deduction is the money still left in the part, which is what the sentence names;
        # otherwise the amount the face prints.
        taken = _dec(item.get("used"))
        amount = str(abs(taken)) if partial and taken is not None else item.get("face_amount")
        return {"code": "counted_on_face", "caption": item.get("face_caption"),
                "whole": bool(item.get("whole_line")), "amount": amount, "partial": partial}
    if item.get("reduced_by") is not None:
        return {"code": "reduced", "amount": str(item.get("reduced_by"))}
    if item.get("face_caption") is not None and reason:
        # A face deduction the answering rung no longer needed: its label still names the face row.
        return {"code": str(reason), "caption": item.get("face_caption")}
    if item.get("located") is False:
        return {"code": str(reason or "not_located")}
    if reason:
        return {"code": str(reason)}
    rescaled = item.get("rescaled")
    if isinstance(rescaled, dict):
        # A NOTE ROW PRINTED IN ANOTHER SCALE than the face: its figure here is the converted one,
        # and the sentence names what the note printed and the two scales.
        return {"code": "rescaled", "amount": item.get("printed"),
                "from": rescaled.get("from"), "to": rescaled.get("to")}
    return None


def _words(item: dict, method: str) -> dict:
    """What an input's text IS: the filing's own words (`quote`), the model's (`model_quote`), or
    the configuration pattern a note row matched (`matched`).

    `excerpt` held all three and the engine's own sentences besides — "role=any_of", "stated in full
    in the sentence" — and the inspector printed every one of them in quotation marks, as though the
    filing had said it. The engine's sentences are replaced by `why` and `scale`, and are not
    served as text at all.
    """
    out: dict[str, str] = {}
    caption = str(item.get("caption") or "").strip()
    if method.startswith("cascade:") and caption:
        out["quote"] = caption                    # the part's printed caption, off its own row
    excerpt = str(item.get("excerpt") or "").strip()
    if not excerpt or excerpt.startswith("role="):
        return out
    if item.get("face_caption") is not None or item.get("reason") == "counted_on_face":
        return out                                # the face settlement's own sentence
    if method.startswith("note_sourced:") and excerpt.startswith("note '"):
        out["matched"] = excerpt
    elif method.startswith(("note_sourced:", "cascade:", "line_item_llm:")):
        pass
    elif method == "prose_sourced" or method.startswith("assembled:"):
        out["model_quote"] = excerpt
    else:
        # A payload from the services that are gone, whose excerpt was the filing's sentence.
        out["quote"] = out["excerpt"] = excerpt
    return out


def _prov_of(item: dict | None) -> dict | None:
    """The input's page for ITS period. A cascade input records the cell its part printed in that
    period (`period_provenance`); the engine's `provenance` is the part's first figure, whichever
    year that was, and stays as it was because the engine reads it."""
    if not item:
        return None
    if "period_provenance" in item:
        return item.get("period_provenance")
    return item.get("provenance")


def _contribution(current: dict | None, prior: dict | None, cur_trail: dict | None = None,
                  prior_trail: dict | None = None) -> dict:
    """One input as the inspector's contribution row, carrying both periods' figures.

    Deliberately the same keys `item_row` emits for a combined concept: label, canonical_key,
    v1/v2, method, residual, src/source, src2/source2, counted/counted2. A second shape would
    mean a second renderer, and the one that exists already handles click-to-source.

    A VERSION-2 TRAIL adds what each period's input did (`status`, `why1`/`why2`), its term's
    `role` and `sign`, whether the model's citation was `located`, the prose `scale`, and the text
    classified by `_words`; `v1`/`v2` are then what the input ADDED, and `counted` whether it added
    anything at all.
    """
    ref = current or prior or {}
    cur_v2 = bool((cur_trail or {}).get("version"))
    prior_v2 = bool((prior_trail or {}).get("version"))
    st1 = _status(current, cur_trail) if current else None
    st2 = _status(prior, prior_trail) if prior else None
    ref_trail = (cur_trail if current else prior_trail) or {}
    words = _words(ref, str(ref_trail.get("method") or ""))
    scale = _to_num(ref_trail.get("scale")) if ref_trail.get("version") else None
    return {
        "label": _label(ref),
        # THE KEY IS PASSED THROUGH WHEN THE INPUT HAS ONE, AND MOST DO NOT.
        #
        # This read `None` with the comment "an input is a note line, not a mapped concept". True
        # of a note-row input, and exactly false of a CASCADE input: those are the sub-line items a
        # derived parent is assembled from, so each one IS a mapped concept with its own figure,
        # its own citations and its own row. Hardcoding None here severed the main-to-sub hop no
        # matter what the trail recorded — the inspector had nothing to make clickable, so a reader
        # on a main line could not reach the sub-line whose notes explain it.
        #
        # Still None for a note row, because a note row genuinely is not a concept. The difference
        # is now carried by the data rather than asserted for every input.
        "canonical_key": ref.get("canonical_key"),
        "v1": _figure(current, cur_trail),
        "v2": _figure(prior, prior_trail),
        # THE FILING'S OWN WORDS, and only those — see `_words`.
        "excerpt": words.get("excerpt"),
        "deducted": ("deducted" in (st1, st2)) if (cur_v2 or prior_v2)
                    else bool(ref.get("deducted")),
        "method": ref.get("dataset"),
        "residual": False,
        "src": _src_label(_prov_of(current)),
        "source": _prov_of(current),
        "src2": _src_label(_prov_of(prior)),
        "source2": _prov_of(prior),
        "counted": ((st1 in ENTERED) if cur_v2 else bool((current or {}).get("counted", True)))
                   if current else False,
        "counted2": ((st2 in ENTERED) if prior_v2 else bool((prior or {}).get("counted", True)))
                    if prior else False,
        "status": st1,
        "status2": st2,
        "why1": _why(current) if cur_v2 else None,
        "why2": _why(prior) if prior_v2 else None,
        "role": ref.get("role"),
        "sign": ref.get("sign"),
        "located": ref.get("located") is not False,
        "scale": scale,
        "quote": words.get("quote"),
        "model_quote": words.get("model_quote"),
        "matched": words.get("matched"),
    }


def _label(item: dict) -> str:
    """The input's caption, qualified by the note it was printed under.

    A cascade routinely draws the same caption — "Depreciation" — from four different notes, and
    four rows reading "Depreciation" with different figures explain nothing. The note number is
    what distinguishes them, so it is part of the label rather than only a column.

    A label that already opens with its note ("note 26: Non-current portion", as a cited row is
    named) is not named again: "note 26: Non-current portion — note 26" said one thing twice.
    """
    label = str(item.get("label") or item.get("dataset") or "").strip()
    note = item.get("note")
    heading = item.get("note_heading")
    if note and re.match(rf"note {re.escape(str(note))}(?=$|[\s:：])", label, re.IGNORECASE):
        return label
    if note and heading:
        return f"{label} — note {note} ({heading})" if label else f"note {note} ({heading})"
    if note:
        return f"{label} — note {note}" if label else f"note {note}"
    return label


def _src_label(prov: dict | None) -> str:
    """The page a reader can look up: the folio the publisher printed, else the sheet position
    counted from 1, or the spreadsheet cell — `documents._prov_label`'s rule, with "" for none.
    The sheet position used to be printed as it is stored, from 0, so every chip read one page
    early and the first page read as no page at all."""
    if not isinstance(prov, dict) or not prov:
        return ""
    if prov.get("source_kind") == "spreadsheet" and prov.get("sheet"):
        return f"{prov['sheet']}!{prov.get('cell') or ''}"
    folio = prov.get("printed_page")
    if folio:
        return f"p.{folio}"
    page = prov.get("page_index")
    if isinstance(page, int) and not isinstance(page, bool):
        return f"p.{page + 1}"
    return ""


def _short(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


#: Reasons a term had no figure to be chosen: it printed none in this period, or it was an amount
#: the model stated and no note contains.
_NOT_A_CANDIDATE = ("no_figure", "stated_not_located")


def _candidate(item: dict, trail: dict | None) -> bool:
    """A term a `max`/`min`/`first` chooses between: not a deduction, not the same row read
    twice, and carrying a figure in this period."""
    return (item.get("role") != "adjustment"
            and _status(item, trail) not in ("same_row", "missing")
            and (_why(item) or {}).get("code") not in _NOT_A_CANDIDATE)


def _formula_of(trail: dict | None) -> str:
    """A version-2 trail's arithmetic in its inputs' names: "A + B − C", "max(A; B; C) − D",
    "first(A; B)". Only the terms that entered the figure are summed; a choosing rung lists every
    candidate it chose between."""
    if not trail or not trail.get("version"):
        return str((trail or {}).get("formula") or "")
    op = str(trail.get("op") or "sum")
    items = _terms_of(trail)
    if op == "sum":
        out = ""
        for item in items:
            status = _status(item, trail)
            if status not in ENTERED:
                continue
            name = _short(_label(item))
            if not out:
                out = ("−" if status == "deducted" else "") + name
            else:
                out += (" − " if status == "deducted" else " + ") + name
        return out
    out = f"{op}(" + "; ".join(_short(_label(i)) for i in items if _candidate(i, trail)) + ")"
    for item in items:
        status = _status(item, trail)
        if item.get("role") == "adjustment" and status in ENTERED:
            out += (" − " if status == "deducted" else " + ") + _short(_label(item))
    return out


def _numbers(text: str) -> list[float]:
    out = []
    for part in str(text or "").split(","):
        value = _to_num(part.strip())
        if value is not None:
            out.append(value)
    return out


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def working(store: dict | None, basis: str, period: str, *, shown: Any = None,
            edited: bool = False, flags: Any = ()) -> dict | None:
    """How one slot's figure adds up, and whether it is the figure the line shows.

    `shown` is the figure the line shows for this slot (None when it shows none), `edited` whether
    an analyst typed it, `flags` the row's and that value's flags. Decimal arithmetic on the stored
    strings: the sum of what the entered inputs and stated terms added, divided by the prose scale,
    is what the trail REACHED. `how`, first match wins:

      * ``not_recorded`` — a legacy trail, or one with no result: nothing to judge;
      * ``edited``       — an analyst's value stands; the trail is what the extraction reached;
      * ``differs``      — the inputs do not come to the trail's own result: a writer defect;
      * ``withheld``     — the line shows no figure for this slot;
      * ``changed``      — the line shows a different figure from the one the trail reached;
      * ``adds_up``.

    For ``withheld`` and ``changed`` `reason` names the flag of the stage that removed or changed
    the figure, when one did; a slot with neither a reason nor its own figure is what `audit`
    reports.
    """
    trail = (store or {}).get(_slot(basis, period))
    if not isinstance(trail, dict) or not trail:
        return None
    versioned = bool(trail.get("version"))
    result = _dec(trail.get("result"))
    scale = _dec(trail.get("scale")) if versioned else None
    items = _terms_of(trail)
    terms = []
    total = Decimal(0)
    for item in items:
        status = _status(item, trail)
        used = _used(item, trail) if status in ENTERED else None
        if used is not None:
            total += used
        if not versioned:
            added = None
        else:
            added = _float(used) if used is not None else 0.0
        terms.append({"label": _label(item), "canonical_key": item.get("canonical_key"),
                      "status": status, "used": added,
                      "value": _to_num(item.get("value")), "sign": int(item.get("sign") or 1),
                      "role": item.get("role"), "why": _why(item) if versioned else None,
                      "located": item.get("located") is not False})
    reached = (total / scale) if (versioned and scale) else total
    shown_d = _dec(shown)
    if not versioned or result is None:
        how = "not_recorded"
    elif edited:
        how = "edited"
    elif abs(reached - result) > TOL:
        how = "differs"
    elif shown_d is None:
        how = "withheld"
    elif abs(shown_d - result) > TOL:
        how = "changed"
    else:
        how = "adds_up"
    all_flags = [str(f) for f in (flags or ())]
    reason = None
    if how == "withheld":
        reason = next((f for f in all_flags if f.startswith(_WITHHELD_BY)), None)
    elif how == "changed":
        reason = next((f for f in all_flags
                       if f.startswith((f"llm_explained_out_of_sweep:{basis}:{period}:",
                                        "shared_figure_removed:"))), None)
    difference = (reached - result if how == "differs"
                  else shown_d - result if how == "changed" else None)
    trail_flags = [str(f) for f in (trail.get("flags") or ())]
    displaced = next((_numbers(f.split(":", 1)[1]) for f in trail_flags
                      if f.startswith("displaced_printed:")), None)
    return {
        "version": trail.get("version"),
        "how": how,
        "op": str(trail.get("op") or "sum"),
        "method": trail.get("method"),
        "combine": trail.get("combine"),
        "terms": terms,
        "sum": _float(total) if versioned else None,
        "scale": _float(scale),
        "result": _float(result),
        "shown": _float(shown_d),
        "difference": _float(difference),
        "missing": list(trail.get("missing") or []) if versioned else [],
        "refused": list(trail.get("refused") or []) if versioned else [],
        "displaced": displaced,
        "settled": "settled_against_face" in trail_flags,
        "reason": reason,
        "formula": _formula_of(trail) if versioned else str(trail.get("formula") or ""),
    }


def _as_printed(text: Any) -> str:
    """A figure with its thousands grouped and exactly the decimals it carries: "44.461", "1,000"."""
    figure = _dec(text)
    return "—" if figure is None else f"{figure:,}"


def _plain_amount(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _compared(term: dict) -> float:
    """The figure a `max`/`min`/`first` compared a candidate it did not take at: its own, less what
    the face settlement took out of it (`reduced_by`, served as the "reduced" why), signed by its
    term."""
    figure = term.get("value") or 0.0
    why = term.get("why") or {}
    if why.get("code") == "reduced":
        figure -= _to_num(why.get("amount")) or 0.0
    return figure * (term.get("sign") or 1)


def _arith(w: dict, fmt) -> str:
    """The working's arithmetic as one line: "788,507 + 128,412 − 549,975", "max(1,650; 1,200)",
    "529,841,000 ÷ 1,000" — magnitudes with their operators, prose amounts as stated in full."""
    terms = w.get("terms") or []
    op = str(w.get("op") or "sum")
    scale = w.get("scale")
    show = _plain_amount if scale else fmt
    entered = [t for t in terms if t.get("status") in ENTERED and t.get("used") is not None]
    if op == "sum":
        out = ""
        for t in entered:
            minus = t.get("status") == "deducted" or t["used"] < 0
            figure = show(abs(t["used"]))
            if not out:
                out = ("−" if minus else "") + figure
            else:
                out += (" − " if minus else " + ") + figure
        count = len(entered)
    else:
        candidates = [t for t in terms if t.get("role") != "adjustment"
                      and t.get("status") not in ("same_row", "missing")
                      and (t.get("why") or {}).get("code") not in _NOT_A_CANDIDATE]
        figures = [show(t["used"]) if t.get("status") in ENTERED else show(_compared(t))
                   for t in candidates]
        out = f"{op}(" + "; ".join(figures) + ")"
        for t in entered:
            if t.get("role") == "adjustment":
                minus = t.get("status") == "deducted" or t["used"] < 0
                out += (" − " if minus else " + ") + show(abs(t["used"]))
        count = 1
    if scale:
        out = (f"({out})" if count > 1 else out) + " ÷ " + _plain_amount(scale)
    return out or "0"


def working_text(w: dict | None, fmt, locale: str = "en") -> str:
    """The working as the Line Items sheet writes it beside the figure, in the reader's language.

    `fmt` formats a figure in the sheet's units. "788,507 + 128,412 − 549,975 = 366,944 — adds up.
    Optional parts not printed: …" — then the terms the filing did not print, the rungs passed over
    and the printed figures the rule replaced.
    """
    if not w:
        return ""
    how = w.get("how")
    if how == "not_recorded":
        return _say("How the inputs combine was not recorded.", locale)
    arith = _arith(w, fmt)
    result = fmt(w.get("result"))
    if how == "differs":
        reached = w.get("sum")
        if reached is not None and w.get("scale"):
            reached = reached / w["scale"]
        text = _say("{arith} = {reached} ≠ {result} — does not add up.", locale).replace(
            "{reached}", fmt(reached))
    elif how == "changed":
        text = _say("{arith} = {result}; now shows {shown}.", locale).replace(
            "{shown}", fmt(w.get("shown")))
    elif how == "edited":
        text = _say("Entered by an analyst; the extraction reached {result}: {arith}.", locale)
    elif how == "withheld":
        text = _say("Withheld; the extraction reached {result}: {arith}.", locale)
    else:
        text = _say("{arith} = {result} — adds up.", locale)
    parts = [text.replace("{arith}", arith).replace("{result}", result)]
    missing = w.get("missing") or []
    named = [str(m.get("label") or m.get("key")) for m in missing if m.get("role") != "any_of"]
    optional = [str(m.get("label") or m.get("key")) for m in missing if m.get("role") == "any_of"]
    if named:
        parts.append(_say("Not found: {lines}.", locale).replace("{lines}", "; ".join(named)))
    if optional:
        parts.append(_say("Optional parts not printed: {lines}.", locale).replace(
            "{lines}", "; ".join(optional)))
    for refusal in w.get("refused") or []:
        rung = str(refusal.get("rung") or "")
        if refusal.get("why") == "negative":
            parts.append(_say("Rule {rung} came to {value}, below zero, and was passed over.",
                              locale).replace("{rung}", rung)
                         .replace("{value}", fmt(_to_num(refusal.get("value")))))
        else:
            parts.append(_say("Rule {rung} was passed over: a deduction it names is the row its "
                              "total already reads.", locale).replace("{rung}", rung))
    if w.get("displaced"):
        parts.append(_say("Replaces the printed {figures}.", locale).replace(
            "{figures}", ", ".join(fmt(v) for v in w["displaced"])))
    return " ".join(parts)


def status_text(status: str | None, why: dict | None = None, locale: str = "en") -> str:
    """What a trail row did to the figure, for the sheet's How column: "added", "deducted",
    "same row, added once", "not used", "not found"."""
    code = (why or {}).get("code")
    if status == "missing" and code in ("not_located", "refused"):
        return _say(_REASON_WORDS[code], locale)
    word = _say(_STATUS_WORDS.get(str(status or ""), str(status or "")), locale)
    if status in ENTERED and code == "stated_not_located":
        word += " · " + _say(_REASON_WORDS["stated_not_located"], locale)
    return word


def why_text(why: dict | None, fmt, locale: str = "en") -> str:
    """Why a trail row reads the way it does, in the reader's language — or "" when its status
    already says it."""
    if not why:
        return ""
    code = why.get("code")
    if code == "same_row":
        line = why.get("line")
        return _say("already added as “{line}”", locale).replace("{line}", str(line)) if line else ""
    if code == "counted_on_face":
        amount = fmt(_to_num(why.get("amount")))
        if why.get("whole"):
            return _say("the face prints this whole line, {amount}; the parts it was built from "
                        "are counted there", locale).replace("{amount}", amount)
        if why.get("partial"):
            return _say("only {amount} of it was still in the part", locale).replace(
                "{amount}", amount)
        return ""
    if code == "reduced":
        return _say("{amount} of it is counted on the face", locale).replace(
            "{amount}", fmt(_to_num(why.get("amount"))))
    if code == "rescaled":
        # The printed figure is in the NOTE's units, so it is written as printed — every decimal
        # it was printed with — and not in the sheet's units; so are the two scales.
        return (_say("printed as {amount} in units of {from}; converted to the statement's units "
                     "of {to}", locale)
                .replace("{amount}", _as_printed(why.get("amount")))
                .replace("{from}", _as_printed(why.get("from")))
                .replace("{to}", _as_printed(why.get("to"))))
    phrase = _REASON_WORDS.get(str(code or ""))
    return _say(phrase, locale) if phrase else ""


def face_label(caption: str, locale: str = "en") -> str:
    """The trail row of money a face row prints, named in the reader's language."""
    return _say("Counted on the face in “{caption}”", locale).replace("{caption}", caption)


def model_quote_text(quote: str, locale: str = "en") -> str:
    return f"{_say('As quoted by the model', locale)}: “{quote}”"


def word(text: str, locale: str = "en") -> str:
    """One of the export's fixed phrases in the reader's language."""
    return _say(text, locale)


def audit(rows: list[dict]) -> dict:
    """Every trail slot of a run's served rows, judged: ``{"counts": …, "problems": […]}``.

    A PROBLEM is a slot whose inputs do not come to its own result, or one whose line shows another
    figure or none with no stage's flag saying why. The invariant test runs this over stored runs;
    the measurement reports its counts per run.
    """
    from app.services.periods import edited_for, slot_for

    counts = {k: 0 for k in ("slots", "adds_up", "differs", "changed", "withheld", "edited",
                             "not_recorded")}
    problems: list[dict] = []
    for row in rows or ():
        store = row.get("derivation") or {}
        if not isinstance(store, dict):
            continue
        for slot, trail in store.items():
            if not isinstance(trail, dict) or not trail:
                continue
            basis, _, period = str(slot).partition(":")
            value = slot_for(row, basis, period)
            flags = list(row.get("flags") or []) + list(
                ((value or {}).get("confidence") or {}).get("flags") or [])
            w = working(store, basis, period, shown=(value or {}).get("value"),
                        edited=edited_for(row, basis, period), flags=flags)
            if w is None:
                continue
            counts["slots"] += 1
            counts[w["how"]] += 1
            if w["how"] == "differs" or (w["how"] in ("changed", "withheld") and w["reason"] is None):
                problems.append({"key": row.get("canonical_key"), "slot": slot, "how": w["how"],
                                 "method": w["method"], "result": w["result"], "sum": w["sum"],
                                 "shown": w["shown"]})
    return {"counts": counts, "problems": problems}
