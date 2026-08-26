/** Screen 8: Export — deliver the extracted, reviewed and reconciled dataset. */
import { useState } from "react";
import { useDocumentRun, useExportOptions, useProject, useProjectLoaded, useSubmitForReview } from "../lib/queries";
import { EmptyState } from "../components/EmptyState";
import { downloadDocumentExport, downloadExport } from "../lib/api";
import { useUI } from "../store";
import type { ExportFmt, ExtractionRow, ExtractionValue } from "../types";
import { EXPORT_EXT } from "../types";
import { useT } from "../i18n";
import { useCan } from "../lib/rbac";
import { color, font, radius } from "../theme";
import { Card } from "../components/ui";

/** One selectable format card (Excel / JSON). */
function FormatCard({
  glyph,
  label,
  sub,
  selected,
  onClick,
}: {
  glyph: string;
  label: string;
  sub: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        flex: 1,
        border: `1.5px solid ${selected ? color.indigo : color.cardBorder}`,
        background: selected ? color.indigoTint : color.surface,
        borderRadius: 10,
        padding: 15,
        cursor: "pointer",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: 22, marginBottom: 5 }}>{glyph}</div>
      <div style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 10.5, color: color.muted }}>{sub}</div>
    </div>
  );
}

/** One include-checklist row, for both the real and the sample export — ONE spelling of one
 *  control. The sample rows used to be a separate presentational component with no onChange,
 *  wrapped in `pointerEvents: auto` for an admin, so they advertised a choice and delivered none
 *  (and the sample download posted `include: {}`, discarding it anyway). */
function IncludeRow({ label, on, onToggle }: { label: string; on: boolean; onToggle: () => void }) {
  return (
    <div
      onClick={onToggle}
      data-testid="e-include"
      data-on={on}
      style={{ display: "flex", alignItems: "center", gap: 9, padding: "7px 0", cursor: "pointer" }}
    >
      <span
        style={{
          width: 15, height: 15, borderRadius: 4, flex: "0 0 auto",
          border: `2px solid ${on ? color.indigo : color.dashed}`,
          background: on ? color.indigo : "#fff", color: "#fff",
          fontSize: 11, lineHeight: "11px", textAlign: "center",
        }}
      >
        {on ? "✓" : ""}
      </span>
      <span style={{ fontSize: 12 }}>{label}</span>
    </div>
  );
}

/** A labeled presentation field mock. */
function PresField({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 11, color: color.muted, marginBottom: 4 }}>{label}</div>
      <div
        style={{
          border: `1px solid ${color.cardBorder}`,
          borderRadius: 7,
          padding: "7px 10px",
          fontSize: 12,
          fontWeight: 500,
        }}
      >
        {value}
      </div>
    </div>
  );
}

function provStr(r: ExtractionRow): string {
  const p = r.values?.[0]?.provenance;
  if (!p) return "";
  if (p.source_kind === "spreadsheet" && p.sheet) return `${p.sheet}!${p.cell ?? ""}`;
  // Character-for-character what `services/export._prov_str` writes into the downloaded file. This
  // string appears in the JSON preview as well as the Source column, so a mismatch here would make
  // the preview lie about the artifact.
  return `p.${p.printed_page || p.page_index + 1}`;
}

/** The CSV as the server writes it, from the rows the screen already has.
 *
 *  A ROW PER LINE ITEM AND A COLUMN PER FIGURE, widening with the filing — the same rule as
 *  `services/export.build_rows_csv`, because a preview whose shape differs from the download's is
 *  worse than no preview. Kept beside it deliberately: the widening is the interesting part of
 *  that format, and it is what a reader is checking here.
 *
 *  The basis word comes from `ws.consolidated` / `ws.standalone`, the vocabulary the rest of this
 *  app shows. NOTE that `services/export._BASIS_LABEL` spells "standalone" differently in zh and
 *  ar (单独 / مستقل there, 单体 / منفصل here) — a disagreement that predates this screen and that
 *  the delivered file wins, so a zh or ar reader can see one word here and the other in the
 *  download. Not settled silently in passing: which spelling is right is a wording decision, and
 *  the same two dictionaries head the statement workbook's columns and the Workspace's own tabs.
 */
const BASIS_ORDER: Record<string, number> = { consolidated: 0, standalone: 1 };

function csvPreviewGrid(
  rows: ExtractionRow[], basisWord: (b: string) => string, valueWord: string,
): { headers: string[]; body: string[][] } {
  // First appearance IS printed order (a row's values are read left to right), and the sort by
  // basis is stable, so it groups the bases without disturbing that.
  const first = new Map<string, ExtractionValue>();
  for (const r of rows)
    for (const v of r.values ?? []) {
      const k = `${v.basis || "consolidated"}\u0000${v.period_label ?? ""}`;
      if (!first.has(k)) first.set(k, v);
    }
  const keys = [...first.keys()].sort(
    (a, b) => (BASIS_ORDER[a.split("\u0000")[0]] ?? 2) - (BASIS_ORDER[b.split("\u0000")[0]] ?? 2));
  const withBasis = new Set([...first.values()].map((v) => v.basis || "consolidated")).size > 1;
  const names = keys.length <= 1 ? [valueWord] : keys.map((k) => {
    const v = first.get(k)!;
    const period = String(v.period_display || v.period_label || "").trim();
    const named = withBasis ? `${basisWord(v.basis || "consolidated")} ${period}`.trim() : period;
    return named || valueWord;
  });
  const headers = names.map((n, i) => (names.filter((m) => m === n).length > 1 ? `${n} (${i + 1})` : n));
  // `keys.length || 1` cells per row: with nothing extracted the header still promises a Value
  // column, so the row has to carry the cell or the sheet stops being rectangular.
  const width = keys.length || 1;
  const body = rows.map((r) => {
    const bySlot = new Map(
      (r.values ?? []).map((v) => [`${v.basis || "consolidated"}\u0000${v.period_label ?? ""}`, v]));
    return [r.source_label || r.canonical_key || "",
            ...Array.from({ length: width }, (_, i) => {
              const v = keys[i] === undefined ? undefined : bySlot.get(keys[i]);
              return v?.value == null ? "" : String(v.value);
            })];
  });
  return { headers, body };
}

/** Real export preview built from the document's actual extracted rows (no demo data). */
function RealPreview({ rows, fmt }: { rows: ExtractionRow[]; fmt: ExportFmt }) {
  const t = useT();
  const isExcel = fmt === "excel";
  if (fmt === "csv") {
    const { headers, body } = csvPreviewGrid(
      rows, (b) => t(b === "standalone" ? "ws.standalone" : "ws.consolidated"), t("e.col.value"));
    // The value columns are fixed-width and the caption takes the rest, so a filing with four of
    // them still shows a readable caption; the whole grid scrolls sideways rather than the page.
    const csvCols = `minmax(200px, 1fr) repeat(${headers.length}, 110px)`;
    return (
      <div style={{ fontFamily: font.mono, fontSize: 11, overflowX: "auto" }}>
        <div style={{ minWidth: 200 + headers.length * 110 }}>
          <div style={{ display: "grid", gridTemplateColumns: csvCols, background: color.excelGreen,
                        color: "#fff", fontWeight: 600 }}>
            {[t("e.col.lineitem"), ...headers].map((h, i) => (
              <div key={i} style={{ padding: "6px 8px", borderRight: "1px solid #1a5c37",
                                    textAlign: i === 0 ? "left" : "right" }}>{h}</div>
            ))}
          </div>
          {body.slice(0, 60).map((cells, ri) => (
            <div key={ri} style={{ display: "grid", gridTemplateColumns: csvCols,
                                   background: ri % 2 ? "#f6f8f6" : "#fff",
                                   borderBottom: "1px solid #e6ebe6" }}>
              {cells.map((cell, ci) => (
                <div key={ci} style={{ padding: "6px 8px", borderRight: "1px solid #eef1ee",
                                       textAlign: ci === 0 ? "left" : "right" }}>
                  {ci === 0 ? cell : cell || "—"}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (!isExcel) {
    const sample = {
      source_document: "(this document)",
      line_item_count: rows.length,
      line_items: rows.slice(0, 6).map((r) => ({
        source_label: r.source_label,
        canonical_key: r.canonical_key,
        note: r.note,
        mapping_confidence: r.mapping_confidence,
        values: r.values.map((v) => ({ period: v.period_label, value: v.value, source: provStr(r) })),
      })),
    };
    return (
      <pre style={{ margin: 0, padding: "14px 16px", fontFamily: font.mono, fontSize: 11,
                    lineHeight: 1.6, color: color.ink, whiteSpace: "pre-wrap" }}>
        {JSON.stringify(sample, null, 2)}
      </pre>
    );
  }
  // Six headers, six localized strings. The third was the English literal "Value" between five
  // translated ones, so a zh reader saw "行项目 | Value | 附注 | 置信度 | 来源" — the missing-key half
  // of this array was fixed while the untranslated-literal half stayed in the same expression.
  const head = [
    "", t("e.col.lineitem"), t("e.col.value"), t("e.col.note"), t("e.col.conf"), t("e.col.source"),
  ];
  const cols = "30px 1fr 90px 46px 56px 80px";
  return (
    <div style={{ fontFamily: font.mono, fontSize: 11 }}>
      <div style={{ display: "grid", gridTemplateColumns: cols, background: color.excelGreen, color: "#fff", fontWeight: 600 }}>
        {head.map((h, i) => (
          <div key={i} style={{ padding: "6px 8px", borderRight: "1px solid #1a5c37", textAlign: i > 1 && i < 5 ? "right" : "left" }}>{h}</div>
        ))}
      </div>
      {rows.slice(0, 40).map((r, ri) => {
        const conf = r.mapping_confidence;
        return (
          <div key={ri} style={{ display: "grid", gridTemplateColumns: cols,
                                  background: ri % 2 ? "#f6f8f6" : "#fff", borderBottom: "1px solid #e6ebe6" }}>
            <div style={{ padding: "6px 8px", borderRight: "1px solid #eef1ee" }}>{ri + 1}</div>
            <div style={{ padding: "6px 8px", borderRight: "1px solid #eef1ee" }}>{r.source_label}</div>
            <div style={{ padding: "6px 8px", borderRight: "1px solid #eef1ee", textAlign: "right" }}>{r.values?.[0]?.value ?? "—"}</div>
            <div style={{ padding: "6px 8px", borderRight: "1px solid #eef1ee" }}>{r.note ?? ""}</div>
            <div style={{ padding: "6px 8px", borderRight: "1px solid #eef1ee", textAlign: "right", color: color.amberFg }}>
              {typeof conf === "number" ? `${Math.round(conf * 100)}%` : "—"}
            </div>
            <div style={{ padding: "6px 8px", color: color.muted }}>{provStr(r)}</div>
          </div>
        );
      })}
    </div>
  );
}

export default function ExportScreen() {
  const t = useT();
  const canConfig = useCan("config:export");
  const canExport = useCan("export:run");
  const canSubmit = useCan("review:submit");
  const submitReview = useSubmitForReview();
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  const loaded = useProjectLoaded();
  // The sample footer's counts, as the demo project itself reports them — the same payload
  // useProjectLoaded already reads, so no extra request and no second copy of the figures.
  const sampleProgress = useProject().data?.project.progress;
  const { data, isPending } = useExportOptions();
  const runQ = useDocumentRun(activeDocumentId ?? undefined);
  const exportFmt = useUI((s) => s.exportFmt);
  const setFmt = useUI((s) => s.setFmt);
  const outputLocale = useUI((s) => s.locale);
  const isExcel = exportFmt === "excel";
  // Interactive Include selection for a real export — drives which analysis sheets are added.
  const [inc, setInc] = useState<Record<string, boolean>>({
    note_details: true, ratios: true, disclosures: true,
  });
  const includeKeys = Object.keys(inc).filter((k) => inc[k]);
  // The SAMPLE export's checklist. Null until the user touches it, so the boxes start at whatever
  // the server declared (`option.on`) instead of a second default kept here — and the map that is
  // shown is the map that is posted, which the sample download used to drop on the floor.
  const [sampleInc, setSampleInc] = useState<Record<string, boolean> | null>(null);
  // Target presentation unit for a real export (empty = as reported). Conversion applies only
  // when the document declared its source units, which the run reports.
  const [targetUnits, setTargetUnits] = useState<string>("");
  const srcUnits = usingReal ? runQ.data?.result.units ?? null : null;

  // No real document and no admin-seeded demo → greenfield guidance.
  if (!usingReal && !loaded) return <EmptyState />;
  if (usingReal && runQ.isError) return <EmptyState />;   // uploaded but not extracted yet
  if (usingReal ? runQ.isPending || !runQ.data : isPending || !data) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: color.muted }}>Loading…</div>
    );
  }

  const realRows: ExtractionRow[] = usingReal ? (runQ.data?.result.rows ?? []) : [];
  // Rows this export would carry with no canonical mapping, or carrying an extraction flag. That
  // is NOT the sample path's review backlog, so the footer labels the two separately.
  const realFlagged = realRows.filter((r) => !r.canonical_key || (r.flags?.length ?? 0) > 0).length;
  // Server defaults until the user overrides one, per option — never a client-side copy of them.
  const sampleInclude: Record<string, boolean> =
    sampleInc ?? Object.fromEntries((data?.options ?? []).map((o) => [o.key, o.on]));

  return (
    <div style={{ maxWidth: 1120, margin: "0 auto", padding: "26px 30px 60px" }}>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("e.title")}</h1>
        <p style={{ margin: 0, color: color.sec2 }}>{t("e.subhead")}</p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.25fr", gap: 18 }}>
        {/* LEFT */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Card>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>{t("e.format")}</div>
            <div style={{ display: "flex", gap: 11 }}>
              <FormatCard
                glyph="▦"
                label={t("e.excel")}
                sub={t("e.excelSub")}
                selected={isExcel}
                onClick={() => setFmt("excel")}
              />
              {/* `selected={!isExcel}` while there were two formats. With three, "not Excel" is
                  not JSON, and the JSON card would have stayed lit under a CSV selection. */}
              <FormatCard
                glyph="{ }"
                label={t("e.json")}
                sub={t("e.jsonSub")}
                selected={exportFmt === "json"}
                onClick={() => setFmt("json")}
              />
              <FormatCard
                glyph="⋮⋮"
                label={t("e.csv")}
                sub={t("e.csvSub")}
                selected={exportFmt === "csv"}
                onClick={() => setFmt("csv")}
              />
            </div>
          </Card>

          {usingReal ? (
            isExcel && (
              <Card>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 11 }}>{t("e.include")}</div>
                <div style={{ opacity: canConfig ? 1 : 0.55, pointerEvents: canConfig ? "auto" : "none" }}>
                  {(["note_details", "ratios", "disclosures"] as const).map((k) => (
                    <IncludeRow key={k} label={t(`e.sheet.${k}`)} on={inc[k]}
                                onToggle={() => setInc((s) => ({ ...s, [k]: !s[k] }))} />
                  ))}
                </div>
              </Card>
            )
          ) : (
            data?.options && (
              <Card>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 11 }}>{t("e.include")}</div>
                <div style={{ opacity: canConfig ? 1 : 0.55, pointerEvents: canConfig ? "auto" : "none" }}>
                  {data.options.map((o) => (
                    <IncludeRow
                      key={o.key}
                      label={t(`e.opt.${o.key}`)}
                      on={sampleInclude[o.key] ?? o.on}
                      onToggle={() =>
                        setSampleInc({ ...sampleInclude, [o.key]: !(sampleInclude[o.key] ?? o.on) })}
                    />
                  ))}
                </div>
              </Card>
            )
          )}

          {/* Real presentation control: convert figures to a chosen unit — enabled only when
              the document declared its source units (otherwise we never guess a scale). Offered
              for CSV as well as Excel: the unit is a conversion of the FIGURES, which that format
              carries, unlike the Include list, which adds analysis SHEETS it has no room for.
              JSON is left out because it reports the source units as data instead. */}
          {usingReal && exportFmt !== "json" && (
            <Card>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{t("e.presentation")}</div>
              <div style={{ fontSize: 11, color: color.muted, marginBottom: 8 }}>
                {srcUnits && (srcUnits.units_label || srcUnits.currency)
                  ? `${t("e.sourceUnits")}: ${srcUnits.currency || ""} ${srcUnits.units_label || ""}`.trim()
                  : t("e.unitsAsReported")}
              </div>
              <select
                value={targetUnits}
                disabled={!canConfig || !srcUnits?.units_label}
                onChange={(e) => setTargetUnits(e.target.value)}
                style={{ width: "100%", fontSize: 12, padding: "7px 10px",
                         borderRadius: radius.controlSm, border: `1px solid ${color.cardBorder}` }}
              >
                <option value="">{t("e.unitsAsReported")}</option>
                <option value="absolute">{t("e.units.absolute")}</option>
                <option value="thousands">{t("e.units.thousands")}</option>
                <option value="millions">{t("e.units.millions")}</option>
                <option value="lakh">{t("e.units.lakh")}</option>
                <option value="crore">{t("e.units.crore")}</option>
              </select>
            </Card>
          )}

          {/* Presentation (currency/units) is a demo affordance; hide it on a real run
              rather than show fabricated INR/Crore defaults. */}
          {!usingReal && (
            <Card>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 11 }}>{t("e.presentation")}</div>
              <div
                style={{
                  display: "flex",
                  gap: 10,
                  opacity: canConfig ? 1 : 0.55,
                  pointerEvents: canConfig ? "auto" : "none",
                }}
              >
                <PresField label={t("e.dataset")} value={`${t("e.both")} ▾`} />
                <PresField label={t("e.currency")} value="INR ₹ ▾" />
                <PresField label={t("e.units")} value="Crore ▾" />
              </div>
            </Card>
          )}
        </div>

        {/* RIGHT — preview */}
        <Card pad={0} style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "12px 16px",
              borderBottom: `1px solid ${color.hairline3}`,
            }}
          >
            <span style={{ fontSize: 12.5, fontWeight: 600 }}>
              {t("e.preview")} extract.{EXPORT_EXT[exportFmt]}
            </span>
            <span style={{ fontSize: 11, color: color.muted }}>{t("e.previewMeta")}</span>
          </div>
          <div style={{ flex: 1, overflow: "auto", background: isExcel ? "#fff" : "#fbfcfd" }}>
            {/* One preview, of the loaded extraction. The sample path used to render a mock built
                from five hardcoded Reliance rows under an "FY25" header — figures, note refs and
                confidences belonging to no extraction this product has ever produced, on the screen
                whose whole job is showing what the file will contain. There is nothing to preview
                without a document, and saying so is the honest answer. */}
            {usingReal
              ? <RealPreview rows={realRows} fmt={exportFmt} />
              : <div style={{ padding: 22, fontSize: 12, color: color.muted, lineHeight: 1.6 }}>
                  {t("e.previewNeedsDoc")}
                </div>}
          </div>
          <div
            style={{
              padding: "13px 16px",
              borderTop: `1px solid ${color.hairline3}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            {/* Each path names the quantity IT counts — BOTH figures, not just the second one.
                "flagged" used to label the real path's rows-with-no-mapping-or-a-flag and the
                sample's review backlog alike — one word over two different quantities, which is how
                a 12 the payload no longer contained went on reading as "flagged" after the literals
                were deleted from this markup. The first figure had the same fault left in it: one
                word, "line items", over the sample's served count of ITEM rows and over
                `realRows.length`.

                THOSE ARE NOT THE SAME POPULATION. A served row's `role` is the extractor's
                (routes/extractions.py::_serialize_rows over LineItem.role): `line`, or the
                `subtotal` the mapper promotes a row to when it lands on a subtotal concept
                (stages/map_ontology.py, the only place a role ever changes) — and a caption word
                never becomes a line item at all, so no row here is one. `realRows.length` is
                therefore exactly the statement LINES that services/review_lines.py defines for both
                routes, the population the Review header's third tile counts within on this same run,
                while a subtotal is a line and is NOT a line item — which is why the sample's own
                count leaves its subtotal and total rows out. So the number needs no filtering and
                did not change: the WORD was the half asserting a membership its number did not have.
                Nothing is printed until the query resolves: a pending request is not an empty
                project. */}
            <span data-testid="e-footer-counts" style={{ fontSize: 11.5, color: color.muted }}>
              {usingReal
                ? `${realRows.length} ${t("e.footer.lines")} · `
                  + `${realFlagged} ${t("e.footer.unmappedOrFlagged")}`
                : sampleProgress
                  ? `${sampleProgress.line_items} ${t("e.footer.lineitems")} · `
                    + `${sampleProgress.in_review} ${t("e.footer.inReview")}`
                  : ""}
            </span>
            {canExport ? (
              // Reviewer/admin, or analyst when the review step is off: deliver the file.
              <button
                onClick={() =>
                  usingReal && activeDocumentId
                    ? downloadDocumentExport(activeDocumentId, exportFmt, outputLocale, includeKeys,
                                             targetUnits || undefined)
                    : downloadExport({
                        format: exportFmt,
                        basis: "consolidated",
                        currency: "INR",
                        units: "crore",
                        // What the checklist above shows. It was `{}`, so the workbook ignored
                        // every box the screen had just presented as a choice.
                        include: sampleInclude,
                      })
                }
                style={{
                  fontSize: 13, fontWeight: 600, color: "#fff", background: color.indigo,
                  border: "none", borderRadius: 9, padding: "10px 22px", cursor: "pointer",
                }}
              >
                {t("e.download")} .{EXPORT_EXT[exportFmt]}
              </button>
            ) : canSubmit ? (
              // Analyst with the review step on: hand the final output to the reviewer.
              submitReview.isSuccess ? (
                <span style={{ fontSize: 12.5, fontWeight: 600, color: color.greenFg }}>
                  ✓ {t("e.submitted")}
                </span>
              ) : (
                <button
                  // The document being submitted, so the audit entry names the filing's own entity
                  // rather than the demo company's. Undefined for the sample project.
                  onClick={() => submitReview.mutate(activeDocumentId ?? undefined)}
                  disabled={submitReview.isPending}
                  style={{
                    fontSize: 13, fontWeight: 600, color: "#fff",
                    background: submitReview.isPending ? color.faint : color.indigo,
                    border: "none", borderRadius: 9, padding: "10px 22px",
                    cursor: submitReview.isPending ? "default" : "pointer",
                  }}
                >
                  {submitReview.isPending ? t("e.sending") : `${t("e.sendForReview")} →`}
                </button>
              )
            ) : null}
          </div>
        </Card>
      </div>
    </div>
  );
}
