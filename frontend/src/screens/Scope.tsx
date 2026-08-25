/** Screen 3 — Statement page detection. Mirrors the wireframe's scrDetect(). */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button, Toggle, confReadout } from "../components/ui";
import { EmptyState } from "../components/EmptyState";
import { pageNames } from "../components/SourceViewer";
import { SCREENS } from "./config";
import {
  useDocumentPages, usePages, useProjectLoaded, useSetDocumentScope,
} from "../lib/queries";
import { useAppLocale, useUI } from "../store";
import { useT } from "../i18n";
import { useCan } from "../lib/rbac";
import { api } from "../lib/api";
import { color, confStyle, font, layout, radius } from "../theme";
import type { PageCard } from "../types";

/** Rendered preview of a single PDF page (side-by-side with the page grid). Fetches the PNG
 *  directly (like ExtractionView's PageSlot) — no bbox overlay needed here. */
function PagePreview({ docId, pageIndex, folio, t, onClose }:
  { docId: string; pageIndex: number; folio?: string | null;
    t: (k: string) => string; onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let obj: string | null = null;
    let cancelled = false;
    setUrl(null);
    api.fetchPageImage(docId, pageIndex)
      .then((blob) => { if (!cancelled) { obj = URL.createObjectURL(blob); setUrl(obj); } })
      .catch(() => {});
    return () => { cancelled = true; if (obj) URL.revokeObjectURL(obj); };
  }, [docId, pageIndex]);
  return (
    <div style={{ flex: "0 0 360px", position: "sticky", top: 0, alignSelf: "flex-start" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>
          {t("sc.preview")} · {pageNames(t, pageIndex, folio).badge}
        </span>
        <button onClick={onClose}
          style={{ fontSize: 11, fontWeight: 600, color: color.sec, background: "none",
                   border: `1px solid ${color.controlBorder}`, borderRadius: 6, padding: "3px 9px", cursor: "pointer" }}>
          {t("sc.previewClose")}
        </button>
      </div>
      <div style={{ border: `1px solid ${color.cardBorder}`, borderRadius: radius.cardSm,
                    overflow: "hidden", background: "#fff", minHeight: 200 }}>
        {url
          ? <img src={url} alt="" style={{ display: "block", width: "100%" }} />
          : <div style={{ padding: 40, textAlign: "center", fontSize: 11, color: color.faint }}>…</div>}
      </div>
    </div>
  );
}

function PageCardTile(
  { p, t, canScope, included, onToggle, onSelect, selected }:
  { p: PageCard; t: (key: string) => string; canScope: boolean;
    included: boolean; onToggle?: () => void; onSelect?: () => void; selected?: boolean },
) {
  const cc = confStyle(p.conf);
  // The classifier's own confidence in this page's class. It used to print `confStyle(cat).pct` —
  // the bucket's representative literal — so a page the classifier scored 0.40 announced "54%" and
  // a page it never scored announced "78%", both as measurements of the very page they sat on. The
  // served percentage is printed instead; with no percentage the tile names the BAND ("Medium"), in
  // muted chrome, because a band is what the payload actually knows and a word cannot be misread as
  // a measurement of this page.
  const conf = confReadout({ cat: p.conf, pct: p.conf_pct }, t);
  const scanned = p.scan === "scanned";
  return (
    <div
      style={{
        background: color.surface,
        border: `1.5px solid ${selected ? color.indigo : included ? color.indigoBorder2 : color.cardBorder}`,
        boxShadow: selected ? `0 0 0 2px ${color.indigoTint}` : undefined,
        borderRadius: radius.cardSm,
        overflow: "hidden",
      }}
    >
      {/* Thumbnail — click to open the rendered preview (real documents only). */}
      <div
        onClick={onSelect}
        data-testid={onSelect ? "scope-page" : undefined}
        style={{
          height: 118,
          background: "#f6f7f9",
          position: "relative",
          padding: "11px 12px",
          borderBottom: `1px solid ${color.hairline3}`,
          cursor: onSelect ? "pointer" : "default",
        }}
      >
        <div style={{ height: 6, width: "60%", background: "#dde1e7", borderRadius: 2, marginBottom: 7 }} />
        <div style={{ height: 4, width: "88%", background: color.trackBg, borderRadius: 2, marginBottom: 4 }} />
        <div style={{ height: 4, width: "80%", background: color.trackBg, borderRadius: 2, marginBottom: 4 }} />
        <div style={{ height: 4, width: "90%", background: color.trackBg, borderRadius: 2, marginBottom: 4 }} />
        <div style={{ height: 4, width: "72%", background: color.trackBg, borderRadius: 2 }} />
        <span
          style={{
            position: "absolute",
            top: 8,
            right: 9,
            fontSize: 9.5,
            fontWeight: 600,
            padding: "2px 6px",
            borderRadius: 5,
            background: scanned ? color.redBg : color.greenBg2,
            color: scanned ? color.redFg : color.greenFg,
          }}
        >
          {scanned ? t("sc.scan") : t("sc.native")}
        </span>
        <span
          style={{
            position: "absolute",
            bottom: 8,
            right: 9,
            fontFamily: font.mono,
            fontSize: 10,
            color: color.faint,
          }}
        >
          {/* Both numbers, labelled, exactly as the viewer's own badge does it — this tile IS a
              page, so the position is meaningful here, and the folio is what the tile's image
              shows. `p.printed` was already in this payload and was being dropped. */}
          {pageNames(t, p.no - 1, p.printed).badge}
        </span>
      </div>
      {/* Body */}
      <div style={{ padding: "9px 11px" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 5,
          }}
        >
          <span style={{ fontSize: 11.5, fontWeight: 600, color: included ? color.ink : color.muted2 }}>
            {p.cls}
          </span>
          <span
            data-testid="scope-conf"
            data-measured={String(conf.measured)}
            title={conf.title}
            style={{
              fontSize: 10,
              fontFamily: conf.measured ? font.mono : font.sans,
              fontStyle: conf.measured ? undefined : "italic",
              color: conf.measured ? cc.fg : color.muted,
            }}
          >
            {conf.text}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 10.5, color: color.muted2 }}>
            {included ? t("sc.inScope") : t("sc.skipped")}
          </span>
          <span
            onClick={canScope && onToggle ? onToggle : undefined}
            style={canScope && onToggle ? { cursor: "pointer" } : { pointerEvents: "none", opacity: 0.5 }}
          >
            <Toggle on={included} />
          </span>
        </div>
      </div>
    </div>
  );
}

export default function ScopeScreen() {
  const nav = useNavigate();
  const t = useT();
  const locale = useAppLocale();
  const canScope = useCan("config:scope");
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  const loaded = useProjectLoaded();
  const realQ = useDocumentPages(activeDocumentId ?? undefined);
  const demoQ = usePages(locale, !usingReal);
  const data = usingReal ? realQ.data : demoQ.data;
  const isPending = usingReal ? realQ.isPending : demoQ.isPending;
  const setScope = useSetDocumentScope(activeDocumentId ?? undefined);

  // Local selection of INCLUDED page indices (0-based), synced from the fetched pages. On a
  // real document, toggling persists the scope so extraction restricts itself to it.
  const [selected, setSelected] = useState<Set<number> | null>(null);
  const [filter, setFilter] = useState(0);   // active Face/Notes/Other filter chip (0 = All)
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);  // 0-based page in preview
  useEffect(() => {
    if (data) {
      setSelected(new Set(data.pages.filter((p) => p.included).map((p) => p.no - 1)));
    }
  }, [data]);

  const toggle = (idx: number) => {
    setSelected((prev) => {
      const next = new Set(prev ?? []);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      if (usingReal) setScope.mutate([...next]);
      return next;
    });
  };

  if (!usingReal && !loaded) return <EmptyState />;
  if (usingReal && realQ.isError) return <EmptyState />;
  if (isPending || !data) {
    return (
      <div style={{ padding: 60, textAlign: "center", color: color.muted }}>Loading…</div>
    );
  }

  // Focused = the live local selection (so counts move with the toggles); fall back to the
  // server's figure until the first sync.
  const focused = selected ? selected.size : data.focused;

  // Face / Notes / Other chips act as filters over the page grid. Filter index → page kind
  // (0 = all). Cards without a kind (demo pages) are always shown.
  const FILTER_KIND: (string | null)[] = [null, "face", "notes", "other"];
  const activeKind = FILTER_KIND[Math.min(filter, FILTER_KIND.length - 1)];
  const visiblePages = data.pages.filter((p) => !activeKind || !p.kind || p.kind === activeKind);

  return (
    <div style={{ maxWidth: layout.screenMaxWide, margin: "0 auto", padding: "26px 30px 60px" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("sc.title")}</h1>
          <p style={{ margin: 0, color: color.sec2 }}>{t("sc.subhead")}</p>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>
            {t("sc.focused")} <span style={{ color: color.indigo }}>{focused} of {data.total}</span> {t("sc.pages")}
          </div>
          <div style={{ fontSize: 11, color: color.muted }}>{data.total - focused} {t("sc.pagesSkipped")}</div>
        </div>
      </div>

      {/* Filter chips */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {data.filters.map((f, i) => {
          const active = i === filter;
          return (
            <span
              key={f.label}
              onClick={() => setFilter(i)}
              style={{
                fontSize: 11.5,
                fontWeight: 600,
                padding: "6px 12px",
                borderRadius: radius.pill,
                border: `1px solid ${active ? color.stepperActive : color.controlBorder}`,
                background: active ? color.stepperActive : color.surface,
                color: active ? "#fff" : color.sec,
                cursor: "pointer",
              }}
            >
              {f.label} <span style={{ opacity: 0.6 }}>{f.count}</span>
            </span>
          );
        })}
      </div>

      {/* Page grid (+ side-by-side rendered preview when a page is selected on a real doc) */}
      <div style={{ display: "flex", gap: 18, alignItems: "flex-start" }}>
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill,minmax(158px,1fr))",
            gap: 14,
          }}
        >
          {visiblePages.map((p) => (
            <PageCardTile
              key={p.no}
              p={p}
              t={t}
              canScope={canScope && usingReal}
              included={selected ? selected.has(p.no - 1) : p.included}
              onToggle={() => toggle(p.no - 1)}
              onSelect={usingReal ? () => setPreviewIndex(p.no - 1) : undefined}
              selected={previewIndex === p.no - 1}
            />
          ))}
        </div>
        {usingReal && activeDocumentId && previewIndex !== null && (
          <PagePreview docId={activeDocumentId} pageIndex={previewIndex} t={t}
                       folio={data.pages.find(
                         (pg: PageCard) => pg.no === previewIndex + 1)?.printed ?? null}
                       onClose={() => setPreviewIndex(null)} />
        )}
      </div>

      {/* Footer */}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 22 }}>
        <Button variant="secondary" onClick={() => nav(SCREENS.integrity.path)}>
          ← {t("sc.back")}
        </Button>
        <Button onClick={() => nav(usingReal ? SCREENS.extraction.path : SCREENS.workspace.path)}>
          {t("sc.extract")} {focused} {t("sc.pages")} →
        </Button>
      </div>
    </div>
  );
}
