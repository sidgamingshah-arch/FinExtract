/** Screen 7, page 1 — the template index.
 *
 * The whole job of this page is picking a template, so it carries only the facts you choose
 * between: what a version is called, which key it is a version of, which version it is, whether
 * it is published, and which LINE-ITEM SET supplies its rules. Those arrive in the single
 * GET /templates the list already makes, plus the one GET /line-items/versions that names the
 * configuration in force. The structure tree and the per-item rule editors are a different task
 * and live on the detail page (see Template.tsx), reached by clicking a row.
 *
 * THE ONTOLOGY COLUMN THAT STOOD HERE IS GONE, and with it the second store it read from
 * (`useOntologies` over `/ontologies`) and the "superseded" label it printed. Line items is the
 * single configuration engine: a row is described by the line-item version IN FORCE for its
 * template key, there is no second kind of rulebook to name, and nothing on this screen selects an
 * engine. Do not reinstate an ontology column — the rows it listed no longer exist.
 *
 * There is deliberately NO item-count column. The count is not in GET /templates; it is only
 * on the per-template detail, a ~230 KB document (the whole tree plus every item's criteria),
 * so printing it cost one such document per row — 922 KB of transfer on a four-row index to fill
 * four integers, 99% of everything the page fetched. What a reader loses is comparing the size of
 * two versions' spreads at a glance from the list; the number itself is not lost, because the
 * detail page prints it in its header (see Template.tsx) and the publish confirmation states it
 * for the version it just created. It belongs back here the day GET /templates carries
 * `line_items`, which the publish endpoint already computes for its own response.
 *
 * Authoring stays HERE: publishing a template version, or a line-item set for it, is something you
 * do to the collection rather than to one item inside one version of it.
 */
import type { CSSProperties } from "react";
import { useRef, useState } from "react";

import {
  configurationInForce, useLineItemVersions, usePublishLineItems, useTemplateXlsxColumns,
  useUploadTemplateXlsx,
} from "../lib/queries";
import { ApiError, api, downloadTemplateXlsx } from "../lib/api";
import { color, font, layout, radius } from "../theme";
import type { LineItemVersionRef, Locale, TemplateRef } from "../types";

/** Admin-only: the authoring desk for templates and their line items.
 *
 * Deciding what a spread should contain is a spreadsheet job, so the primary path is the round
 * trip: download the active template as a workbook, mark each line extracted or calculated (and
 * for a calculated one, what it is calculated FROM), upload it back. That publishes a new
 * VERSION — nothing is overwritten, so an extraction that already ran still explains itself
 * against the template it actually used. The line-item set (the one configuration the extractor
 * reads) is uploaded against a named template and validated against it, so a rule for a line the
 * template does not define is refused with the key in the message rather than silently ignored.
 */
function TemplateAuthoring({
  templates,
  selectedId,
  onSelect,
  t,
}: {
  templates: TemplateRef[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  t: (k: string) => string;
}) {
  const xlsxRef = useRef<HTMLInputElement>(null);
  const lineItemsRef = useRef<HTMLInputElement>(null);
  const jsonRef = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Upload onto the selected template's key (a new version of it) or start a fresh template.
  const [asNew, setAsNew] = useState(false);
  const uploadXlsx = useUploadTemplateXlsx();
  const publishLineItems = usePublishLineItems();
  const cols = useTemplateXlsxColumns();

  const selected = templates.find((x) => x.id === selectedId);

  const fail = (err: unknown) => {
    const text = err instanceof ApiError ? (err.detail ?? err.message)
      : err instanceof Error ? err.message : String(err);
    setMsg({ ok: false, text: text.slice(0, 400) });
  };

  async function download() {
    if (!selected) return;
    setBusy("xlsx");
    setMsg(null);
    try {
      await downloadTemplateXlsx(selected.id, selected.template_key);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }

  async function onXlsx(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy("upload");
    setMsg(null);
    try {
      const res = await uploadXlsx.mutateAsync({
        file: f,
        templateKey: asNew ? "" : (selected?.template_key ?? ""),
        name: asNew ? f.name.replace(/\.(xlsx|xlsm)$/i, "") : (selected?.name ?? ""),
      });
      setMsg({ ok: true, text: t("tp.auth.publishedTemplate")
        .replace("{key}", res.template_key).replace("{v}", String(res.version))
        .replace("{n}", String(res.line_items)) });
      onSelect(res.id);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
      if (xlsxRef.current) xlsxRef.current.value = "";
    }
  }

  // Was `onOntology`, posting to `/ontologies` through `useUploadOntology`. That store and that
  // route are gone: a set published here becomes a new version of THE configuration, the one the
  // extractor reads and a run pins itself to.
  async function onLineItems(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy("line-items");
    setMsg(null);
    try {
      const definition = JSON.parse(await f.text());
      const res = await publishLineItems.mutateAsync({
        definition,
        // Point the set at the template the buttons act on — that is the one it will be
        // checked against, and checking it against something else would be the wrong answer
        // quietly.
        targetTemplateKey: selected?.template_key,
      });
      setMsg({ ok: true, text: t("tp.auth.publishedLineItems")
        .replace("{key}", res.line_items_key).replace("{v}", String(res.version))
        .replace("{n}", String(res.items)).replace("{tpl}", res.target_template_key) });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
      if (lineItemsRef.current) lineItemsRef.current.value = "";
    }
  }

  async function onJson(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy("json");
    setMsg(null);
    try {
      const def = JSON.parse(await f.text());
      // Which of the two documents was pasted. The old sniff looked for `mappings`, the ontology's
      // concept list; a line-item set declares `items`, so that is what says "this is the
      // configuration, publish it as a version of it" rather than "this is a template".
      const isLineItems = "target_template_key" in def || "items" in def;
      const res = isLineItems ? await api.publishLineItems(def) : await api.createTemplate(def);
      const key = "template_key" in res ? res.template_key : res.line_items_key;
      setMsg({ ok: true, text: t("tp.importOk").replace("{key}", key)
        .replace("{v}", String(res.version)) });
      if ("template_key" in res) onSelect(res.id);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
      if (jsonRef.current) jsonRef.current.value = "";
    }
  }

  const btn = (primary: boolean): CSSProperties => ({
    fontSize: 12, fontWeight: 600,
    color: primary ? "#fff" : color.indigo,
    background: primary ? color.indigo : "#fff",
    border: primary ? "none" : `1px solid ${color.indigoBorder2}`,
    borderRadius: radius.control, padding: "8px 14px",
    cursor: busy ? "wait" : "pointer", whiteSpace: "nowrap",
  });

  return (
    <div
      data-testid="template-authoring"
      style={{
        background: color.surface, border: `1px solid ${color.cardBorder}`,
        borderRadius: radius.card, padding: 18, marginBottom: 22,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 9, marginBottom: 4 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{t("tp.auth.title")}</span>
        <span style={{ fontSize: 11, color: color.muted }}>{t("tp.auth.versioned")}</span>
      </div>
      <p style={{ margin: "0 0 14px", fontSize: 12, color: color.sec, lineHeight: 1.55 }}>
        {t("tp.auth.hint")}
      </p>

      <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12,
                      fontSize: 12, color: color.sec }}>
        <span style={{ color: color.muted }}>{t("tp.auth.active")}</span>
        <select
          value={selectedId ?? ""}
          data-testid="template-picker"
          onChange={(e) => onSelect(e.target.value)}
          style={{ fontSize: 12, fontWeight: 600, fontFamily: font.sans, color: color.ink,
                   border: `1px solid ${color.controlBorder}`, borderRadius: radius.controlSm,
                   padding: "6px 9px", background: "#fff", cursor: "pointer", maxWidth: 420 }}
        >
          {sortTemplates(templates).map((x) => (
            <option key={x.id} value={x.id}>{`${x.name || x.template_key} · v${x.version}`}</option>
          ))}
        </select>
      </label>

      <input ref={xlsxRef} type="file" data-testid="tpl-xlsx-input" style={{ display: "none" }}
             onChange={onXlsx}
             accept=".xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
      <input ref={lineItemsRef} type="file" accept="application/json,.json"
             data-testid="tpl-line-items-input"
             style={{ display: "none" }} onChange={onLineItems} />
      <input ref={jsonRef} type="file" accept="application/json,.json" data-testid="tpl-json-input"
             style={{ display: "none" }} onChange={onJson} />

      <div style={{ display: "flex", gap: 9, flexWrap: "wrap", alignItems: "center" }}>
        <button onClick={download} disabled={!!busy || !selected} data-testid="tpl-download-xlsx"
                style={btn(false)}>
          {busy === "xlsx" ? t("tp.auth.working") : t("tp.auth.download")}
        </button>
        <button onClick={() => xlsxRef.current?.click()} disabled={!!busy}
                data-testid="tpl-upload-xlsx" style={btn(true)}>
          {busy === "upload" ? t("tp.auth.working") : t("tp.auth.upload")}
        </button>
        <button onClick={() => lineItemsRef.current?.click()} disabled={!!busy}
                data-testid="tpl-upload-line-items" style={btn(false)}>
          {busy === "line-items" ? t("tp.auth.working") : t("tp.auth.uploadLineItems")}
        </button>
        <button onClick={() => jsonRef.current?.click()} disabled={!!busy}
                data-testid="tpl-import-json"
                style={{ ...btn(false), color: color.sec2,
                         border: `1px solid ${color.controlBorder}` }}>
          {busy === "json" ? t("tp.auth.working") : t("tp.importTemplate")}
        </button>
      </div>

      <label style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 11,
                      fontSize: 11.5, color: color.sec }}>
        <input type="checkbox" checked={asNew} data-testid="tpl-as-new"
               onChange={(e) => setAsNew(e.target.checked)} />
        {t("tp.auth.asNew")}
      </label>

      {/* The workbook's contract, read from the reader that enforces it — so this screen can
          never describe columns the API does not actually accept. */}
      {cols.data && (
        <div style={{ marginTop: 13, paddingTop: 12, borderTop: `1px solid ${color.hairline}`,
                      fontSize: 11.5, color: color.sec, lineHeight: 1.6 }}>
          <div style={{ fontWeight: 600, color: color.ink, marginBottom: 4 }}>
            {t("tp.auth.columns")}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: 10.5, color: color.sec2,
                        marginBottom: 7 }}>
            {cols.data.columns.map((c) => c.header).join(" · ")}
          </div>
          {cols.data.kinds.map((k) => (
            <div key={k.value}>
              <span style={{ fontFamily: font.mono, fontWeight: 600, color: color.ink }}>
                {k.value}
              </span>{" — "}{k.help}
            </div>
          ))}
        </div>
      )}

      {msg && (
        <div
          data-testid="tpl-auth-message"
          style={{ marginTop: 12, padding: "8px 11px", borderRadius: radius.control,
                   fontSize: 11.5, lineHeight: 1.55,
                   background: msg.ok ? color.indigoTint : color.redBg,
                   color: msg.ok ? color.indigo : color.redFg }}
        >
          {msg.text}
        </div>
      )}
    </div>
  );
}

/** Row order for the index: the versions of one template stay together, newest first — that is
 *  the one in force — and templates are ordered by name. Exported because the screen aims the
 *  authoring controls at the version a reader sees FIRST; defaulting them at the list's oldest
 *  row would mean "Download as Excel" handed back a superseded spread. */
export function sortTemplates(templates: TemplateRef[]): TemplateRef[] {
  return [...templates].sort((a, b) => (a.template_key === b.template_key
    ? b.version - a.version
    : (a.name || a.template_key).localeCompare(b.name || b.template_key)));
}

/** The JSON Schema an uploaded line-item set is validated against, saved as a file.
 *
 * Fetched rather than bundled: it is generated from the pydantic model the upload gate uses, so a
 * copy shipped in the frontend would drift the first time a field is added — and the whole value of
 * handing someone the contract is that it is the contract. Failures are surfaced, because the
 * download is invisible when it works and a silently dead button is indistinguishable from one the
 * browser blocked. */
function SchemaDownload({ t }: { t: (k: string) => string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      const s = await api.lineItemSchema();
      const blob = new Blob([JSON.stringify(s.json_schema, null, 2)],
                            { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `line_items_schema_v${s.schema_version}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {err && (
        <span data-testid="tpl-schema-error" style={{ fontSize: 11, color: color.redFg }}>{err}</span>
      )}
      <button
        onClick={go}
        disabled={busy}
        data-testid="tpl-schema-download"
        title={t("tp.list.schemaHelp")}
        style={{ fontSize: 12, fontWeight: 600, color: color.indigo, background: "#fff",
                 border: `1px solid ${color.indigoBorder2}`, borderRadius: radius.controlSm,
                 padding: "7px 11px", cursor: busy ? "default" : "pointer",
                 whiteSpace: "nowrap" }}
      >
        {busy ? t("tp.list.schemaBusy") : t("tp.list.schema")}
      </button>
    </div>
  );
}

const GRID = "minmax(200px,2.4fr) minmax(130px,1.1fr) 74px 96px minmax(130px,1.2fr)";

function HeadCell({ label }: { label: string }) {
  return (
    <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: 0.4, color: color.muted }}>
      {label}
    </div>
  );
}

/** One template version. The whole row is the affordance — an index exists to be clicked into,
 *  so there is no separate "open" button to hunt for. */
function TemplateRow({ tpl, lineItems, active, onOpen, t }: {
  tpl: TemplateRef; lineItems: LineItemVersionRef | undefined;
  active: boolean; onOpen: () => void; t: (k: string) => string;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      data-testid="tpl-row"
      role="button"
      tabIndex={0}
      title={t("tp.list.openHint")}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "grid", gridTemplateColumns: GRID, alignItems: "center", gap: 12,
        padding: "13px 18px", cursor: "pointer",
        borderTop: `1px solid ${color.hairline}`,
        background: active ? color.indigoTint : hover ? color.rowAltBg : color.surface,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: color.ink }}>
          {tpl.name || tpl.template_key}
        </div>
        {active && (
          <div style={{ fontSize: 10.5, color: color.indigo, marginTop: 2 }}>
            {t("tp.list.activeRow")}
          </div>
        )}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 11, color: color.sec2, overflowWrap: "anywhere" }}>
        {tpl.template_key}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 11.5, fontWeight: 600, color: color.ink2 }}>
        {`v${tpl.version}`}
      </div>
      <div>
        <span style={{ fontSize: 10, fontWeight: 600, padding: "3px 9px", borderRadius: radius.pill,
                       background: tpl.is_published ? color.greenBg : color.rowAltBg,
                       color: tpl.is_published ? color.greenFg : color.muted }}>
          {t(tpl.is_published ? "tp.list.published" : "tp.list.draft")}
        </span>
      </div>
      {/* THE CONFIGURATION IN FORCE for this template key — the line-item version the next run
          will map against (`configurationFor`), named by key and revision.

          THE "IN FORCE, THOUGH SUPERSEDED" BADGE THAT SAT HERE IS GONE with the field behind it.
          It reconciled an ontology row that declared itself replaced while still being the one
          selected; `LineItemVersionRef` has no `superseded`, because selection is "the latest
          stored wins" and `in_force` is the whole answer. Do not reintroduce a second, declarative
          notion of replacement — it is what made this column able to contradict the extractor. */}
      <div data-testid="tpl-row-line-items" style={{ fontSize: 11.5, minWidth: 0 }}>
        {lineItems ? (
          <>
            <span style={{ fontFamily: font.mono, color: color.sec, overflowWrap: "anywhere" }}>
              {lineItems.line_items_key}
            </span>
            <span style={{ color: color.muted }}>{` · v${lineItems.version}`}</span>
          </>
        ) : (
          <span style={{ color: color.muted }}>{t("tp.list.noLineItems")}</span>
        )}
      </div>
    </div>
  );
}

/** Page 1: the index. `activeId` is the version the authoring buttons act on; `onOpen` hands a
 *  row to the screen, which raises the detail over this page — this component stays mounted, so
 *  the filter and the scroll position are exactly where they were when the detail is dismissed.
 *
 *  `covered` says the detail is up over this page. Staying mounted is what preserves the filter
 *  and the scroll, but it also left every row Tab-reachable UNDER the overlay: keyboard focus
 *  walked out of a dirty editor into the list behind it and a row press swapped the subject the
 *  editor was editing. `inert` takes the whole page out of the focus order and off the hit-testing
 *  path while it is covered, without unmounting anything. */
export function TemplateList({
  templates, activeId, canEdit, covered, onPick, onOpen, t,
}: {
  templates: TemplateRef[]; activeId: string | undefined; canEdit: boolean; covered: boolean;
  // Nothing on the index is localized per row any more — the one thing that was, the line-item
  // count, came from a localized per-template document this page no longer fetches. The prop stays
  // because the screen passes it (see Template.tsx) and removing it there is a separate change.
  locale: Locale; onPick: (id: string) => void; onOpen: (id: string) => void;
  t: (k: string) => string;
}) {
  const [filter, setFilter] = useState("");
  const configurations = useLineItemVersions();

  // `inert` is not in React 18's attribute types, so it is spread rather than written as a prop.
  // aria-hidden rides along: a screen reader must not read out a list the pointer and the keyboard
  // cannot reach either.
  const inert: Record<string, string> = covered ? { inert: "" } : {};
  const coveredProps = { ...inert, "aria-hidden": covered || undefined };

  // Nothing configured yet → guidance, plus the authoring desk that is the way out of it.
  if (templates.length === 0) {
    return (
      <div {...coveredProps}
           style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "0 24px" }}>
        <div style={{ maxWidth: 560, margin: "60px auto", textAlign: "center", color: color.muted }}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>◆</div>
          <h1 style={{ fontSize: 18, fontWeight: 600, color: color.ink, marginBottom: 8 }}>
            {t("tp.emptyTitle")}
          </h1>
          <p style={{ fontSize: 12.5, lineHeight: 1.6 }}>{t("tp.emptyHint")}</p>
          {canEdit && (
            <div style={{ maxWidth: 560, margin: "18px auto 0", textAlign: "left" }}>
              <TemplateAuthoring templates={[]} selectedId={undefined} onSelect={onPick} t={t} />
            </div>
          )}
        </div>
      </div>
    );
  }

  // Which configuration a row is honestly described by: the line-item version IN FORCE for its
  // template key, READ from the server's own flag through the shared reader (see
  // configurationInForce) rather than ranked by a local copy of the rule. The copy that stood here
  // compared `version` alone, which named the superseded v1 whenever it had been edited more times
  // than the v2 that replaced it — and would have named a generated skeleton of empty stubs on the
  // same grounds.
  const configurationFor = (key: string) =>
    configurationInForce(configurations.data, (c) => c.target_template_key === key);

  const q = filter.trim().toLowerCase();
  const rows = sortTemplates(
    templates.filter((x) => !q || `${x.name} ${x.template_key}`.toLowerCase().includes(q)));

  return (
    <div data-testid="template-list" {...coveredProps}
         style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "26px 30px 60px" }}>
      <div style={{ maxWidth: layout.screenMax, margin: "0 auto" }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between",
                      gap: 18, marginBottom: 18, flexWrap: "wrap" }}>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("tp.list.title")}</h1>
            <p style={{ margin: 0, color: color.sec2, fontSize: 12.5, maxWidth: 640,
                        lineHeight: 1.55 }}>
              {t("tp.list.subhead")}
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            {/* The SHAPE an uploaded line-item set must have, next to the filter because it
                describes the collection rather than any one version. Generated from the model the
                upload gate validates with, so it cannot describe a rule the gate does not enforce.
                The per-template starter file is a different artifact and lives on the detail page,
                where there is a template to derive it from. */}
            {canEdit && <SchemaDownload t={t} />}
            <input
              value={filter}
              data-testid="tpl-filter"
              placeholder={t("tp.list.filter")}
              onChange={(e) => setFilter(e.target.value)}
              style={{ fontFamily: font.sans, fontSize: 12, color: color.ink, background: "#fff",
                       border: `1px solid ${color.controlBorder}`, borderRadius: radius.controlSm,
                       padding: "7px 11px", outline: "none", minWidth: 230 }}
            />
          </div>
        </div>

        {/* The index comes FIRST — picking a template is what this page is for; publishing a new
            version of one is the occasional task, so the authoring desk sits below it. */}
        <div style={{ background: color.surface, border: `1px solid ${color.cardBorder}`,
                      borderRadius: radius.card, overflow: "hidden", marginBottom: 22 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 9, padding: "14px 18px 12px" }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{t("tp.list.stored")}</span>
            <span style={{ fontSize: 11, color: color.muted }}>
              {t("tp.list.count").replace("{n}", String(rows.length))}
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: GRID, alignItems: "center", gap: 12,
                        padding: "0 18px 9px" }}>
            <HeadCell label={t("tp.list.colName")} />
            <HeadCell label={t("tp.list.colKey")} />
            <HeadCell label={t("tp.list.colVersion")} />
            <HeadCell label={t("tp.list.colState")} />
            <HeadCell label={t("tp.list.colLineItems")} />
          </div>
          {rows.length === 0 ? (
            <div style={{ borderTop: `1px solid ${color.hairline}`, padding: "22px 18px",
                          fontSize: 12, color: color.muted }}>
              {t("tp.list.noMatch")}
            </div>
          ) : rows.map((tpl) => (
            <TemplateRow
              key={tpl.id} tpl={tpl}
              lineItems={configurationFor(tpl.template_key)} active={tpl.id === activeId}
              onOpen={() => onOpen(tpl.id)} t={t}
            />
          ))}
        </div>

        {canEdit && (
          <TemplateAuthoring templates={templates} selectedId={activeId} onSelect={onPick} t={t} />
        )}
      </div>
    </div>
  );
}
