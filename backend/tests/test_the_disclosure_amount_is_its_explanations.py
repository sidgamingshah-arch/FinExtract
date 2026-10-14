"""The amount beside a disclosure's explanation is the amount of the period that explanation describes.

`contingent_liabilities.disclosure_explanation` names the period it explains in `basis_period`, and
`documents._with_quantified_amounts` reads the amount from that period. It used to read consolidated
current whatever the explanation described, so on a filing that prints only the company's statements
the standalone contingent-liability figure (1,120 on an Ind AS test filing) disappeared from the
Disclosures screen. An entry that names no period is read on consolidated current, as before.
"""
from __future__ import annotations

from app.api.routes.documents import _with_quantified_amounts

_ROWS = [{"canonical_key": "notes__contingent_liabilities", "values": [
    {"basis": "consolidated", "period_label": "current", "value": "640"},
    {"basis": "standalone", "period_label": "current", "value": "1120"},
    {"basis": "standalone", "period_label": "prior", "value": "980"}]}]


def test_the_amount_is_read_from_the_period_explained():
    (entry,) = _with_quantified_amounts(
        [{"key": "contingent_liabilities", "basis_period": "standalone:current"}],
        [{"canonical_key": "notes__contingent_liabilities",
          "values": [{"basis": "standalone", "period_label": "current", "value": "1120"}]}])

    assert entry["amount"] == "1120"


def test_each_period_explained_gets_its_own_amount():
    for basis_period, amount in (("standalone:current", "1120"), ("standalone:prior", "980"),
                                 ("consolidated:current", "640")):
        (entry,) = _with_quantified_amounts(
            [{"key": "contingent_liabilities", "basis_period": basis_period}], _ROWS)
        assert entry["amount"] == amount, basis_period


def test_an_entry_that_names_no_period_is_read_on_consolidated_current():
    (entry,) = _with_quantified_amounts([{"key": "contingent_liabilities"}], _ROWS)

    assert entry["amount"] == "640"
