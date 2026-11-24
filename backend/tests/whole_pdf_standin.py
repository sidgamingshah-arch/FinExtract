"""Run the WHOLE-PDF ROUTE on a real filing with SCRIPTED replies — no model, no key, no network.

WHY THIS EXISTS AS A SHARED MODULE. The whole-PDF route (`extraction.whole_pdf_to_model`,
`stages.whole_pdf`) sends the PDF itself to the model, and with no provider that takes a file the
stage falls back at once and the rules read the filing. So nothing about the route — what the
model's rows replace, what the rules still read beside them, which stages run — could be observed
in a test or on a real filing. This runs the real pipeline, extraction included, on a real filing,
with the shipped stand-in (`scripts/llm_standin.py`) as the provider: it declares that it takes
files, and answers each whole-PDF request from a SCRIPT (`scripted_whole_pdf_reply`), as a model
that read the part would answer it.

WHAT IT IS NOT. A script decides every figure the "model" gives, so a test built on this shows
what the pipeline DOES with a given answer — never how well a model reads a filing. Every reply it
returns is labelled `standin-scripted`, and nothing here may be reported as a model's figure.

    from tests.whole_pdf_standin import run_whole_pdf, script_figure

    script = {"lines": {"sub__cp_face_trading_fincl_assets": [
        script_figure("1783494750.68", page=150, caption="交易性金融资产")]}}
    doc, ctx, rec = run_whole_pdf(monkeypatch, tmp_path, "688008_montage_2024_prc", script,
                                  pages=range(149, 152))

`pages` (0-based, as the Page Scope screen stores them) narrows what is read AND sent, so a test can
run on the few pages it is about in a few seconds; None reads the filing the way a run does. Other
model calls the run makes (the disclosures, the contingent-liability rewrite, the shared-figure
tie-break) are switched off by default, so the stand-in is asked nothing but the whole-PDF
requests; pass `extraction={...}` to set them, or any other extraction setting, yourself.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FILINGS = REPO / "testdata" / "filings"
TEMPLATES = BACKEND / "app" / "sample" / "templates"
HK_TEMPLATE = "output_csv_hk_v1_template.json"
HK_SET = "output_csv_hk_line_items.json"


def standin_module():
    """`scripts/llm_standin.py`, loaded once (the tests load it the same way)."""
    if "llm_standin" in sys.modules:
        return sys.modules["llm_standin"]
    path = BACKEND / "scripts" / "llm_standin.py"
    spec = importlib.util.spec_from_file_location("llm_standin", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["llm_standin"] = module  # pydantic resolves the module's annotations by name
    spec.loader.exec_module(module)
    return module


def script_figure(amount: str, *, page: int, caption: str = "", basis: str = "consolidated",
                  period: str = "current", parts=()) -> dict:
    """One scripted figure: `page` is the REPORT's page, 1 = the file's first (a PDF viewer's
    number, i.e. a 0-based page index + 1). `parts`: (caption, amount) rows of a sum."""
    out = {"basis": basis, "period": period, "amount": str(amount), "page": int(page),
           "caption": caption}
    if parts:
        out["parts"] = [{"caption": c, "amount": str(a)} for c, a in parts]
    return out


def filing_path(stem: str) -> Path:
    return FILINGS / f"{stem}.pdf"


def run_whole_pdf(monkeypatch, out_dir: Path, filing, script: dict, *, pages=None,
                  template: str = HK_TEMPLATE, line_set: str = HK_SET,
                  whole_pdf_pages: str = "all", extraction: dict | None = None):
    """`run_extraction` on `filing` (a stem under testdata/filings, a path, or PDF bytes) with the
    whole-PDF route on and the stand-in, answering from `script`, as the provider.

    Returns `(doc, ctx, recorder)`; `recorder.calls` lists every request the run made (each
    whole-PDF one with the part it carried and the report pages that part holds), and the request
    files are under `out_dir/requests`.
    """
    from app.config import get_settings
    from app.ports.registry import registry
    from app.schemas.line_items import load_line_item_set
    from app.schemas.loader import load_template
    from app.services.documents import run_extraction
    from app.services.working_view import build_working_view

    if isinstance(filing, (bytes, bytearray)):
        raw, name = bytes(filing), "filing.pdf"
    else:
        path = Path(filing) if str(filing).endswith(".pdf") else filing_path(str(filing))
        raw, name = path.read_bytes(), path.name
    standin = standin_module()
    rec = standin.Recorder(Path(out_dir), takes_documents=True, whole_pdf_script=script,
                           filing_bytes=raw)
    settings = get_settings()
    for key, value in {"whole_pdf_to_model": True, "whole_pdf_pages": whole_pdf_pages,
                       "llm_disclosures": False, "llm_contingent_liabilities": False,
                       "llm_shared_figure_tiebreak": False, **(extraction or {})}.items():
        monkeypatch.setattr(settings.extraction, key, value)
    monkeypatch.setitem(registry._factories.setdefault("llm", {}), "standin", rec.provider)
    monkeypatch.setattr(settings.llm, "provider", "standin")
    cfg = load_line_item_set(json.loads((TEMPLATES / line_set).read_text(encoding="utf-8")),
                             resolve=True)
    tpl = load_template(json.loads((TEMPLATES / template).read_text(encoding="utf-8")))
    doc, ctx = run_extraction(raw, filename=name, ontology=build_working_view(cfg), template=tpl,
                              line_items=cfg,
                              included_pages=set(pages) if pages is not None else None)
    return doc, ctx, rec


def slot(doc, key: str, basis: str = "consolidated", period: str = "current"):
    """`(row, value)` of the first row carrying `key` with a figure in that slot, or (None, None)."""
    for row in doc.line_items:
        if row.canonical_key != key:
            continue
        for ev in (row.values or {}).values():
            b = str(getattr(ev.basis, "value", ev.basis))
            if b == basis and ev.period_label == period and ev.value is not None:
                return row, ev
    return None, None


def rows_of(doc, key: str) -> list:
    return [row for row in doc.line_items if row.canonical_key == key]
