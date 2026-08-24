/** Shared live document source-viewer: a scroll-through PDF page stack with bbox highlight,
 *  and a spreadsheet cell-context grid. Used by both the Extraction view and the Workspace so
 *  every extracted value — wherever it's shown — hyperlinks to its exact source location. */
import React, { useEffect, useRef, useState } from "react";

import { Card } from "./ui";
import { api } from "../lib/api";
import { useCellContext, useDocumentPages, useDocumentSearch } from "../lib/queries";
import { useT } from "../i18n";
import { color, font } from "../theme";
import type { DocSearchHit, ExtractionProvenance } from "../types";

/** A value's source location, resolved to what the Source panel needs to render it.
 *  PDF sources carry a page + bbox; spreadsheet sources carry a sheet + cell. */
export type Picked =
  | { kind: "pdf"; page_index: number; bbox: { x0: number; y0: number; x1: number; y1: number }; label: string }
  | { kind: "xlsx"; sheet: string; cell: string; label: string };

export type PdfPick = Extract<Picked, { kind: "pdf" }>;
export type XlsxPick = Extract<Picked, { kind: "xlsx" }>;

/** Resolve a provenance record to a Picked, or null if it isn't click-to-source-able. */
export function toPicked(p: ExtractionProvenance | null | undefined, label: string): Picked | null {
  if (!p) return null;
  if (p.source_kind === "spreadsheet" && p.sheet && p.cell)
    return { kind: "xlsx", sheet: p.sheet, cell: p.cell, label };
  if (p.bbox) return { kind: "pdf", page_index: p.page_index, bbox: p.bbox, label };
  return null;
}

/** Shared chrome for a source panel: sticky column with a heading and a card. */
export function PanelShell({ t, width = 420, children }:
  { t: (k: string) => string; width?: number; children: React.ReactNode }) {
  return (
    <div style={{ width, flex: `0 0 ${width}px`, position: "sticky", top: 0, alignSelf: "flex-start" }}>
      <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: 0.3, color: color.muted, margin: "0 0 8px" }}>
        {t("ex.col.source").toUpperCase()}
      </div>
      <Card pad={10}>{children}</Card>
    </div>
  );
}

/** One page slot in the scrollable document viewer. Lazily fetches its PNG when it nears
 *  the viewport (so a 200-page filing doesn't fetch every page at once), and draws the
 *  picked value's bounding box when the pick lands on this page. */
function PageSlot({ documentId, index, picked, pickedRef, printed }: {
  documentId: string; index: number; picked: PdfPick | null;
  pickedRef: React.MutableRefObject<HTMLDivElement | null>;
  /** The folio printed on this page, when the document prints one. */
  printed?: string | null;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const isPicked = picked?.page_index === index;
  // Eagerly load the first pages; also load the picked page immediately (don't wait for the
  // scroll→IntersectionObserver chain — that's what made far-page click-to-source look dead).
  const [visible, setVisible] = useState(index < 2 || isPicked);
  const holder = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (isPicked) setVisible(true);
  }, [isPicked]);

  useEffect(() => {
    const el = holder.current;
    if (!el || visible) return;
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: "400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    let objUrl: string | null = null;
    let cancelled = false;
    api.fetchPageImage(documentId, index)
      .then((blob) => { if (!cancelled) { objUrl = URL.createObjectURL(blob); setUrl(objUrl); } })
      .catch(() => {});
    return () => { cancelled = true; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [visible, documentId, index]);

  // Once the picked page's real image is in (its true height is known), re-center on it so the
  // highlight lands in view — the first scroll only had the placeholder's height to go on.
  useEffect(() => {
    if (isPicked && url && holder.current) {
      holder.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [isPicked, url]);

  const b = isPicked ? picked!.bbox : null;
  return (
    <div
      ref={(el) => { holder.current = el; if (isPicked) pickedRef.current = el; }}
      style={{
        position: "relative", marginBottom: 10,
        border: `1px solid ${isPicked ? color.amberFg : color.hairline3}`,
        borderRadius: 6, overflow: "hidden", background: "#fafbfc",
      }}
    >
      <div style={{ position: "absolute", top: 4, right: 6, zIndex: 1, fontFamily: font.mono,
                    fontSize: 9.5, color: color.faint, background: "rgba(255,255,255,0.8)",
                    borderRadius: 4, padding: "1px 5px" }}>
        {/* BOTH numbers, because they are both true and they disagree. `index + 1` is the page's
            position in the file — what this viewer scrolls to, what provenance carries, what the
            Page Scope screen selects — and `printed` is the folio on the paper, which runs behind
            it by however much front matter the report has. Showing only the first makes a correct
            jump look like it landed two pages down. */}
        p.{index + 1}{printed ? ` · ${printed}` : ""}
      </div>
      {url
        ? <img src={url} alt="" style={{ display: "block", width: "100%" }} />
        // Reserve page-shaped space (A4 portrait ≈ 1:1.414) so the panel doesn't grow/shift as
        // images stream in — a page slot occupies its final footprint before its PNG arrives.
        : <div style={{ width: "100%", aspectRatio: "1 / 1.414", background: color.rowAltBg }} />}
      {url && b && (
        <div data-testid="prov-highlight" style={{
          position: "absolute",
          left: `${b.x0 * 100}%`, top: `${b.y0 * 100}%`,
          width: `${(b.x1 - b.x0) * 100}%`, height: `${(b.y1 - b.y0) * 100}%`,
          border: `2px solid ${color.amberFg}`, background: "rgba(217,164,65,0.22)",
          borderRadius: 2, boxShadow: "0 0 0 1px rgba(0,0,0,0.05)",
        }} />
      )}
    </div>
  );
}

/** Bare scrollable stack of the document's pages — no panel chrome, so a host (the Workspace's
 *  dark viewer column) can place it in its own layout. Picking a value scrolls its page into
 *  view and highlights the bbox. */
export function PageStack({ documentId, pageCount, picked, maxHeight = "78vh", scale = 1,
                           search = true }: {
  documentId: string; pageCount: number; picked: PdfPick | null; maxHeight?: number | string;
  /** Page-column width multiplier — what a zoom IS here. Each page's image is width-driven and
   *  the bbox highlight is expressed in percentages of the page box, so widening the column
   *  scales the page and its highlight together and provenance geometry is untouched. Default 1
   *  leaves every other caller (the Extraction view, the Notes viewer) exactly as it was. */
  scale?: number;
  /** Offer the find-in-document bar. On by default: every host of this stack shows a source
   *  document the reader may need to reach by what it SAYS rather than by which value they
   *  happened to click. */
  search?: boolean;
}) {
  const pickedRef = useRef<HTMLDivElement | null>(null);
  // A search hit is a pick like any other, but it belongs to the viewer rather than to the host's
  // selection — clicking a hit must not move the grid's selected row. It is cleared whenever the
  // host picks something, so the two can never both claim the highlight.
  const [hit, setHit] = useState<PdfPick | null>(null);
  useEffect(() => { setHit(null); }, [picked?.page_index, picked?.bbox.x0, picked?.bbox.y0]);
  const shown = hit ?? picked;

  useEffect(() => {
    if (shown && pickedRef.current) {
      pickedRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [shown?.page_index, shown?.bbox.x0, shown?.bbox.y0]);

  // The printed folios, from the same per-page payload the Page Scope screen reads (cached, so
  // hosting the viewer beside that screen costs nothing extra).
  const pages = useDocumentPages(documentId);
  const folio = (i: number) => pages.data?.pages?.find((p) => p.no === i + 1)?.printed ?? null;

  const n = Math.max(1, pageCount);
  return (
    <div style={{ maxHeight, overflow: "auto", paddingRight: 4 }}>
      {search && <PageSearch documentId={documentId} onPick={setHit} />}
      {/* `overflow: auto` above rather than overflowY, so a page column widened past 100% can be
          panned horizontally instead of being clipped. */}
      <div style={{ width: `${scale * 100}%` }}>
        {Array.from({ length: n }).map((_, i) => (
          <PageSlot key={i} documentId={documentId} index={i} picked={shown}
                    pickedRef={pickedRef} printed={folio(i)} />
        ))}
      </div>
    </div>
  );
}

/** Find-in-document: the text search the page-image viewer cannot do for itself.
 *
 *  The term is submitted rather than searched as it is typed — one request per query, not one per
 *  keystroke — and each hit carries its page and a normalized box, so picking one highlights the
 *  phrase with exactly the overlay a picked value gets. */
function PageSearch({ documentId, onPick }: {
  documentId: string; onPick: (p: PdfPick) => void;
}) {
  const t = useT();
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const q = useDocumentSearch(documentId, query);
  const hits = q.data?.hits ?? [];

  const go = (i: number) => {
    const h: DocSearchHit | undefined = hits[i];
    if (!h) return;
    setAt(i);
    onPick({ kind: "pdf", page_index: h.page_index, bbox: h.bbox, label: h.snippet });
  };
  // Land on the first hit as soon as a search returns, so one Enter is the whole interaction.
  useEffect(() => {
    if (q.data && hits.length) go(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setAt(0);
    setQuery(term.trim());
  };
  const step = (d: number) => {
    if (!hits.length) return;
    go((at + d + hits.length) % hits.length);
  };

  return (
    // Opaque and sticky: the bar has to stay legible over the page images scrolling under it, and
    // over the Workspace's dark viewer column as well as the Extraction view's white card.
    <div style={{ position: "sticky", top: 0, zIndex: 2, background: "#fff",
                  borderBottom: `1px solid ${color.cardBorder}`, borderRadius: 6,
                  padding: "6px 6px 8px", marginBottom: 8 }}>
      <form onSubmit={submit} style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <input
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          placeholder={t("view.searchPlaceholder")}
          aria-label={t("view.searchPlaceholder")}
          data-testid="page-search"
          style={{ flex: 1, minWidth: 0, fontSize: 11.5, padding: "5px 8px",
                   border: `1px solid ${color.cardBorder}`, borderRadius: 6,
                   background: "#fff", color: color.sec }}
        />
        {hits.length > 1 && (
          <>
            <button type="button" onClick={() => step(-1)} title={t("view.prevMatch")}
                    style={stepSt}>↑</button>
            <button type="button" onClick={() => step(1)} title={t("view.nextMatch")}
                    style={stepSt}>↓</button>
          </>
        )}
      </form>
      {query.length >= 2 && (
        <div style={{ fontSize: 10.5, color: color.muted, marginTop: 5, lineHeight: 1.5 }}>
          {q.isPending && t("view.searching")}
          {q.isError && t("view.searchFailed")}
          {q.data && (hits.length
            ? <>
                <span data-testid="page-search-count">
                  {`${at + 1} / ${hits.length}${q.data.truncated ? "+" : ""} ${t("view.matches")}`}
                </span>
                {hits[at] && (
                  <span style={{ color: color.faint }}>
                    {` · p.${hits[at].page_index + 1}`}
                    {hits[at].printed_page ? ` · ${hits[at].printed_page}` : ""}
                  </span>
                )}
              </>
            : t("view.noMatches"))}
          {/* A page with no text layer cannot be searched, and that is not the same as a page with
              no match on it. The count of them is stated rather than left to look like an answer. */}
          {q.data && q.data.scanned_pages > 0 && (
            <div>{`${q.data.scanned_pages} ${t("view.scannedUnsearchable")}`}</div>
          )}
        </div>
      )}
    </div>
  );
}

const stepSt: React.CSSProperties = {
  border: `1px solid ${color.cardBorder}`, background: "#fff", color: color.sec,
  borderRadius: 6, width: 24, height: 24, fontSize: 11, cursor: "pointer", padding: 0,
};

/** The full source document panel (Extraction view): panel chrome + the page stack. */
export function PagedSource({ documentId, pageCount, picked, t, width }: {
  documentId: string; pageCount: number; picked: PdfPick | null; t: (k: string) => string; width?: number;
}) {
  const n = Math.max(1, pageCount);
  return (
    <PanelShell t={t} width={width}>
      <div style={{ fontSize: 11, color: color.sec2, marginBottom: 8 }}>
        {picked ? `${picked.label} · p.${picked.page_index + 1}` : `${n} ${t("ex.pagesLabel")}`}
      </div>
      <PageStack documentId={documentId} pageCount={pageCount} picked={picked} />
    </PanelShell>
  );
}

/** Bare spreadsheet cell-context grid — no panel chrome (for hosting in the Workspace column).
 *  Renders a small window of cells around the value's origin with the target highlighted. */
export function ExcelGrid({ documentId, picked, t }: {
  documentId: string; picked: XlsxPick | null; t: (k: string) => string;
}) {
  const q = useCellContext(documentId, picked?.sheet, picked?.cell);
  return (
    <>
      {!picked && (
        <div style={{ fontSize: 12, color: color.muted, padding: "24px 8px", textAlign: "center" }}>
          {t("ex.pickHint")}
        </div>
      )}
      {picked && q.isError && <div style={{ fontSize: 12, color: color.redFg }}>{t("ex.failed")}</div>}
      {picked && q.isPending && !q.isError && (
        <div style={{ fontSize: 12, color: color.muted, padding: "18px 8px" }}>{t("ex.running")}</div>
      )}
      {picked && q.data && (
        <>
          <div style={{ fontSize: 11, color: color.sec2, marginBottom: 8 }}>
            {picked.label} · <span style={{ fontFamily: font.mono }}>{q.data.sheet}!{q.data.target}</span>
          </div>
          <div style={{ overflowX: "auto", border: `1px solid ${color.hairline3}`, borderRadius: 6 }}>
            <table style={{ borderCollapse: "collapse", fontSize: 10.5, fontFamily: font.mono, width: "100%" }}>
              <thead>
                <tr>
                  <th style={cellSt(false, true)}></th>
                  {q.data.col_letters.map((c) => (
                    <th key={c} style={cellSt(false, true)}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {q.data.grid.map((line, r) => (
                  <tr key={r}>
                    <td style={cellSt(false, true)}>{q.data!.row_numbers[r]}</td>
                    {line.map((cell) => (
                      <td key={cell.ref}
                          data-testid={cell.is_target ? "cell-target" : undefined}
                          title={cell.ref}
                          style={{
                            ...cellSt(cell.is_target, false),
                            textAlign: cell.numeric ? "right" : "left",
                          }}>
                        {cell.value.length > 22 ? cell.value.slice(0, 21) + "…" : cell.value}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

/** Spreadsheet click-to-source panel (Extraction view): panel chrome + the cell grid. */
export function ExcelSourcePanel({ documentId, picked, t, width }: {
  documentId: string; picked: XlsxPick | null; t: (k: string) => string; width?: number;
}) {
  return (
    <PanelShell t={t} width={width}>
      <ExcelGrid documentId={documentId} picked={picked} t={t} />
    </PanelShell>
  );
}

function cellSt(target: boolean, header: boolean): React.CSSProperties {
  return {
    border: `1px solid ${color.hairline2}`,
    padding: "3px 6px",
    whiteSpace: "nowrap",
    maxWidth: 130,
    overflow: "hidden",
    textOverflow: "ellipsis",
    background: target ? "rgba(217,164,65,0.22)" : header ? color.rowAltBg : undefined,
    color: header ? color.muted : color.ink,
    fontWeight: target ? 700 : header ? 600 : 400,
    outline: target ? `2px solid ${color.amberFg}` : undefined,
  };
}
