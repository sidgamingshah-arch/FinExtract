/** Page Scope: the document's own pages beside its sections. A strip of small page images (only
 *  the ones scrolled near are fetched), each labelled with its folio and sheet number and with what
 *  the next run reads it for, or "not read". Clicking a page shows it large, with the page before
 *  and after and the editor that cuts a page into parts. With a section chosen, each page offers one
 *  button — Add to, Move to or Remove from that section, or Choose the part… for a page cut into
 *  parts — which the screen saves through PUT /documents/{id}/sections.
 *
 *  What a page is read for is the SERVER's answer (`reads` on each card), never derived here: the
 *  reader's rule has cases a client cannot see, and a label that disagreed with the run would be
 *  worse than none. */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { Button, confReadout } from "../components/ui";
import { pageNames } from "../components/SourceViewer";
import { api } from "../lib/api";
import { color, confStyle, font, radius } from "../theme";
import type { PageCard, PagePart, ScopeSection, ScopeSectionId } from "../types";

type T = (key: string) => string;

/** The sections the screen lists, in order — the server's `page_reading.SECTIONS`, held equal by
 *  backend/tests/test_scope_screen_strings.py. */
export const SECTION_IDS: ScopeSectionId[] = [
  "balance_sheet", "profit_and_loss", "cash_flow", "changes_in_equity", "notes",
];
const STATEMENTS = ["balance_sheet", "profit_and_loss", "cash_flow", "changes_in_equity"];
/** The strip asks for small images: a quarter of the default's bytes, and each request makes the
 *  server open the whole file again. */
export const THUMB_DPI = 50;
export const LARGE_DPI = 110;

/** `{name}` placeholders filled by hand, the way every placeholder in this app is. */
export function fill(s: string, vars: Record<string, string | number>): string {
  return s.replace(/\{(\w+)\}/g, (whole, k: string) => (k in vars ? String(vars[k]) : whole));
}

export function sectionName(t: T, id: string): string {
  return id === "notes" ? t("sc.kindNotes") : t(`sc.stmt.${id}`);
}

/** The section a part is read for, as the server decides it (`page_reading.section_of`). */
export function sectionOf(part: PagePart): ScopeSectionId | null {
  if (part.kind === "face") {
    return part.statement && STATEMENTS.includes(part.statement)
      ? part.statement as ScopeSectionId : null;
  }
  return part.kind === "notes" ? "notes" : null;
}

/** "4–6, 9", or "—" for no pages. */
export function pagesLabel(nos: number[]): string {
  if (!nos.length) return "—";
  const runs: [number, number][] = [];
  for (const n of [...new Set(nos)].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && n === last[1] + 1) last[1] = n;
    else runs.push([n, n]);
  }
  return runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ");
}

function partName(t: T, part: PagePart): string {
  const s = sectionOf(part);
  if (!s) return t("sc.readUnattributed");
  const name = sectionName(t, s);
  if (part.kind === "face" && part.entity) {
    const entity = part.entity === "company" ? t("sc.entityCompany") : t("sc.entityGroup");
    return fill(t("sc.readForEntity"), { section: name, entity });
  }
  return name;
}

/** What the next run reads a page for: "Balance sheet (Group), Notes", or "not read". Empty for a
 *  document that is not a PDF, which this screen does not change. */
export function readFor(t: T, card: PageCard): { text: string; read: boolean } {
  if (card.reads == null) return { text: "", read: false };
  if (!card.reads.length) return { text: t("sc.notRead"), read: false };
  return { text: [...new Set(card.reads.map((p) => partName(t, p)))].join(", "), read: true };
}

export type PickAction = "add" | "move" | "remove" | "parts";

/** The one button a page offers for the chosen section. A page left out of a page selection is
 *  added back; a page cut into parts is changed part by part, in the editor. */
export function pickAction(card: PageCard, chosen: ScopeSection): PickAction {
  if (chosen.pages.includes(card.no)) return card.unread === "out_of_scope" ? "add" : "remove";
  const parts = card.parts ?? [];
  if (!parts.some((p) => sectionOf(p))) return "add";
  return parts.length === 1 ? "move" : "parts";
}

/** The page PNG as an object URL, fetched only once `visible`, revoked when it changes. */
function usePageImage(docId: string | undefined, index: number, dpi: number, visible: boolean) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!visible || !docId) return;
    let obj: string | null = null;
    let cancelled = false;
    api.fetchPageImage(docId, index, dpi)
      .then((blob) => {
        if (cancelled) return;
        obj = URL.createObjectURL(blob);
        setUrl(obj);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; if (obj) URL.revokeObjectURL(obj); };
  }, [docId, index, dpi, visible]);
  return { url, failed };
}

const tagStyle: CSSProperties = {
  fontSize: 9, fontWeight: 600, padding: "1px 5px", borderRadius: 4, lineHeight: 1.3,
};

/** The pick button's label and hover text. */
function pickText(t: T, card: PageCard, chosen: ScopeSection, action: PickAction) {
  const section = sectionName(t, chosen.id);
  if (action === "parts") {
    return { label: t("sc.pick.parts"),
             title: fill(t("sc.pick.partsHelp"), { n: card.parts?.length ?? 0, section }) };
  }
  const label = fill(t(`sc.pick.${action}`), { section });
  if (action === "move") {
    // Named from the page's PARTS, not from what the next run reads: a page whose section is
    // switched off, or that a page selection leaves out, reads nothing, and its hover text would
    // say it "is read for not read".
    const from = [...new Set((card.parts ?? []).filter((p) => sectionOf(p))
      .map((p) => partName(t, p)))].join(", ");
    return { label, title: fill(t("sc.pick.moveHelp"), { from, section }) };
  }
  return { label, title: label };
}

function PickButton({ t, card, chosen, busy, onPick, testid, large }: {
  t: T; card: PageCard; chosen: ScopeSection; busy: boolean;
  onPick: (action: PickAction) => void; testid: string; large?: boolean;
}) {
  const action = pickAction(card, chosen);
  const { label, title } = pickText(t, card, chosen, action);
  const primary = action === "add" || action === "move";
  return (
    <button type="button" data-testid={testid} data-page={card.no} data-action={action}
            disabled={busy} title={title}
            onClick={(e) => { e.stopPropagation(); onPick(action); }}
            style={{ marginTop: large ? 0 : 5, width: large ? undefined : "100%",
                     fontSize: large ? 12 : 10.5, fontWeight: 600, lineHeight: 1.25,
                     padding: large ? "6px 13px" : "4px 4px", borderRadius: large ? 7 : 6,
                     cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
                     border: `1px solid ${primary ? color.indigo : color.controlBorder}`,
                     background: primary ? color.indigo : color.surface,
                     color: primary ? "#fff" : color.sec, overflowWrap: "anywhere" }}>
      <span style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                     overflow: "hidden" }}>{label}</span>
    </button>
  );
}

function Tile({ t, docId, card, current, lit, chosen, canEdit, busy, onOpen, onPick, root, width }: {
  t: T; docId: string | undefined; card: PageCard; current: boolean; lit: boolean;
  chosen: ScopeSection | null; canEdit: boolean; busy: boolean;
  onOpen: () => void; onPick: (action: PickAction) => void; root: HTMLElement | null;
  width?: number;
}) {
  const holder = useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = useState(false);
  // FETCHED ONCE SCROLLED NEAR: a strip of two hundred pages must not ask for two hundred images.
  useEffect(() => {
    const el = holder.current;
    if (!el || visible || !docId) return;
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { root, rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [visible, root, docId]);
  const { url, failed } = usePageImage(docId, card.no - 1, THUMB_DPI, visible);
  const names = pageNames(t, card.no - 1, card.printed);
  const rf = readFor(t, card);
  // The classifier's own confidence in this page's class, as the tile has always printed it: the
  // served percentage, else the band's NAME in muted chrome — never a band's stand-in figure.
  const conf = confReadout({ cat: card.conf, pct: card.conf_pct }, t);
  const cc = confStyle(card.conf);
  return (
    <div ref={holder} data-testid="scope-tile" data-page={card.no} data-lit={String(lit)}
         data-read={String(rf.read)}
         style={{ flex: width ? `0 0 ${width}px` : undefined, width, boxSizing: "border-box",
                  padding: 5, borderRadius: 8,
                  background: lit ? color.indigoTint : current ? color.rowAltBg : "transparent",
                  border: `2px solid ${current ? color.indigo : lit ? color.indigoBorder
                                                                     : "transparent"}` }}>
      <button type="button" data-testid="scope-page" data-page={card.no} onClick={onOpen}
              aria-label={rf.text ? `${names.badge} · ${rf.text}` : names.badge}
              aria-current={current ? "true" : undefined}
              style={{ display: "block", width: "100%", padding: 0, border: "none",
                       background: "none", cursor: "pointer", textAlign: "start", color: "inherit",
                       font: "inherit" }}>
        <span style={{ display: "block", position: "relative", width: "100%", paddingTop: "129%",
                       background: "#fff", border: `1px solid ${color.cardBorder}`, borderRadius: 3,
                       overflow: "hidden", opacity: rf.read || lit ? 1 : 0.6 }}>
          {url && <img src={url} alt="" draggable={false}
                       style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                                objectFit: "contain" }} />}
          {!url && (
            <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center",
                           justifyContent: "center", padding: 4, textAlign: "center",
                           fontSize: 9.5, color: color.muted, lineHeight: 1.3 }}>
              {failed ? t("sc.renderFailed") : names.badge}
            </span>
          )}
        </span>
        <span style={{ display: "flex", gap: 4, alignItems: "baseline", marginTop: 4,
                       flexWrap: "wrap" }}>
          <span style={{ fontFamily: font.mono, fontSize: 10, fontWeight: 700, color: color.ink }}>
            {names.badge}
          </span>
          {card.scan === "scanned" && (
            <span style={{ ...tagStyle, background: color.redBg, color: color.redFg }}>
              {t("sc.scan")}</span>
          )}
          {card.overridden && (
            <span style={{ ...tagStyle, background: color.indigoTint2, color: color.indigo }}>
              {t("sc.corrected")}</span>
          )}
        </span>
        <span data-testid="scope-conf" data-measured={String(conf.measured)} title={conf.title}
              style={{ display: "block", marginTop: 2, fontSize: 10,
                       fontFamily: conf.measured ? font.mono : font.sans,
                       fontStyle: conf.measured ? undefined : "italic",
                       color: conf.measured ? cc.fg : color.muted }}>
          {conf.text}
        </span>
        <span data-testid="scope-page-for" data-page={card.no}
              style={{ fontSize: 10.5, lineHeight: 1.3, marginTop: 2,
                       color: rf.read ? color.sec : color.muted,
                       fontStyle: rf.read ? undefined : "italic", overflow: "hidden",
                       display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                       overflowWrap: "anywhere" }}>
          {rf.text}
        </span>
      </button>
      {chosen && canEdit && (
        <PickButton t={t} card={card} chosen={chosen} busy={busy} onPick={onPick}
                    testid="scope-pick" />
      )}
    </div>
  );
}

/** The strip: every page, a column beside the large view on a wide screen, a sideways row on a
 *  narrow one. The page shown large is scrolled into it. */
export function PageStrip({ t, docId, cards, current, chosen, canEdit, busy, onOpen, onPick,
                            vertical, rtl }: {
  t: T; docId: string | undefined; cards: PageCard[]; current: number | null;
  chosen: ScopeSection | null; canEdit: boolean; busy: boolean;
  onOpen: (no: number) => void; onPick: (card: PageCard, action: PickAction) => void;
  vertical: boolean; rtl: boolean;
}) {
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  // Again when a section is chosen: its buttons make every tile taller, which can push the page
  // shown large out of sight. The strip's lower edge counts only as far as the window shows it.
  useEffect(() => {
    if (!root || current == null) return;
    const el = root.querySelector<HTMLElement>(`[data-testid="scope-tile"][data-page="${current}"]`);
    if (!el) return;
    const r = root.getBoundingClientRect();
    const e = el.getBoundingClientRect();
    if (vertical) {
      const bottom = Math.min(r.bottom, window.innerHeight);
      if (e.top < r.top || e.bottom > bottom) root.scrollTop += e.top - r.top - 8;
    } else if (e.left < r.left || e.right > r.right) {
      root.scrollLeft += rtl ? e.right - r.right + 8 : e.left - r.left - 8;
    }
  }, [root, current, vertical, rtl, chosen?.id]);
  return (
    <div ref={setRoot} data-testid="scope-strip" aria-label={t("sc.strip")} role="group"
         style={vertical
           ? { display: "flex", flexDirection: "column", gap: 4, overflowY: "auto",
               overflowX: "hidden", maxHeight: "calc(100vh - 150px)", minHeight: 300,
               paddingInlineEnd: 2 }
           : { display: "flex", gap: 6, overflowX: "auto", overflowY: "hidden",
               paddingBottom: 6 }}>
      {cards.map((card) => (
        <Tile key={card.no} t={t} docId={docId} card={card} current={current === card.no}
              lit={!!chosen && chosen.pages.includes(card.no) && card.unread !== "out_of_scope"}
              chosen={chosen} canEdit={canEdit} busy={busy} root={root}
              width={vertical ? undefined : 104}
              onOpen={() => onOpen(card.no)} onPick={(action) => onPick(card, action)} />
      ))}
    </div>
  );
}

/** How one page is read, part by part: where each part begins, what it is, and — for a statement
 *  face — which statement and whose figures. Starts from the reading the next run would use (the
 *  saved correction, else the classifier's own cut). Clicking the page image adds a cut there. */
function PartsEditor({ parts, setParts, canEdit, t }:
  { parts: PagePart[]; setParts: (p: PagePart[]) => void; canEdit: boolean;
    t: (k: string) => string }) {
  const update = (i: number, patch: Partial<PagePart>) => {
    const next = parts.map((p, j) => (j === i ? { ...p, ...patch } : p));
    if (patch.kind && patch.kind !== "face") {
      next[i] = { ...next[i], statement: null, entity: null };
    } else if (patch.kind === "face" && !next[i].statement) {
      next[i] = { ...next[i], statement: "balance_sheet" };
    }
    setParts(next);
  };
  const sel = { fontSize: 11, padding: "3px 4px", borderRadius: 5,
                border: `1px solid ${color.controlBorder}`, background: color.surface };
  return (
    <div data-testid="parts-editor" style={{ display: "grid", gap: 6 }}>
      {parts.map((p, i) => (
        <div key={i} style={{ display: "flex", gap: 5, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 10.5, color: color.muted, minWidth: 64 }}>
            {i + 1}. {t("sc.from")} {Math.round(p.from_y * 100)}%
          </span>
          <select disabled={!canEdit} value={p.kind} style={sel}
                  onChange={(e) => update(i, { kind: e.target.value as PagePart["kind"] })}>
            <option value="face">{t("sc.kindFace")}</option>
            <option value="notes">{t("sc.kindNotes")}</option>
            <option value="other">{t("sc.kindOther")}</option>
          </select>
          {p.kind === "face" && (
            <>
              <select disabled={!canEdit} value={p.statement ?? ""} style={sel}
                      onChange={(e) => update(i, { statement: e.target.value })}>
                {STATEMENTS.map((k) => <option key={k} value={k}>{t(`sc.stmt.${k}`)}</option>)}
              </select>
              <select disabled={!canEdit} value={p.entity ?? ""} style={sel}
                      onChange={(e) => update(i, {
                        entity: (e.target.value || null) as PagePart["entity"] })}>
                <option value="">{t("sc.entityAuto")}</option>
                <option value="consolidated">{t("sc.entityConsolidated")}</option>
                <option value="company">{t("sc.entityCompany")}</option>
              </select>
            </>
          )}
          {canEdit && i > 0 && (
            <button onClick={() => setParts(parts.filter((_, j) => j !== i))}
                    style={{ fontSize: 10.5, color: color.sec, background: "none", cursor: "pointer",
                             border: `1px solid ${color.controlBorder}`, borderRadius: 5,
                             padding: "2px 7px" }}>
              {t("sc.removePart")}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** The page shown large: "p.03 · sheet 4 · Profit & loss (Group)", the page before and after, the
 *  chosen section's button, and the editor that cuts the page into parts. */
export function PageLarge({ t, docId, card, cards, sections, chosen, canEdit, busy, rtl,
                            partsFor, onStep, onPick, onClose, onSaveParts, refusal }: {
  t: T; docId: string | undefined; card: PageCard; cards: PageCard[]; sections: ScopeSection[];
  chosen: ScopeSection | null; canEdit: boolean; busy: boolean; rtl: boolean;
  /** The section a "Choose the part…" click asked to set on this page. */
  partsFor: ScopeSectionId | null;
  onStep: (no: number) => void; onPick: (action: PickAction) => void; onClose?: () => void;
  onSaveParts?: (parts: PagePart[] | null) => Promise<unknown>;
  refusal: (err: unknown) => string;
}) {
  const { url, failed } = usePageImage(docId, card.no - 1, LARGE_DPI, true);
  const names = pageNames(t, card.no - 1, card.printed);
  const rf = readFor(t, card);
  const at = cards.findIndex((c) => c.no === card.no);
  const prev = at > 0 ? cards[at - 1] : undefined;
  const next = at >= 0 && at < cards.length - 1 ? cards[at + 1] : undefined;
  const off = sections.filter((s) => !s.on && s.pages.includes(card.no));

  // THE EDITOR'S OWN COPY of the parts, re-seeded when the page or its SERVED parts change — by
  // value, not by object identity, so a save elsewhere on the screen (which replaces every card)
  // does not wipe a cut made here and not yet saved.
  const [parts, setParts] = useState<PagePart[]>(card.parts ?? []);
  const [status, setStatus] = useState<string | null>(null);
  const servedParts = JSON.stringify(card.parts ?? []);
  useEffect(() => {
    setParts(card.parts ?? []);
    setStatus(null);
  }, [card.no, servedParts]);
  const editorRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (partsFor) editorRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [partsFor, card.no]);

  const cut = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!canEdit || !parts.length) return;
    const box = e.currentTarget.getBoundingClientRect();
    const y = Math.round(((e.clientY - box.top) / box.height) * 1000) / 1000;
    if (y <= 0 || y >= 1 || parts.some((p) => Math.abs(p.from_y - y) < 0.01)) return;
    const above = [...parts].reverse().find((p) => p.from_y < y) ?? parts[0];
    setParts([...parts, { ...above, from_y: y }].sort((a, b) => a.from_y - b.from_y));
  };
  const save = (nextParts: PagePart[] | null) => {
    if (!onSaveParts) return;
    setStatus(t("sc.saving"));
    onSaveParts(nextParts).then(() => setStatus(t("sc.saved")))
      .catch((err: unknown) => setStatus(refusal(err)));
  };
  const navBtn: CSSProperties = {
    fontSize: 13, fontWeight: 600, lineHeight: 1, padding: "5px 9px", borderRadius: 6,
    border: `1px solid ${color.controlBorder}`, background: color.surface, color: color.sec,
    cursor: "pointer",
  };
  return (
    <div data-testid="scope-large" style={{ minWidth: 0 }}>
      <div style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: 0.3, color: color.muted,
                    fontVariantCaps: "all-small-caps", marginBottom: 4 }}>
        {t("sc.preview")}
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8,
                    flexWrap: "wrap" }}>
        <span data-testid="scope-large-title"
              style={{ fontSize: 12.5, fontWeight: 600, flex: "1 1 160px", minWidth: 0,
                       lineHeight: 1.35 }}>
          {names.badge}
          {rf.text && (
            <> · <span style={{ fontWeight: rf.read ? 600 : 400,
                                 color: rf.read ? color.ink : color.muted }}>{rf.text}</span></>
          )}
        </span>
        <button type="button" data-testid="scope-prev" aria-label={t("sc.prevPage")}
                title={t("sc.prevPage")} disabled={!prev}
                onClick={() => prev && onStep(prev.no)}
                style={{ ...navBtn, opacity: prev ? 1 : 0.4 }}>{rtl ? "›" : "‹"}</button>
        <button type="button" data-testid="scope-next" aria-label={t("sc.nextPage")}
                title={t("sc.nextPage")} disabled={!next}
                onClick={() => next && onStep(next.no)}
                style={{ ...navBtn, opacity: next ? 1 : 0.4 }}>{rtl ? "‹" : "›"}</button>
        {onClose && (
          <button type="button" data-testid="scope-large-close" onClick={onClose}
                  style={{ ...navBtn, fontSize: 11.5 }}>{t("sc.previewClose")}</button>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center",
                    marginBottom: 8 }}>
        <span style={{ ...tagStyle, fontSize: 9.5,
                       background: card.scan === "scanned" ? color.redBg : color.greenBg2,
                       color: card.scan === "scanned" ? color.redFg : color.greenFg }}>
          {card.scan === "scanned" ? t("sc.scan") : t("sc.native")}
        </span>
        {card.overridden && (
          <span style={{ ...tagStyle, fontSize: 9.5, background: color.indigoTint2,
                         color: color.indigo }}>{t("sc.corrected")}</span>
        )}
        {off.length > 0 && (
          <span style={{ fontSize: 11, color: color.muted }}>
            {fill(t("sc.alsoOff"),
                  { sections: off.map((s) => sectionName(t, s.id)).join(", ") })}
          </span>
        )}
        {card.unread === "out_of_scope" && (
          <span style={{ fontSize: 11, color: color.amberFg }}>{t("sc.outOfScope")}</span>
        )}
      </div>
      {chosen && canEdit && (
        <div style={{ marginBottom: 8 }}>
          <PickButton t={t} card={card} chosen={chosen} busy={busy} onPick={onPick}
                      testid="scope-large-pick" large />
        </div>
      )}
      <div style={{ border: `1px solid ${color.cardBorder}`, borderRadius: radius.cardSm,
                    overflow: "hidden", background: "#fff", minHeight: url ? undefined : 320,
                    position: "relative" }}>
        <div onClick={cut} style={{ position: "relative", cursor: canEdit ? "crosshair" : "default" }}>
          {url
            ? <img src={url} alt="" draggable={false} style={{ display: "block", width: "100%" }} />
            : (
              <div style={{ padding: 40, textAlign: "center", fontSize: 11.5, color: color.faint }}>
                {failed ? t("sc.renderFailed") : docId ? "…" : names.badge}
              </div>
            )}
          {url && parts.slice(1).map((p, i) => (
            <div key={i} style={{ position: "absolute", left: 0, right: 0, top: `${p.from_y * 100}%`,
                                  borderTop: `2px dashed ${color.indigo}` }}>
              <span style={{ position: "absolute", insetInlineEnd: 4, top: 2, fontSize: 10,
                             fontWeight: 600, color: "#fff", background: color.indigo,
                             borderRadius: 4, padding: "0 5px" }}>{i + 2}</span>
            </div>
          ))}
        </div>
      </div>
      {card.parts && (
        <div ref={editorRef} style={{ marginTop: 12 }}>
          {partsFor && canEdit && (
            <div data-testid="scope-parts-for"
                 style={{ fontSize: 11.5, color: color.ink2, background: color.indigoTint2,
                          borderRadius: 8, padding: "7px 10px", marginBottom: 8 }}>
              {fill(t("sc.partsFor"), { section: sectionName(t, partsFor) })}
            </div>
          )}
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>{t("sc.readAs")}</div>
          {canEdit && (
            <div style={{ fontSize: 10.5, color: color.muted, marginBottom: 8 }}>
              {t("sc.readAsHelp")}</div>
          )}
          <PartsEditor parts={parts} setParts={setParts} canEdit={canEdit} t={t} />
          {canEdit && (
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <Button onClick={() => save(parts)} disabled={busy} testid="scope-save-parts"
                      style={{ padding: "7px 14px", fontSize: 12 }}>{t("sc.saveParts")}</Button>
              {card.overridden && (
                <Button variant="secondary" onClick={() => save(null)} disabled={busy}
                        testid="scope-reset-parts" style={{ padding: "7px 14px", fontSize: 12 }}>
                  {t("sc.resetParts")}</Button>
              )}
            </div>
          )}
          {status && <div role="status" style={{ fontSize: 11, color: color.sec, marginTop: 6 }}>
            {status}</div>}
        </div>
      )}
    </div>
  );
}
