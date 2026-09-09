"""Add up the rows the model DECLARED components of one line item, and record the trail.

WHY THIS EXISTS. A line item's figure is often the sum of several printed rows rather than one:
a note that splits depreciation by function prints four, and all four belong on the
operating-expense line. Before this, two rows landing on one concept was an ``ambiguous_mapping``
and the line went unfilled — the flag treats a repeated figure and a genuine component set as the
same thing.

THEY ARE NOT THE SAME THING, AND THE DIFFERENCE IS INVISIBLE AFTERWARDS. A face line and the note
total behind it are the SAME amount printed twice; adding them overstates the line, and the
statement still balances because the parent's own row was consumed. A component set adds up to
something the filing also prints. Both produce "a bigger number", so nothing downstream can tell
them apart — which is why the model is asked to DECLARE which case it is
(``LlmBatchItem.role``), and why only a declared component is summed here. Two undeclared rows on
one key stay ambiguous, exactly as before.

WHAT THE TRAIL CARRIES, and where each part comes from:

* WHERE IT WAS PRINTED — the note, the caption and the page, taken from the ROW's own provenance.
  Not from the model: it was never given a bbox, so a location it stated would be a citation that
  looks authoritative and points at the wrong place.
* WHY IT WAS INCLUDED — the model's ``reason`` for that row. The one part of the trace only it can
  supply, and the reason ``role`` is asked per row rather than per concept.
* THE ARITHMETIC — the value, the sign the model declared, and the total. So the sum is checkable
  without re-deriving it.

Written through ``services.derivation.build`` rather than a new structure, because a row assembled
out of several inputs is what that trail was already for and the statement inspector already
renders it as contributions with click-to-source.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.core.models.document import DocumentModel
from app.services import derivation

# The flags `stages.map_ontology._apply_result` leaves on a declared component.
_OF = "component_of:"
_SIGN = "component_sign:"
_REASON = "llm_reason:"


def _flag(item, prefix: str) -> str | None:
    for f in item.confidence.flags or ():
        if str(f).startswith(prefix):
            return str(f)[len(prefix):]
    return None


def _basis(value) -> str:
    """The basis as the string the derivation trail is keyed on.

    `ExtractedValue.basis` is an ENUM, so `str()` on it gives "Basis.CONSOLIDATED" — which keys a
    slot nothing else can look up, and the trail then reads as absent rather than wrong. Uses
    `.value` where there is one, which is also what `services.derivation._slot` is given
    everywhere else.
    """
    return str(getattr(value, "value", value) or "")


def _num(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _slots(item) -> list[tuple[str, str, Decimal]]:
    """Every (basis, period, value) this row states, with a parseable amount."""
    out: list[tuple[str, str, Decimal]] = []
    for v in item.values or ():
        amount = _num(getattr(v, "value", None) if getattr(v, "value", None) is not None
                      else getattr(v, "value_raw", None))
        if amount is None:
            continue
        out.append((_basis(getattr(v, "basis", "")),
                    str(getattr(v, "period_label", "") or ""), amount))
    return out


def assemble(doc: DocumentModel, *, log=None) -> int:
    """Fill each concept whose components were declared, and return how many were filled.

    A concept is assembled only when NO row claims to be its whole figure. A `whole` row is the
    filing stating the amount directly, and a declared component beside it means the model read
    the same money twice — reported rather than silently added to, because choosing either way
    would be inventing a total the filing does not print.
    """
    components: dict[str, list] = {}
    whole: dict[str, list] = {}
    for item in doc.line_items or ():
        key = item.canonical_key
        if not key:
            continue
        if _flag(item, _OF) == key:
            components.setdefault(key, []).append(item)
        else:
            whole.setdefault(key, []).append(item)

    filled = 0
    for key, rows in sorted(components.items()):
        if key in whole:
            # THE ONE CASE THIS REFUSES. Reported on every row involved so a reviewer sees it on
            # whichever they open, and left unassembled: the printed figure stands.
            for row in rows:
                row.confidence.flags.append(f"component_beside_stated_total:{key}")
                row.confidence.flags.append("low_mapping_confidence")
            if log:
                log(f"assemble:{key}:refused(a row states the whole figure and "
                    f"{len(rows)} more claim to be components of it)")
            continue

        # Per (basis, period), because a component set is only a sum within one column: mixing
        # the current year's rows with the prior year's would produce a figure the filing never
        # states in either.
        by_slot: dict[tuple[str, str], list[tuple[object, Decimal, int]]] = {}
        for row in rows:
            sign = -1 if (_flag(row, _SIGN) or "1").strip().lstrip("+") == "-1" else 1
            for basis, period, amount in _slots(row):
                by_slot.setdefault((basis, period), []).append((row, amount, sign))

        target = rows[0]
        for (basis, period), parts in sorted(by_slot.items()):
            total = sum((amount * sign for _row, amount, sign in parts), Decimal(0))
            inputs = []
            for row, amount, sign in parts:
                inputs.append({
                    # WHERE, off the row itself — never restated by the model.
                    "label": row.source_label,
                    "note": row.note_number,
                    "value": str(amount),
                    "provenance": _provenance_of(row, basis, period),
                    # WHY, the model's own sentence for this row.
                    "excerpt": _flag(row, _REASON),
                    # THE ARITHMETIC.
                    "deducted": sign < 0,
                    "counted": True,
                })
            target.derivation = derivation.record(
                target.derivation, basis=basis, period_label=period,
                derivation=derivation.build(
                    method="assembled:declared_components",
                    formula=" + ".join("−" + i["label"] if i["deducted"] else i["label"]
                                       for i in inputs) or None,
                    inputs=inputs, result=total,
                    flags=[f"components:{len(parts)}"]))
            _write(target, basis, period, total)
            filled += 1
        target.is_computed = True
        target.confidence.flags.append(f"assembled_from:{len(rows)}")
        if log:
            log(f"assemble:{key}:{len(rows)} component(s) over "
                f"{len(by_slot)} (basis,period) slot(s)")
    return filled


def _provenance_of(row, basis: str, period: str) -> dict | None:
    for v in row.values or ():
        if (_basis(getattr(v, "basis", "")) == basis
                and str(getattr(v, "period_label", "") or "") == period):
            return derivation._json_safe_provenance(getattr(v, "provenance", None))
    return None


def _write(row, basis: str, period: str, total: Decimal) -> None:
    """Put the assembled total on the row it is filed under, replacing that slot's own part.

    The target row keeps its own caption and provenance — it IS one of the components, and the
    trail above says which. Writing a new row instead would leave the components on the statement
    as well as their total, which double-counts the section.
    """
    for v in row.values or ():
        if (_basis(getattr(v, "basis", "")) == basis
                and str(getattr(v, "period_label", "") or "") == period):
            v.value = total
            return
