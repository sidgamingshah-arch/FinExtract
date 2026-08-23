"""PDF → line items. Native pages use the PyMuPDF text layer; scanned pages route through
the configured OCR provider. Both converge on the shared word→line-item reconstruction, so
every value carries page + normalized-bbox provenance regardless of source.
"""
from __future__ import annotations

from app.core.models.enums import PageKind, PageSourceKind
from app.core.models.geometry import BBox
from app.core.stage import PipelineContext
from app.services.row_reconstruct import Word, build_line_items


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def text_rotation(page) -> int:
    """The dominant reading direction of the page's text, in degrees clockwise: 0, 90, 180 or 270.

    ``page.rect`` already accounts for a page's /Rotate attribute, so a page marked landscape
    needs nothing. What it does not cover is text DRAWN sideways on an upright page, which wide
    statements use — a statement of changes in equity with fourteen component columns is
    routinely printed rotated to fit. There the words are laid out bottom-to-top, so grouping
    them into rows by shared y finds no rows at all and the page yields nothing.

    The writing direction comes from the span's own ``dir`` unit vector, weighted by how much
    text is drawn that way, so a single rotated stamp or watermark cannot outvote the body.
    """
    weights: dict[int, float] = {}
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:                        # a malformed page must not stop extraction
        return 0
    for block in blocks:
        for line in block.get("lines", []):
            dx, dy = (line.get("dir") or (1.0, 0.0))[:2]
            chars = sum(len(sp.get("text", "")) for sp in line.get("spans", []))
            if not chars:
                continue
            if abs(dx) >= abs(dy):
                deg = 0 if dx >= 0 else 180
            else:
                deg = 270 if dy >= 0 else 90
            weights[deg] = weights.get(deg, 0.0) + chars
    if not weights:
        return 0
    return max(weights, key=lambda d: weights[d])


def _to_reading_space(box: BBox, rotation: int) -> BBox:
    """A page-space box expressed in reading space, for text drawn at ``rotation`` degrees.

    Reading space is where "down the page" is the direction successive lines advance and "across"
    is the direction words advance — which is what row grouping and column detection assume. The
    transform is the inverse rotation about the unit square, so the result stays normalized.
    """
    if rotation in (0, 360):
        return box
    if rotation == 90:                       # text runs bottom-to-top
        return BBox(x0=_clamp(box.y0), y0=_clamp(1.0 - box.x1),
                    x1=_clamp(box.y1), y1=_clamp(1.0 - box.x0))
    if rotation == 270:                      # text runs top-to-bottom
        return BBox(x0=_clamp(1.0 - box.y1), y0=_clamp(box.x0),
                    x1=_clamp(1.0 - box.y0), y1=_clamp(box.x1))
    return BBox(x0=_clamp(1.0 - box.x1), y0=_clamp(1.0 - box.y1),
                x1=_clamp(1.0 - box.x0), y1=_clamp(1.0 - box.y0))


def _native_words(page, w: float, h: float, rotation: int | None = None) -> list[Word]:
    rot = text_rotation(page) if rotation is None else rotation
    out: list[Word] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        if not text.strip():
            continue
        page_box = BBox(x0=_clamp(x0 / w), y0=_clamp(y0 / h),
                        x1=_clamp(x1 / w), y1=_clamp(y1 / h))
        if rot in (0, 360):
            out.append(Word(text=text, bbox=page_box))
        else:
            # Layout logic reads the rotated box; provenance keeps the page-space one so
            # click-to-source still highlights where the figure is actually drawn.
            out.append(Word(text=text, bbox=_to_reading_space(page_box, rot),
                            page_bbox=page_box))
    return out


def extract_pdf(data: bytes, doc, ctx: PipelineContext, *, scope=None,
                normalisation=None) -> int:
    """Extract line items from a PDF into ``doc.line_items``. Returns the count added.

    ``scope``/``normalisation`` are the reading rules of the rulebook the RUN was pinned to
    (``stages.extract.reconstruction_rules``). Omitting them falls back to the rulebook shipped as
    the one in force, which is what a run started without an ontology is read with — a run that
    does name a rulebook must be read by that one, or its pin only decided how its figures were
    mapped and not which column they came from.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        ctx.log("extract:pymupdf_missing")
        return 0
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # corruption already surfaced by the integrity stage
        ctx.log(f"extract:pdf_open_failed:{exc}")
        return 0

    # Prefer statement pages (face + notes). Also include SCANNED pages even if they didn't
    # classify — a scanned page has no text layer to match a title against, so it would
    # otherwise be dropped before ever reaching the OCR path. Fall back to all pages if
    # nothing was classified at all.
    targets = [p for p in doc.pages
               if p.kind in (PageKind.FACE, PageKind.NOTES)
               or p.source_kind == PageSourceKind.SCANNED]
    if not targets:
        targets = list(doc.pages)

    # Honour an explicit user page scope (from the Page Scope screen): keep only pages the
    # user chose to include. An empty selection is treated as "no restriction" so a stray
    # empty list can never silently extract nothing.
    if ctx.included_pages:
        scoped = [p for p in targets if p.index in ctx.included_pages]
        if scoped:
            targets = scoped
            ctx.log(f"extract:page_scope_applied={sorted(ctx.included_pages)}")

    number_format = _resolve_number_format(ctx, doc)
    ocr = None
    added = 0
    ordinal = len(doc.line_items)
    for ps in targets:
        if ps.index >= pdf.page_count:
            continue
        page = pdf[ps.index]
        rect = page.rect
        w, h = max(rect.width, 1.0), max(rect.height, 1.0)

        if ps.source_kind == PageSourceKind.SCANNED:
            if ocr is None:                      # resolve the OCR provider lazily, once
                ocr = _resolve_ocr(ctx)
            if ocr is None:
                ctx.log(f"extract:page={ps.index}:scanned_no_ocr")
                continue
            words = _ocr_words_for(page, ocr, ctx)
            source_kind = "ocr"
        else:
            # Text drawn sideways is read in reading space; the page records the angle so the
            # run is auditable and the viewer knows the page is not upright.
            rot = text_rotation(page)
            if rot:
                ps.rotation = rot
                ctx.log(f"extract:page={ps.index}:text_rotation={rot}")
            words = _native_words(page, w, h, rotation=rot)
            source_kind = "native"

        if not words:
            continue
        # Notes pages → note detail tables (the breakdowns behind the face figures); every
        # other page → face line items. Both keep page + bbox provenance.
        if ps.kind == PageKind.NOTES:
            from app.services.notes_extract import extract_note_tables

            tables = extract_note_tables(words, page_index=ps.index,
                                         document_id=doc.content_hash, source_kind=source_kind,
                                         scope=scope, normalisation=normalisation)
            doc.notes.extend(tables)
            continue
        # ``ps.statement`` (from the classifier) is what tells the reconstructor that a page is a
        # component matrix rather than a two-column comparative; ``ctx.log`` records the cases
        # where a matrix page could not be attributed and was skipped.
        #
        # ``ps.scope`` is the same classifier's verdict on WHOSE figures the page presents. It was
        # computed and dropped here, so a Company-only statement of financial position — which an
        # HKEX filing prints on its own page past the notes, with no column header naming an entity
        # — was reconstructed as the Group's and added to it under the same canonical keys.
        items, ordinal = build_line_items(
            words, page_index=ps.index, document_id=doc.content_hash,
            source_kind=source_kind, ordinal_start=ordinal, number_format=number_format,
            statement=ps.statement, log=ctx.log, scope=scope, normalisation=normalisation,
            page_scope=ps.scope)
        doc.line_items.extend(items)
        added += len(items)
    ctx.log(f"extract:pdf_line_items={added} note_tables={len(doc.notes)}")
    return added


def extract_image(data: bytes, doc, ctx: PipelineContext, *, scope=None,
                  normalisation=None) -> int:
    """Extract line items from a standalone image (PNG/JPG/TIFF) by sending the bytes straight
    to the configured OCR provider, then reconstructing rows exactly like a scanned PDF page.
    With no OCR engine configured (the default ``stub``), logs a clear 'OCR not configured'
    marker and adds nothing — never a silent empty success."""
    ocr = _resolve_ocr(ctx)
    if ocr is None:
        ctx.log("extract:image_no_ocr(configure an OCR engine; default is stub)")
        return 0
    lang = (ctx.settings.ocr.languages or ["en"])[0]
    try:
        result = ocr.recognize(data, lang=lang)
    except NotImplementedError:
        ctx.log("extract:image_ocr_not_implemented")
        return 0
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"extract:image_ocr_failed({exc})")
        return 0
    words = [Word(text=w["text"], bbox=w["bbox"]) for w in result.get("words", [])]
    if not words:
        ctx.log("extract:image_ocr_no_words")
        return 0
    items, _ = build_line_items(words, page_index=0, document_id=doc.content_hash,
                                source_kind="ocr", scope=scope, normalisation=normalisation)
    doc.line_items.extend(items)
    ctx.log(f"extract:image_line_items={len(items)}")
    return len(items)


def _resolve_number_format(ctx: PipelineContext, doc):
    """The locale ``NumberFormat`` for value parsing: the ontology's per-locale format keyed
    by the document's detected locale. Returns None for English or an unset locale so the fast
    US regex path is used unchanged — locale-aware parsing (EU decimal-comma, Indian grouping)
    activates only for a non-English document that has a declared format."""
    loc = doc.locale
    if not loc or loc == "en":
        return None
    ontology = getattr(ctx, "ontology", None)
    if ontology is None:
        return None
    by_locale = getattr(ontology, "number_format_by_locale", None) or {}
    return by_locale.get(loc)


def _resolve_ocr(ctx: PipelineContext):
    engine = ctx.settings.ocr.engine
    if engine == "stub":
        return None
    try:
        return ctx.registry.get("ocr", engine)
    except Exception as exc:
        ctx.log(f"extract:ocr_unavailable({exc})")
        return None


def _ocr_words_for(page, ocr, ctx: PipelineContext) -> list[Word]:
    """Rasterize a page and OCR it; OCR bboxes are already normalized (see OcrProvider)."""
    try:
        import fitz  # noqa: F401 - page already comes from fitz

        pix = page.get_pixmap(dpi=ctx.settings.ocr.dpi)
        png = pix.tobytes("png")
        result = ocr.recognize(png, lang=(ctx.settings.ocr.languages or ["en"])[0])
    except NotImplementedError:
        ctx.log("extract:ocr_not_implemented")
        return []
    except Exception as exc:
        ctx.log(f"extract:ocr_failed({exc})")
        return []
    return [Word(text=w["text"], bbox=w["bbox"]) for w in result.get("words", [])]
