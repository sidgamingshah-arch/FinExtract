"""INCOME TAX, THE MINORITY SHARE AND 税金及附加 ARRIVE SIGNED THE WAY THEIR ROLLUPS ADD THEM.

The HK template sums them: Profit(Loss) Bef Extraord Items = Profit(Loss) Before Tax + Total Income
Tax, Profit for the year = PBEI + … + Minority Interests(PL), and Other Income(Exp) holds Other
Non-Income Related Taxes. A mainland income statement prints all three as unsigned magnitudes the
statement itself deducts, and nothing negated them, so every CAS filing ADDED its tax, its minority
share and its 税金及附加: measured on 000883, PBEI 4.07bn against a printed 1.88bn (2023). The
line-item set already negates the other CAS captions printed this way (营业外支出, 财务费用,
支付的各项税费); these three now do the same, and an HKFRS face's bracketed tax is left as printed.
The minority share is flipped on an HKFRS face too: "Non-controlling interests (410,074)" is a
share of a LOSS, which the template's profit for the year adds back.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.models.document import DocumentModel, PageSource
from app.core.models.enums import Basis
from app.core.models.geometry import Provenance
from app.core.models.line_item import ExtractedValue, LineItem
from app.core.stage import PipelineContext
from app.schemas.line_items import load_line_item_set
from app.services.working_view import build_working_view
from app.stages.normalize import NormalizeStage

SEED = (Path(__file__).resolve().parent.parent
        / "app" / "sample" / "templates" / "output_csv_hk_line_items.json")


@pytest.fixture(scope="module")
def ontology():
    return build_working_view(load_line_item_set(json.loads(SEED.read_text(encoding="utf-8")),
                                                 resolve=True))


def _normalised(ontology, label: str, key: str, amount: str) -> Decimal:
    li = LineItem(source_label=label, canonical_key=key)
    li.set_value(ExtractedValue(value=Decimal(amount), value_raw=Decimal(amount),
                                basis=Basis.CONSOLIDATED, period_label="current",
                                provenance=Provenance(page_index=0)))
    doc = DocumentModel(filename="f.pdf")
    doc.pages = [PageSource(index=0, statement="profit_and_loss")]
    doc.line_items = [li]
    ctx = PipelineContext(raw_bytes=b"")
    ctx.ontology = ontology
    NormalizeStage().run(doc, ctx)
    return next(iter(li.values.values())).value


@pytest.mark.parametrize("label, key, printed, signed", [
    ("减：所得税费用", "is_pl__total_income_tax", "583236069.93", "-583236069.93"),
    ("减：所得税费用", "is_pl__total_income_tax", "-773433.24", "773433.24"),      # a tax credit
    ("2.少数股东损益", "is_pl__minority_interests_pl", "405241174.05", "-405241174.05"),
    ("税金及附加", "is_pl__other_non_income_related_taxes", "193625794.60", "-193625794.60"),
    ("Non-controlling interests 非控股權益", "is_pl__minority_interests_pl", "-410074", "410074"),
    ("Income tax expense 稅項開支", "is_pl__total_income_tax", "-189504", "-189504"),  # as printed
])
def test_the_caption_is_signed_for_its_rollup(ontology, label, key, printed, signed):
    assert _normalised(ontology, label, key, printed) == Decimal(signed)
