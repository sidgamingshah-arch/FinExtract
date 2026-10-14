/** Screen 3 — Statement page detection: which pages each statement and the notes are read from.
 *
 *  Organised by SECTION — the four statements and the notes, each with its pages and a Use switch
 *  — beside a strip of the document's own pages, each labelled with what the next run reads it for
 *  (ScopePages.tsx). Choosing a section lights its pages and gives every page one button: Add to,
 *  Move to or Remove from that section. A section's pages can be typed. Every change is one
 *  PUT /documents/{id}/sections, and its answer replaces the screen's copy of the pages.
 *
 *  NOTHING IS EXTRACTED FROM A CLICK HERE. The screen says when the last run read other pages; the
 *  footer goes to the Extraction screen, whose mount starts the run, and on a document already
 *  extracted it asks first. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { Button, Dialog, Toggle } from "../components/ui";
import { EmptyState } from "../components/EmptyState";
import { pageNames } from "../components/SourceViewer";
import { SCREENS } from "./config";
import {
  PageLarge, PageStrip, SECTION_IDS, fill, pagesLabel, sectionName, type PickAction,
} from "./ScopePages";
import {
  useDocumentPages, usePages, useProjectLoaded, useReextract, useRunPins, useSetPageOverrides,
  useSetSections,
} from "../lib/queries";
import { useAppLocale, useUI } from "../store";
import { RTL_LOCALES, useT } from "../i18n";
import { useCan } from "../lib/rbac";
import { useNarrow } from "../lib/media";
import { ApiError, refusalText } from "../lib/api";
import { color, layout, radius } from "../theme";
import type {
  Locale, PageCard, PagePart, PagesResponse, ScopeKept, ScopeSection, ScopeSectionEdit,
  ScopeSectionId,
} from "../types";

const COLS = "minmax(150px,1.6fr) minmax(84px,1fr) 52px";
const DATE_LOCALE: Record<Locale, string> = { en: "en-GB", zh: "zh-CN", ar: "ar", fr: "fr-FR" };

type T = (key: string) => string;

/** A refusal in the reader's language, from the code and parameters the server sent with it.
 *  Anything this screen does not know is said in the server's own words. */
function refusal(t: T, err: unknown): string {
  const body = err instanceof ApiError ? err.body : undefined;
  const code = err instanceof ApiError ? err.code : undefined;
  const param = (k: string) => String(body?.[k] ?? "");
  switch (code) {
    case "run_in_flight": return t("sc.err.runInFlight");
    case "not_paged": return t("sc.notPdf");
    case "page_not_a_number": return fill(t("sc.err.notANumber"), { text: param("text") });
    case "pages_empty": return t("sc.err.empty");
    case "page_out_of_range":
      return fill(t("sc.err.outOfRange"), { page: param("page"), pages: param("pages") });
    case "page_has_parts":
      return fill(t("sc.pick.partsHelp"),
                  { n: param("parts"), section: sectionName(t, param("section")) });
    default: return refusalText(err);
  }
}

function Note({ children, testid, tone = "info", action }: {
  children: ReactNode; testid?: string; tone?: "info" | "warn"; action?: ReactNode;
}) {
  return (
    <div data-testid={testid}
         style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
                  marginTop: 12, padding: "9px 12px", borderRadius: radius.control, fontSize: 12,
                  lineHeight: 1.45,
                  background: tone === "warn" ? color.amberBg : color.indigoTint2,
                  color: tone === "warn" ? color.amberFg : color.ink2 }}>
      <span style={{ flex: "1 1 220px", minWidth: 0 }}>{children}</span>
      {action}
    </div>
  );
}

export default function ScopeScreen() {
  const nav = useNavigate();
  const t = useT();
  const locale = useAppLocale();
  const rtl = RTL_LOCALES.includes(locale);
  const qc = useQueryClient();
  // The server refuses a page change without `pipeline:run`; the screen has been gated on
  // `config:scope`. Analysts and admins hold both, and a control shows only for a reader who does.
  const canRun = useCan("pipeline:run");
  const canScope = useCan("config:scope");
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const docId = activeDocumentId ?? undefined;
  const real = !!activeDocumentId;
  const loaded = useProjectLoaded();
  const realQ = useDocumentPages(docId, true);
  const demoQ = usePages(locale, !real);
  const data: PagesResponse | undefined = real ? realQ.data : demoQ.data;
  const isPending = real ? realQ.isPending : demoQ.isPending;
  const setSections = useSetSections(docId);
  const setOverrides = useSetPageOverrides(docId);
  const reextract = useReextract(docId);
  const pins = useRunPins();
  const narrow = useNarrow();

  const [chosenId, setChosenId] = useState<ScopeSectionId | null>(null);
  const [open, setOpen] = useState<number | null>(null);          // the page shown large (`no`)
  const [editing, setEditing] = useState<ScopeSectionId | null>(null);
  const [text, setText] = useState("");
  // The page a "Choose the part…" click opened, and the section it asked for.
  const [partsFor, setPartsFor] = useState<{ no: number; section: ScopeSectionId } | null>(null);
  const [askAgain, setAskAgain] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const [againError, setAgainError] = useState<string | null>(null);
  useEffect(() => {
    setChosenId(null);
    setOpen(null);
    setEditing(null);
    setPartsFor(null);
    setStatus(null);
  }, [docId]);
  const largeRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (narrow && open != null) largeRef.current?.scrollIntoView({ block: "nearest",
                                                                    behavior: "smooth" });
  }, [narrow, open]);

  if (!real && !loaded) return <EmptyState />;
  if (real && realQ.isError) return <EmptyState />;
  if (isPending || !data) {
    return <div style={{ padding: 60, textAlign: "center", color: color.muted }}>
      {t("empty.loading")}</div>;
  }

  const cards = data.pages;
  const sections: ScopeSection[] = data.sections ?? [];
  const paged = !real || data.format === "pdf";
  const locked = !!data.locked;
  const lastRun = data.last_run ?? null;
  const canEdit = canRun && canScope && real && data.format === "pdf" && !locked;
  const busy = setSections.isPending || setOverrides.isPending;
  const chosen = sections.find((s) => s.id === chosenId) ?? null;
  const read = data.read ?? data.focused;
  const cardOf = (no: number) => cards.find((c) => c.no === no);
  // The page shown large. On a wide screen there always is one: the chosen section's first page,
  // else the first page a run reads — a report's cover and contents say nothing about its scope.
  const firstRead = cards.find((c) => c.reads && c.reads.length)?.no;
  const shown = open ?? (narrow ? null : (chosen?.pages[0] ?? firstRead ?? cards[0]?.no ?? null));
  const shownCard = shown != null ? cardOf(shown) : undefined;

  const choose = (id: ScopeSectionId) => {
    if (chosenId === id) { setChosenId(null); return; }
    setChosenId(id);
    const first = sections.find((s) => s.id === id)?.pages[0];
    if (first) setOpen(first);
  };
  const keptText = (kept: ScopeKept[] | undefined) => {
    if (!kept?.length) return "";
    const pages = kept.map((k) => pageNames(t, k.no - 1, cardOf(k.no)?.printed).badge).join(", ");
    return ` ${fill(t("sc.kept"), { pages })}`;
  };
  const save = (items: ScopeSectionEdit[], after?: () => void) => {
    setStatus({ text: t("sc.saving"), error: false });
    setSections.mutateAsync(items)
      .then((resp) => {
        setStatus({ text: t("sc.savedPages") + keptText(resp.kept), error: false });
        after?.();
      })
      .catch((err: unknown) => setStatus({ text: refusal(t, err), error: true }));
  };
  const onPick = (card: PageCard, action: PickAction) => {
    if (!chosen) return;
    if (action === "parts") {
      setOpen(card.no);
      setPartsFor({ no: card.no, section: chosen.id });
      return;
    }
    // A typing box left open would send its old pages again on Enter: a pick closes it.
    setEditing(null);
    save([{ id: chosen.id, ...(action === "remove" ? { remove: [card.no] } : { add: [card.no] }) }]);
  };
  // THE WHOLE SET IS SAVED: the server replaces every page's correction, so this one's is merged
  // into the others' (null removes it, and the page goes back to the classifier's reading).
  const saveParts = (pageIndex: number, parts: PagePart[] | null) => {
    const others = cards
      .filter((pg) => pg.overridden && pg.no - 1 !== pageIndex && pg.parts)
      .map((pg) => ({ page: pg.no - 1, parts: pg.parts as PagePart[] }));
    return setOverrides.mutateAsync(parts ? [...others, { page: pageIndex, parts }] : others);
  };

  // The start answers cached for this document go before the Extraction screen mounts, so its
  // POST asks the server — which starts a new run when the pages changed — rather than being
  // handed the run that read the old ones. Inactive only: see `pagesSaved`.
  const dropStartCache = () =>
    qc.removeQueries({ queryKey: ["extraction-start", docId], type: "inactive" });
  const extract = () => {
    if (!real) { nav(SCREENS.workspace.path); return; }
    if (locked) { nav(SCREENS.extraction.path); return; }
    if (lastRun?.status === "succeeded") { setAgainError(null); setAskAgain(true); return; }
    dropStartCache();
    nav(SCREENS.extraction.path);
  };
  const extractAgain = async () => {
    setAgainError(null);
    dropStartCache();
    try {
      await reextract.mutateAsync({ lineItemVersionId: pins.cfg?.id, templateId: pins.tpl?.id });
      nav(SCREENS.extraction.path);
    } catch (err) {
      setAgainError(refusalText(err));
    }
  };

  const changed = real && lastRun?.status === "succeeded" && !lastRun.reads_current_pages && !locked;
  const runDate = (() => {
    if (!lastRun?.created_at) return "";
    // Served as naive UTC, like every run timestamp; read as UTC, not as local time.
    const iso = /(?:[zZ]|[+-]\d\d:?\d\d)$/.test(lastRun.created_at)
      ? lastRun.created_at : `${lastRun.created_at}Z`;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? ""
      : d.toLocaleDateString(DATE_LOCALE[locale] ?? "en-GB", { day: "numeric", month: "short" });
  })();

  const hint = chosen && canEdit && (
    <div data-testid="scope-hint"
         style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 11.5,
                  color: color.ink2, background: color.indigoTint2, borderRadius: 8,
                  padding: "7px 10px", marginBottom: 8 }}>
      <span style={{ flex: "1 1 180px", minWidth: 0, lineHeight: 1.4 }}>
        {fill(t("sc.hint"), { section: sectionName(t, chosen.id) })}</span>
      <Button variant="ghost" testid="scope-hint-done" onClick={() => setChosenId(null)}
              style={{ padding: "4px 10px", fontSize: 11.5 }}>{t("sc.done")}</Button>
    </div>
  );
  const strip = paged && cards.length > 0 && (
    <PageStrip t={t} docId={real ? docId : undefined} cards={cards} current={shown}
               chosen={chosen} canEdit={canEdit} busy={busy} rtl={rtl} vertical={!narrow}
               onOpen={setOpen} onPick={onPick} />
  );
  const large = paged && shownCard && (
    <PageLarge t={t} docId={real ? docId : undefined} card={shownCard} cards={cards}
               sections={sections} chosen={chosen} canEdit={canEdit} busy={busy} rtl={rtl}
               partsFor={partsFor && partsFor.no === shownCard.no ? partsFor.section : null}
               onStep={setOpen} onPick={(action) => onPick(shownCard, action)}
               onClose={narrow ? () => setOpen(null) : undefined}
               onSaveParts={real ? (parts) => saveParts(shownCard.no - 1, parts) : undefined}
               refusal={(err) => refusal(t, err)} />
  );

  const table = (
    <div style={{ background: color.surface, border: `1px solid ${color.cardBorder}`,
                  borderRadius: radius.card, overflow: "hidden" }}>
      <div style={{ display: "grid", gridTemplateColumns: COLS, gap: 12, padding: "10px 16px",
                    background: color.rowAltBg, borderBottom: `1px solid ${color.cardBorder}`,
                    fontSize: 10.5, fontWeight: 600, letterSpacing: 0.3, color: color.muted,
                    textTransform: "uppercase" }}>
        <span>{t("sc.col.section")}</span><span>{t("sc.col.pages")}</span>
        <span>{t("sc.col.use")}</span>
      </div>
      {SECTION_IDS.map((id) => {
        const s = sections.find((x) => x.id === id) ?? { id, pages: [], read: [], on: true };
        const name = sectionName(t, id);
        const isChosen = chosenId === id;
        const sub = !s.pages.length ? t("sc.sectionNotFound")
          : !s.on && id === "notes" ? t("sc.notesOff")
          : !s.on ? t("sc.sectionOff")
          : fill(t("sc.sectionRead"), { read: s.read.length, pages: s.pages.length });
        return (
          <div key={id} data-testid="scope-section" data-section={id} data-on={String(s.on)}
               style={{ display: "grid", gridTemplateColumns: COLS, gap: 12, padding: "10px 16px",
                        borderBottom: `1px solid ${color.hairline}`, alignItems: "center",
                        opacity: s.on ? 1 : 0.55,
                        background: isChosen ? color.indigoTint : undefined,
                        boxShadow: isChosen ? `inset ${rtl ? -3 : 3}px 0 0 ${color.indigo}`
                                            : undefined }}>
            <button type="button" data-testid="scope-choose" data-section={id}
                    aria-pressed={isChosen} title={t("sc.chooseHelp")} onClick={() => choose(id)}
                    style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0,
                             padding: 0, border: "none", background: "none", cursor: "pointer",
                             textAlign: "start", color: "inherit", font: "inherit" }}>
              <span style={{ fontSize: 12.5, fontWeight: 600,
                             color: isChosen ? color.indigo : color.ink }}>{name}</span>
              <span style={{ fontSize: 11, color: color.muted, lineHeight: 1.35 }}>{sub}</span>
            </button>
            {editing === id ? (
              <span style={{ display: "flex", gap: 4, alignItems: "center", minWidth: 0 }}>
                <input data-testid="scope-pages-input" data-section={id} autoFocus value={text}
                       placeholder="6, 9–10" aria-label={t("sc.pagesEdit")} dir="ltr"
                       onChange={(e) => setText(e.target.value)}
                       onKeyDown={(e) => {
                         // Saved on Enter; the box stays open on a refusal, with the text in it.
                         if (e.key === "Enter" && !busy) {
                           save([{ id, pages: text }], () => setEditing(null));
                         }
                         if (e.key === "Escape") setEditing(null);
                       }}
                       style={{ width: "100%", minWidth: 60, fontSize: 11.5, padding: "3px 6px",
                                border: `1px solid ${color.controlBorder}`, borderRadius: 6 }} />
                <button type="button" aria-label={t("sc.pagesCancel")} title={t("sc.pagesCancel")}
                        onClick={() => setEditing(null)}
                        style={{ background: "none", border: "none", color: color.muted,
                                 cursor: "pointer", fontSize: 12, padding: 2 }}>✕</button>
              </span>
            ) : (
              <button type="button" data-testid="scope-pages" data-section={id}
                      disabled={!canEdit || busy}
                      title={canEdit ? t("sc.pagesEdit") : t("sc.pagesHelp")}
                      onClick={() => {
                        setEditing(id);
                        setText(s.pages.length ? pagesLabel(s.pages) : "");
                      }}
                      style={{ justifySelf: "start", fontSize: 11, fontWeight: 600,
                               padding: "2px 7px", borderRadius: 5, border: "none",
                               background: color.indigoTint2, color: color.indigo,
                               cursor: canEdit && !busy ? "pointer" : "default",
                               direction: "ltr", maxWidth: "100%",
                               overflowWrap: "anywhere", textAlign: "start" }}>
                {pagesLabel(s.pages)}{canEdit ? " ✎" : ""}
              </button>
            )}
            <Toggle on={s.on} testid="scope-use" data={{ section: id }}
                    label={fill(t("sc.useLabel"), { section: name })}
                    disabled={!canEdit || busy || !s.pages.length}
                    onChange={(on) => save([{ id, on }])} />
          </div>
        );
      })}
      {sections.every((s) => !s.pages.length) && (
        <div style={{ padding: "12px 16px 0", fontSize: 12, color: color.sec }}>
          {t("sc.noSections")}</div>
      )}
      <div style={{ padding: "10px 16px 12px", fontSize: 11.5, color: color.muted,
                    lineHeight: 1.5 }}>{t("sc.help")}</div>
    </div>
  );

  const again = askAgain && lastRun && (
    <Dialog title={lastRun.reads_current_pages ? t("sc.again.title") : t("sc.again.titleChanged")}
            closeLabel={t("sc.again.cancel")} onClose={() => setAskAgain(false)}
            testid="scope-extract-again">
      <div style={{ fontSize: 12.5, color: color.ink2, lineHeight: 1.55 }}>
        {fill(lastRun.reads_current_pages ? t("sc.again.body") : t("sc.again.bodyChanged"),
              { n: lastRun.run_number, date: runDate })}
      </div>
      <div style={{ fontSize: 12, color: color.sec2, lineHeight: 1.5, marginTop: 8 }}>
        {fill(t("sc.again.cost"), { n: lastRun.run_number })}
      </div>
      {againError && (
        <div data-testid="scope-again-error" role="alert"
             style={{ fontSize: 12, color: color.redFg, marginTop: 10 }}>{againError}</div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        {(() => {
          const extractBtn = (primary: boolean) => (
            <Button key="extract" testid="scope-again-extract"
                    variant={primary ? "primary" : "secondary"}
                    disabled={!pins.ready || !pins.tplSettled || reextract.isPending}
                    onClick={() => { void extractAgain(); }} style={{ padding: "8px 16px" }}>
              {reextract.isPending ? t("sc.again.starting") : t("sc.again.extract")}
            </Button>
          );
          const openBtn = (primary: boolean) => (
            <Button key="open" testid="scope-again-open" variant={primary ? "primary" : "secondary"}
                    onClick={() => nav(SCREENS.workspace.path)} style={{ padding: "8px 16px" }}>
              {t("sc.again.open")}
            </Button>
          );
          return lastRun.reads_current_pages
            ? [openBtn(true), extractBtn(false)] : [extractBtn(true), openBtn(false)];
        })()}
        <Button variant="ghost" testid="scope-again-cancel" onClick={() => setAskAgain(false)}
                style={{ padding: "8px 16px" }}>{t("sc.again.cancel")}</Button>
      </div>
    </Dialog>
  );

  const below = (
    <>
      {!paged && <Note testid="scope-not-pdf">{t("sc.notPdf")}</Note>}
      {locked && (
        <Note testid="scope-locked" tone="warn"
              action={<Button variant="secondary" testid="scope-locked-open"
                              onClick={() => nav(SCREENS.extraction.path)}
                              style={{ padding: "5px 12px", fontSize: 12 }}>
                {t("sc.openExtraction")}</Button>}>
          {t("sc.locked")}
        </Note>
      )}
      {status && (
        <div data-testid="scope-status" role="status" aria-live="polite"
             style={{ marginTop: 10, fontSize: 12, lineHeight: 1.45,
                      color: status.error ? color.redFg : color.sec }}>{status.text}</div>
      )}
      {changed && <Note testid="scope-changed">{t("sc.changed")}</Note>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 20,
                    flexWrap: "wrap" }}>
        <Button variant="secondary" testid="scope-back" onClick={() => nav(SCREENS.integrity.path)}>
          ← {t("sc.back")}
        </Button>
        {locked ? (
          <Button testid="scope-open-extraction" onClick={() => nav(SCREENS.extraction.path)}>
            {t("sc.openExtraction")}</Button>
        ) : (
          <Button testid="scope-extract" onClick={extract} disabled={paged && read === 0}
                  title={paged && read === 0 ? t("sc.blockedNone") : undefined}>
            {/* A workbook or an image is not read page by page, so it has no count of pages. */}
            {paged ? fill(t("sc.extractN"), { n: read }) : t("sc.openExtraction")}
          </Button>
        )}
      </div>
    </>
  );

  return (
    <div style={{ maxWidth: cards.length && paged ? 1500 : layout.screenMaxWide, margin: "0 auto",
                  padding: "26px 30px 60px" }}>
      {again}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between",
                    gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 360px" }}>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("sc.title")}</h1>
          <p style={{ margin: 0, color: color.sec2 }}>{t("sc.subhead")}</p>
        </div>
        {paged && (
          <div data-testid="scope-read-count"
               style={{ fontSize: 12.5, fontWeight: 600, color: color.ink }}>
            {fill(t("sc.readCount"), { read, total: data.total })}
          </div>
        )}
      </div>
      {!paged ? below : (
        <div style={narrow ? undefined : { display: "flex", gap: 18, alignItems: "flex-start" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {table}
            {narrow && strip && (
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: color.sec2, marginBottom: 6 }}>
                  {t("sc.strip")}</div>
                {hint}
                {strip}
                {large && <div ref={largeRef} style={{ marginTop: 10 }}>{large}</div>}
              </div>
            )}
            {below}
          </div>
          {!narrow && strip && (
            <aside style={{ flex: "0 0 auto",
                            width: "max(min(560px, 42vw), min(760px, calc(100% - 600px)))",
                            position: "sticky", top: 12, alignSelf: "flex-start" }}>
              {hint}
              <div style={{ display: "flex", gap: 10 }}>
                <div style={{ flex: "0 0 112px", minWidth: 0 }}>{strip}</div>
                <div style={{ flex: 1, minWidth: 0 }}>{large}</div>
              </div>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
