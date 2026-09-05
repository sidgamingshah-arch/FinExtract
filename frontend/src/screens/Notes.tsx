/** Screen 6 — All Notes. Left index of extracted notes; right detail with a
 * particulars grid and a note-to-face reconciliation callout. */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { Card, ConfidencePill } from "../components/ui";
import { PageStack, type PdfPick } from "../components/SourceViewer";
import { SCREENS } from "./config";
import { useT } from "../i18n";
import { useDocumentNote, useDocumentNotes, useDocumentRun, useNote, useNotes, useProjectLoaded } from "../lib/queries";
import { EmptyState } from "../components/EmptyState";
import { usePinnedRun, useUI } from "../store";
import { color, confStyle, fmtIN, font } from "../theme";
import type { NoteDetail, NoteDetailRow } from "../types";

const GRID = "1fr 120px 120px 64px";

function Loading() {
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: color.muted }}>
      Loading…
    </div>
  );
}

function DetailRow({ row }: { row: NoteDetailRow }) {
  const isSub = row.kind === "sub";
  const isTot = row.kind === "tot";
  const wt = isSub || isTot ? 600 : 400;
  const fg = isTot ? color.indigo : color.ink;
  const bg = isTot ? color.indigoTint2 : isSub ? color.rowAltBg : color.surface;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: GRID,
        alignItems: "center",
        padding: "10px 18px",
        borderBottom: `1px solid ${color.hairline}`,
        background: bg,
      }}
    >
      <span style={{ fontSize: 12.5, fontWeight: wt, color: fg }}>{row.label}</span>
      <span style={{ textAlign: "right", fontFamily: font.mono, fontSize: 12, fontWeight: wt, color: fg }}>
        {fmtIN(row.v1)}
      </span>
      <span style={{ textAlign: "right", fontFamily: font.mono, fontSize: 12, color: color.muted }}>
        {fmtIN(row.v2)}
      </span>
      {/* Under the CONF. header, the row's OWN measured mapping confidence. It used to print
          `confStyle(cat).pct` — 96/78/54 per bucket — so every 'high' row read "96%" whatever its
          real score, and a row the extractor never scored read "78%": a band's stand-in figure
          presented as a measurement of the line it sits beside. The pill now prints the served
          percentage, and names the band instead when the payload carries no number — which is the
          case for the seeded sample, whose rows record a band and were never scored. */}
      <span style={{ textAlign: "right" }}>
        {row.conf && <ConfidencePill cat={row.conf} pct={row.conf_pct} testid="note-conf" />}
      </span>
    </div>
  );
}

function StructuredTable({ table }: { table: NonNullable<NoteDetail["table"]> }) {
  const columns = `minmax(190px, 2fr) repeat(${table.columns.length}, minmax(94px, 1fr))`;
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: columns, padding: "10px 18px",
                    background: color.rowAltBg, borderBottom: `1px solid ${color.hairline}`,
                    fontSize: 10.5, fontWeight: 600, color: color.muted }}>
        <span>PARTICULARS</span>
        {table.columns.map((column) => <span key={column} style={{ textAlign: "right" }}>{column}</span>)}
      </div>
      {table.rows.map((row, index) => (
        <div key={`${row.section ?? ""}-${row.label}-${index}`} style={{ display: "grid", gridTemplateColumns: columns,
          alignItems: "center", padding: "10px 18px", borderBottom: `1px solid ${color.hairline}` }}>
          <span style={{ fontSize: 12.5, color: color.ink }}>
            {row.section ? `${row.section}: ${row.label}` : row.label}
          </span>
          {table.columns.map((column) => (
            <span key={column} style={{ textAlign: "right", fontFamily: font.mono, fontSize: 12,
              color: color.sec2 }}>{row.values[column] ?? "-"}</span>
          ))}
        </div>
      ))}
    </>
  );
}

/** How the note's location reads: "276" for a note on one page, "276-277" when it runs on. A note
 *  continued across pages is ONE note, and printing only its first page made the pane disagree with
 *  what the viewer beside it was showing. First and last only — a run of pages is contiguous, and
 *  listing every one of note 38's seven is noise. */
function pageSpan(detail: NoteDetail): string {
  // THE FOLIOS WHEN THE FILING PRINTED THEM, because a citation exists so a person can go and
  // look, and the only number they can look up is the one on the paper. The sheet positions are
  // the fallback and stay untouched in `detail.page`/`pages`, which the viewer navigates by.
  //
  // Compared as SHEET positions either way. A folio is a string and sorts lexicographically
  // ('100' < '99'), so `printed_pages` is already in the order its sheet pages were sorted into
  // and its ends are simply read off — never re-compared.
  const folios = detail.printed_pages ?? [];
  if (folios.length) {
    const first = folios[0];
    const last = folios[folios.length - 1];
    return last !== first ? `${first}-${last}` : `${first}`;
  }
  const pages = detail.pages && detail.pages.length ? detail.pages : [detail.page];
  const first = pages[0];
  const last = pages[pages.length - 1];
  return last > first ? `${first}-${last}` : `${first}`;
}

function Detail({ detail }: { detail: NoteDetail }) {
  const navigate = useNavigate();
  const t = useT();
  // The two column headers were the literals "FY25"/"FY24" while the Workspace showed the
  // filing's real period labels, so on a 2023/2022 filing the two screens labelled the same
  // figure differently. They now come from the note's own value lists — the same lists v1/v2 were
  // resolved from — and fall back to a localized Current/Prior only when the source named no
  // columns, because an empty header cell says less than "Current" does.
  const per = (i: number) =>
    detail.periods?.[i]?.trim() || t(i === 0 ? "n.periodCurrent" : "n.periodPrior");
  const structuredTable = detail.table && detail.table.columns.length > 2 ? detail.table : null;
  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <span style={{ fontFamily: font.mono, fontSize: 13, fontWeight: 600, color: color.indigo }}>
          {t("n.note")} {detail.no}
        </span>
        <h1 style={{ fontSize: 19, fontWeight: 600, margin: 0 }}>{detail.title}</h1>
      </div>
      <div style={{ fontSize: 12, color: color.muted, marginBottom: 18 }}>
        {t("n.extractedFrom").replace("{p}", pageSpan(detail))} {t("n.linked")}{" "}
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate(SCREENS.workspace.path);
          }}
          style={{ color: color.indigo }}
        >
          {detail.linked_label}
        </a>{" "}
        {t("n.onFace")}
      </div>
      <Card pad={0} style={{ overflow: "hidden" }}>
        {structuredTable ? <StructuredTable table={structuredTable} /> : <><div
          style={{
            display: "grid",
            gridTemplateColumns: GRID,
            padding: "10px 18px",
            background: color.rowAltBg,
            borderBottom: "1px solid #e8eaee",
            fontSize: 10.5,
            fontWeight: 600,
            color: color.muted,
          }}
        >
          <span>{t("n.particulars")}</span>
          <span data-testid="note-period" style={{ textAlign: "right" }}>{per(0)}</span>
          <span data-testid="note-period" style={{ textAlign: "right" }}>{per(1)}</span>
          <span style={{ textAlign: "right" }}>{t("n.conf")}</span>
        </div>
        {detail.rows.map((r, i) => (
          <DetailRow key={i} row={r} />
        ))}
        </>}
      </Card>
      {detail.reconciliation && (
        <div
          style={{
            marginTop: 14,
            padding: "13px 15px",
            background: color.indigoTint,
            border: `1px solid ${color.indigoBorder}`,
            borderRadius: 10,
            fontSize: 12,
            color: color.indigo,
            lineHeight: 1.55,
          }}
        >
          <b>{t("n.reconciliation")}</b> {detail.reconciliation}
        </div>
      )}
    </div>
  );
}

export default function NotesScreen() {
  const t = useT();
  const locale = useUI((s) => s.locale);
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  const loaded = useProjectLoaded();
  const { note, setNote } = useUI();
  // Real document → notes from its extraction (line-item note references); else the demo.
  // The same run the workspace grid is pinned to: a note read from the latest run beside a
  // historical statement is a different extraction wearing the same heading.
  const pinnedRunId = usePinnedRun();
  const realNotes = useDocumentNotes(activeDocumentId ?? undefined, pinnedRunId);
  const realDetail = useDocumentNote(activeDocumentId ?? undefined, note, locale, pinnedRunId);
  const demoNotes = useNotes(locale, !usingReal);
  const demoDetail = useNote(note, locale, !usingReal);
  const notes = usingReal ? realNotes.data : demoNotes.data;
  const detail = usingReal ? realDetail.data : demoDetail.data;
  // Real native-PDF docs get a side-by-side annual-report view, open by default, framed to
  // the selected note's page (no note-level bbox → the whole page is highlighted, not a region).
  const run = useDocumentRun(activeDocumentId ?? undefined);
  const result = run.data?.result;
  const showViewer = usingReal && result?.format === "pdf" && !!activeDocumentId;
  const picked: PdfPick | null =
    showViewer && detail && detail.page > 0
      ? { kind: "pdf", page_index: detail.page - 1, bbox: { x0: 0, y0: 0, x1: 1, y1: 1 }, label: detail.title }
      : null;

  // On a real doc, if the remembered note isn't one this document has, select the first.
  useEffect(() => {
    if (usingReal && notes?.notes.length && !notes.notes.some((n) => n.no === note)) {
      setNote(notes.notes[0].no);
    }
  }, [usingReal, notes, note, setNote]);

  if (!usingReal && !loaded) return <EmptyState />;
  if (usingReal && realNotes.isError) return <EmptyState />;
  if (!notes) return <Loading />;

  return (
    <div style={{ display: "flex", height: "100%", minHeight: 0 }}>
      <div
        style={{
          width: 290,
          flex: "0 0 290px",
          borderRight: `1px solid ${color.cardBorder}`,
          background: color.surface,
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <div style={{ padding: "16px 16px 11px", flex: "0 0 auto" }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 3px" }}>{t("n.heading")}</h2>
          <div style={{ fontSize: 11.5, color: color.muted }}>
            {notes.count} {t("n.notes")} · {t("n.linkedTo")} {notes.linked} {t("n.lineItems")}
          </div>
          <div
            style={{
              marginTop: 11,
              display: "flex",
              alignItems: "center",
              gap: 7,
              border: `1px solid ${color.cardBorder}`,
              borderRadius: 8,
              padding: "7px 10px",
            }}
          >
            <span style={{ color: color.faint }}>⌕</span>
            <span style={{ fontSize: 12, color: color.faint }}>{t("n.search")}</span>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "0 8px 12px" }}>
          {notes.notes.map((n) => {
            const sel = n.no === note;
            // The index prints no confidence figure — only a coloured dot — so the CATEGORY is all
            // it needs, and colour is all confStyle offers. Anything numeric here must come from
            // the note's own served percentage, not from the bucket.
            const cc = confStyle(n.conf);
            return (
              <div
                key={n.no}
                onClick={() => setNote(n.no)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "9px 10px",
                  borderRadius: 8,
                  cursor: "pointer",
                  background: sel ? color.indigoTint : "transparent",
                  marginBottom: 2,
                }}
              >
                <span
                  style={{
                    fontFamily: font.mono,
                    fontSize: 11,
                    fontWeight: 600,
                    color: sel ? color.indigo : color.muted,
                    minWidth: 26,
                  }}
                >
                  N{n.no}
                </span>
                <span
                  style={{
                    flex: 1,
                    fontSize: 12,
                    fontWeight: sel ? 600 : 500,
                    color: sel ? color.indigo : color.ink2,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {n.title}
                </span>
                <span style={{ width: 7, height: 7, borderRadius: "50%", background: cc.fg }} />
              </div>
            );
          })}
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 0, display: "flex", minHeight: 0 }}>
        <div style={{ flex: 1, minWidth: 0, overflowY: "auto", padding: "26px 34px" }}>
          {detail ? <Detail detail={detail} /> : <Loading />}
        </div>
        {showViewer && activeDocumentId && (
          <div
            style={{
              flex: "0 0 42%",
              minWidth: 0,
              borderLeft: `1px solid ${color.cardBorder}`,
              background: color.viewerBg,
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 12px",
                background: color.viewerHeader,
                color: "#dfe3e9",
                flex: "0 0 auto",
                fontSize: 11.5,
                fontWeight: 600,
              }}
            >
              <span>{t("n.sourceView")}</span>
              {detail && detail.page > 0 && (
                <span style={{ color: "#aab1bc", fontWeight: 400 }}>
                  · {t("n.sourcePage").replace("{p}", pageSpan(detail))}
                </span>
              )}
            </div>
            <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 12, background: "#e9ebef" }}>
              <PageStack
                documentId={activeDocumentId}
                // Fall back to the note's own page when the run omits a page count, so a note
                // beyond page 1 still renders (PageStack draws pages 0…n-1).
                pageCount={Math.max(result?.page_count ?? 1,
                                    detail?.pages?.[detail.pages.length - 1] ?? detail?.page ?? 1)}
                picked={picked}
                maxHeight="100%"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
