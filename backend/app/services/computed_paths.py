"""TWO PATHS TO A FIGURE, and one place that decides which of them publishes it.

WHY THIS EXISTS. Eight concepts are reachable two different ways:

  * the GENERIC path — the rulebook. An alias names the printed caption, the section banner
    scopes it, the template's rollups check it. It works on any filing whose captions the
    vocabulary covers, and it degrades gracefully: an unrecognised caption becomes a review item.

  * the COMPLEX path — five hand-written derivations (deprec_impairment, secur_fincl_assets,
    related_party_receivables, sales_revenues, contingent_liabilities), each implementing a spec
    in ``docs/`` that assembles a figure from up to a dozen note-level datasets and resolves it
    through a fixed priority cascade. It is far more capable ON THE FILINGS ITS SPEC WAS WRITTEN
    FROM, and it is where the audited derivations live.

The complex path's reach is bounded by CLOSED CAPTION ENUMERATIONS. Measured:
``deprec_impairment`` carries 162 caption alternatives, ``contingent_liabilities`` 145,
``related_party_receivables`` 106, ``secur_fincl_assets`` 89 — whitelists, not shapes. A filing
that writes "Depreciation charge for the year" instead of one of the eight spellings
``_QUALIFYING_RE`` lists has nothing for the P1–P5 cascade to stand on, so the cascade refuses.
On two filings from outside the reference set that produced 2 of 8 and 3 of 8 figures; the rest
were blank, revenue included.

WHAT WAS WRONG WITH HOW THEY MET. Nothing chose between them. Each derivation stage ran after
``MapOntologyStage`` and called ``row.set_value`` unconditionally, so the complex path silently
won whenever it produced anything and the generic reading was discarded without a record. That is
a defensible default and it is preserved as one — but it was not a decision anybody could see,
change, or audit, and when the complex path refused there was no fallback because nothing knew a
second path existed.

So the precedence is now stated, configurable, and recorded:

    complex      the derivation publishes; a generic reading it displaces is recorded, not lost
    generic      the rulebook reading publishes; the derivation is recorded as corroboration
    corroborate  the rulebook reading publishes WHERE IT HAS ONE, the derivation fills the gaps,
                 and a disagreement beyond tolerance is flagged for review rather than hidden

Set globally (``computed_path_precedence``), or per concept in config.toml
(``computed_path_by_key``) because a per-key mapping cannot travel through the admin settings
patch — the same split ``llm_focus_keys``/``llm_focus_only`` already uses and for the same reason.
Either path can also be switched off outright, which is what makes them separable at all:
``complex_path_enabled = false`` runs a filing on the rulebook alone, and that is the honest
measurement of what the generic layer can do.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.core.models.enums import Basis
from app.core.models.line_item import ExtractedValue, LineItem

# The five derivations, by the name each stage reports itself as. Used to gate them individually.
COMPLEX_SERVICES = ("deprec_impairment", "secur_fincl_assets", "related_party_receivables",
                    "sales_revenues", "contingent_liabilities")

PRECEDENCE = ("complex", "generic", "corroborate")

# Flags a reviewer sees on the row. Named for what happened, not for which code branch ran.
FLAG_DISPLACED = "GENERIC_READING_DISPLACED"      # the derivation overwrote a printed reading
FLAG_DISAGREE = "PATH_DISAGREEMENT"               # the two paths differ beyond tolerance
FLAG_FILLED_GAP = "DERIVATION_FILLED_GAP"         # generic had nothing; the derivation supplied it
FLAG_CORROBORATED = "PATHS_AGREE"                 # both produced the same figure


@dataclass(frozen=True)
class PathPolicy:
    """The resolved policy for one run. Read once per stage rather than per row."""

    complex_enabled: bool
    precedence_default: str
    by_key: dict[str, str]
    enabled_services: tuple[str, ...]
    rel_tolerance: float

    def precedence_for(self, canonical_key: str) -> str:
        """The precedence in force for one concept, ALWAYS one of ``PRECEDENCE``.

        Both the per-key value and the default are validated here rather than only where the
        policy is built. `policy_from` already screens the settings, and pydantic's ``Literal``
        screens them again before that — but a caller that constructs a ``PathPolicy`` directly
        (the tests, and any future caller) would otherwise get its own typo back, and the one
        thing this function must never do is return a precedence the writer has no branch for.
        """
        got = str(self.by_key.get(canonical_key, "") or "").strip().lower()
        if got in PRECEDENCE:
            return got
        return self.precedence_default if self.precedence_default in PRECEDENCE else "complex"

    def runs(self, service: str) -> bool:
        if not self.complex_enabled:
            return False
        return not self.enabled_services or service in self.enabled_services


def policy_from(settings) -> PathPolicy:
    """The policy this run's settings describe.

    Tolerant of a settings object that predates these fields, so a caller constructed from an
    older config still works and gets today's behaviour — which is what `complex` means.
    """
    ex = getattr(settings, "extraction", settings)
    prec = str(getattr(ex, "computed_path_precedence", "complex") or "complex").lower()
    return PathPolicy(
        complex_enabled=bool(getattr(ex, "complex_path_enabled", True)),
        precedence_default=prec if prec in PRECEDENCE else "complex",
        by_key=dict(getattr(ex, "computed_path_by_key", {}) or {}),
        enabled_services=tuple(getattr(ex, "complex_path_services", ()) or ()),
        rel_tolerance=float(getattr(ex, "recon_rel_tolerance", 0.01) or 0.01),
    )


def _as_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _differs(a: Decimal, b: Decimal, rel: float) -> bool:
    """Whether two figures disagree by more than the reconciliation band.

    The same relative band the reconciler uses, so "these two paths agree" means the same thing
    here as it does there. An absolute zero on one side and a figure on the other always differs.
    """
    if a == b:
        return False
    scale = max(abs(a), abs(b))
    if scale == 0:
        return False
    return abs(a - b) / scale > Decimal(str(rel))


def existing_value(row: LineItem | None, basis: str, period_label: str) -> ExtractedValue | None:
    """The value the GENERIC path already put on this (row, basis, period), if any.

    ``None`` for a row the mapper created but never valued, which is the ordinary case for a
    concept only a derivation can reach — a row exists so the template can show the line.
    """
    if row is None:
        return None
    for ev in (row.values or {}).values():
        if ev.value is None:
            continue
        if ev.basis == Basis(basis) and (ev.period_label or "") == (period_label or ""):
            return ev
    return None


@dataclass
class WriteOutcome:
    """What the policy decided, so the caller can log one line about it."""

    wrote: bool
    reason: str
    flags: tuple[str, ...] = ()


def resolve_write(policy: PathPolicy, canonical_key: str, row: LineItem | None,
                  basis: str, period_label: str, derived) -> WriteOutcome:
    """Decide whether the COMPLEX path's figure publishes over the GENERIC path's.

    ``derived`` is the derivation's own value (Decimal or None). The decision is separated from
    the writing so it can be unit-tested without a DocumentModel, and so every stage reaches the
    identical answer instead of five copies of an implicit one.
    """
    prec = policy.precedence_for(canonical_key)
    have = existing_value(row, basis, period_label)
    generic = _as_decimal(have.value) if have is not None else None
    complex_v = _as_decimal(derived)

    if complex_v is None:
        return WriteOutcome(False, "derivation produced nothing")

    if generic is None:
        # Nothing to displace. Every precedence publishes here — this is the case the old code
        # could not express, because it had no notion that the generic path might have answered.
        flags = (FLAG_FILLED_GAP,) if prec != "complex" else ()
        return WriteOutcome(True, f"{prec}: no generic reading, derivation fills the gap", flags)

    disagrees = _differs(generic, complex_v, policy.rel_tolerance)
    agree_flag = () if disagrees else (FLAG_CORROBORATED,)

    if prec == "generic":
        # The printed reading stands. The derivation is still recorded on the row's derivation
        # trail by the caller, so the working is not lost — it just does not publish.
        return WriteOutcome(False,
                            f"generic: rulebook reading {generic} stands"
                            + (f", derivation says {complex_v}" if disagrees else " (corroborated)"),
                            (FLAG_DISAGREE,) + agree_flag if disagrees else agree_flag)

    if prec == "corroborate":
        if disagrees:
            return WriteOutcome(False,
                                f"corroborate: kept generic {generic}, derivation {complex_v} "
                                f"disagrees and is flagged for review",
                                (FLAG_DISAGREE,))
        return WriteOutcome(False, f"corroborate: paths agree on {generic}", agree_flag)

    # "complex" — today's behaviour, now with a record of what it displaced.
    flags = (FLAG_DISPLACED,) + ((FLAG_DISAGREE,) if disagrees else agree_flag)
    return WriteOutcome(True,
                        f"complex: derivation {complex_v} publishes over generic {generic}",
                        flags)


def apply_computed(doc, *, canonical_key: str, basis: str, period_label: str,
                   value, formula: str, service: str, evidence: list[dict],
                   flags: list[str], next_ordinal: list[int], policy: PathPolicy,
                   log=None) -> bool:
    """Write one derived figure, subject to the two-path policy. Returns whether it published.

    THE ONE IMPLEMENTATION. Four stages carried a byte-for-byte identical `_apply` differing only
    in the service name embedded in `confidence.method`, and each one wrote unconditionally — so
    the precedence between the paths was five implicit copies of a decision nobody had made. It
    is here once, applied identically, and the derivation trail is recorded WHETHER OR NOT the
    figure publishes: a derivation that lost to the printed reading is still the best account of
    how that figure could have been assembled, and throwing it away is what made the old
    behaviour unauditable.
    """
    from app.services.derivation import build, input_from_evidence, record

    row = next((li for li in doc.line_items if li.canonical_key == canonical_key), None)
    outcome = resolve_write(policy, canonical_key, row, basis, period_label, value)
    all_flags = list(flags) + [f for f in outcome.flags if f not in flags]

    if log is not None:
        log(f"{service}:{canonical_key}:{basis}:{period_label}:{outcome.reason}")

    # A row that does not exist yet is only created when something will actually be written to
    # it — either a published figure or a derivation trail worth keeping. A refusal that creates
    # an empty row publishes a blank line the template did not ask for.
    if not outcome.wrote and value is None:
        return False
    if row is None:
        row = LineItem(source_label=canonical_key, canonical_key=canonical_key,
                       ordinal=next_ordinal[0])
        next_ordinal[0] += 1
        doc.line_items.append(row)

    if outcome.wrote:
        src_prov = next((e["provenance"] for e in evidence if e.get("provenance")), None)
        row.set_value(ExtractedValue(value=value, value_raw=value, basis=Basis(basis),
                                     period_label=period_label, provenance=src_prov))
        row.confidence.method = f"computed:{service}:{formula}"

    row.derivation = record(
        row.derivation, basis=basis, period_label=period_label,
        derivation=build(method=service, formula=formula,
                         inputs=[input_from_evidence(e) for e in evidence],
                         result=value, flags=all_flags))
    for flag in all_flags:
        if flag not in row.confidence.flags:
            row.confidence.flags.append(flag)
    return outcome.wrote
