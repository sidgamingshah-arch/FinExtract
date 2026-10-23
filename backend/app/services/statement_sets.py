"""WHICH STATEMENT SET A TEMPLATE READS, when a filing prints its statements under two standards.

`stages.classify.statement_frameworks` tags each face page of a dual-standards filing with the set
it belongs to ("IFRS" / "CAS"); this says which of the two the HK template reads, and which pages
are therefore the other set's. Kept apart from `face_context` so the stages can ask without
importing the request builder.
"""
from __future__ import annotations

from app.services.mapping import normalize_statement

# The standards the HK template is built on — see `face_context.face_rows`.
PREFERRED_FRAMEWORK = "IFRS"
# The statements whose OTHER set feeds nothing on the deterministic route — see `unread_set_pages`.
# Every statement the template's face lines are read from: PetroChina's CAS balance sheet (page
# index 125-126) and cash flow statement (128) were summed with the IFRS ones (227, 228-229) as one
# consolidated basis, which roughly doubled every balance-sheet total (Total current liabilities
# 784,751 against a printed 637,317).
ONE_SET_STATEMENTS = frozenset({"profit_and_loss", "balance_sheet", "cash_flow"})
# On a face row of a statement set this template does not read: `<flag>:<its standards>`.
SET_NOT_READ_FLAG = "statement_set_not_read"


def read_framework(doc) -> str | None:
    """The statement set this template reads when the filing prints two, else None.

    The same choice `face_context.face_rows` makes for the model's blocks: the preferred standards
    where the filing prints them, else the first by name. A filing whose face pages carry one set
    or none (`stages.classify.statement_frameworks` tags nothing then) has nothing to choose
    between.
    """
    frameworks = {str((getattr(p, "evidence", None) or {}).get("framework") or "")
                  for p in (getattr(doc, "pages", None) or ())}
    frameworks.discard("")
    if len(frameworks) < 2:
        return None
    return PREFERRED_FRAMEWORK if PREFERRED_FRAMEWORK in frameworks else sorted(frameworks)[0]


def unread_set_pages(doc, statements=ONE_SET_STATEMENTS) -> dict[int, str]:
    """``{page index: standards}`` for the face pages of the statement set NOT read, per statement.

    ONE PRINTING OF EACH STATEMENT PER ENTITY BASIS FEEDS THE FACE. An A+H filer prints its statements twice:
    PetroChina's CAS "CONSOLIDATED AND COMPANY INCOME STATEMENTS" (page index 127, the Group and
    the Company side by side) and its IFRS "CONSOLIDATED STATEMENT OF COMPREHENSIVE INCOME" (226).
    The model's blocks already keep the CAS set apart (`face_context.face_rows`), but the
    deterministic route read both as one face: the CAS rows were bound and swept beside the IFRS rows, so the profit for the
    year carried a second owners' row (164,676 beside the IFRS 164,684) and the CAS net profit,
    taxation and revenue went into Other Operating Expenses. The balance sheet and the cash flow
    statement were summed the same way (CAS page index 125-126 and 128 beside IFRS 227 and 228-229), so
    every balance-sheet total came out at about twice the printed figure. Rows on these pages are left unbound
    and unswept (`SET_NOT_READ_FLAG`); they are still extracted and still shown to the model, apart.
    """
    read = read_framework(doc)
    if read is None:
        return {}
    want = {normalize_statement(str(s)) for s in statements}
    out: dict[int, str] = {}
    for p in getattr(doc, "pages", None) or ():
        framework = str((getattr(p, "evidence", None) or {}).get("framework") or "")
        if (framework and framework != read
                and normalize_statement(str(getattr(p, "statement", "") or "")) in want):
            out[int(getattr(p, "index", -1))] = framework
    return out
