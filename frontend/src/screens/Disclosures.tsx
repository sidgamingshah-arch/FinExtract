/** Disclosures — the qualitative scan, and the figures the pipeline computed for it.
 *
 * These were reachable in exactly two places: a card at the very BOTTOM of the Extraction screen,
 * below the rows grid and the page-source panel, and a sheet in the Excel export. On a 367-page
 * filing that card is a long scroll past the thing most readers came for, and a reviewer looking
 * for contingent liabilities had no reason to expect it there. Given its own destination it is
 * where its name says it is.
 *
 * Deliberately its own screen rather than a strip of tabs inside Extraction: this app's tabs ARE
 * its nav destinations (All Notes, Review Queue, Analysis are each one), so a local tab strip would
 * be a second, competing idiom for the same idea.
 */
import { Card } from "../components/ui";
import { EmptyState } from "../components/EmptyState";
import { useT } from "../i18n";
import { useDocumentAnalysis } from "../lib/queries";
import { useUI } from "../store";
import { color, font } from "../theme";
import type { Disclosure } from "../types";

/** A money string as a figure. Blank for null, never 0 — see `Disclosure.amount`. */
function amount(value: string | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return `${currency ? `${currency} ` : ""}${n.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** The working behind a quantified disclosure: the sentence, the per-type sums, the leftovers.
 *
 * Renders nothing at all when the pipeline computed no working, which is every disclosure except
 * contingent liabilities today — so this cannot change how a qualitative entry looks. */
function DisclosureWorking({ d }: { d: Disclosure }) {
  const breakdown = d.breakdown ?? [];
  const statements = d.statements ?? [];
  const explanation = (d.explanation ?? "").trim();
  if (!breakdown.length && !statements.length && !explanation) return null;

  const label = { fontSize: 10, fontWeight: 700 as const, letterSpacing: 0.3,
                  color: color.muted, textTransform: "uppercase" as const };
  const fig = { fontSize: 12, color: color.ink, textAlign: "right" as const,
                fontVariantNumeric: "tabular-nums" as const, fontFamily: font.mono };

  return (
    <div data-testid={`disc-working-${d.key}`}
         /* `gridColumn: 1 / -1` — the row above is a 4-column grid and this is a child of it, so
            spanning the whole width is what puts the working UNDER the disclosure it explains
            instead of inside one of its cells. */
         style={{ gridColumn: "1 / -1", marginTop: 8, paddingLeft: 14,
                  borderLeft: `2px solid ${color.hairline2}`,
                  display: "flex", flexDirection: "column", gap: 10 }}>
      {explanation && (
        <div data-testid={`disc-explanation-${d.key}`}
             style={{ fontSize: 11.5, color: color.sec2, lineHeight: 1.6 }}>
          {explanation}
        </div>
      )}
      {breakdown.length > 0 && (
        <div>
          <div style={label}>Exposure by type</div>
          {breakdown.map((g, i) => (
            <div key={`${g.type}-${g.currency}-${i}`}
                 data-testid={`disc-breakdown-${d.key}`}
                 style={{ display: "grid", gridTemplateColumns: "1.4fr 1.1fr 1.4fr",
                          gap: 12, padding: "3px 0", alignItems: "baseline" }}>
              <span style={{ fontSize: 12, color: color.ink }}>{g.type}</span>
              <span style={fig}>{amount(g.amount, g.currency)}</span>
              <span style={{ fontSize: 10.5, color: color.faint }}>
                {[g.item_count ? `${g.item_count} disclosed item${g.item_count === 1 ? "" : "s"}` : "",
                  g.source_pages?.length ? `p.${g.source_pages.join(", p.")}` : ""]
                  .filter(Boolean).join(" · ")}
              </span>
            </div>
          ))}
        </div>
      )}
      {statements.length > 0 && (
        <div>
          <div style={label}>Matters not classified to a type</div>
          {statements.map((s, i) => (
            <div key={`${s.statement}-${i}`}
                 data-testid={`disc-statement-${d.key}`}
                 style={{ display: "grid", gridTemplateColumns: "1.4fr 1.1fr 1.4fr",
                          gap: 12, padding: "3px 0", alignItems: "baseline" }}>
              <span style={{ fontSize: 11.5, color: color.ink, lineHeight: 1.5 }}>
                {s.statement}
              </span>
              {/* Blank, never 0: "the amount was not disclosed" and "the exposure is nil" are
                  different answers and the statement itself already says which. */}
              <span style={fig}>{amount(s.amount, s.currency)}</span>
              <span style={{ fontSize: 10.5, color: color.faint }}>
                {[s.source_note ? `note ${s.source_note}` : "",
                  s.page ? `p.${s.page}` : ""].filter(Boolean).join(" · ")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function DisclosuresScreen() {
  const t = useT();
  // The same store fields every other per-document screen reads, so this one follows the
  // document the rest of the app is on rather than keeping a selection of its own.
  const id = useUI((st) => st.activeDocumentId);
  const locale = useUI((st) => st.locale);
  // `activeDocumentId` is `string | null` in the store while the query takes `string | undefined`,
  // and the query is `enabled: !!documentId` either way — so the null is normalised rather than
  // asserted away.
  const q = useDocumentAnalysis(id ?? undefined, locale);

  if (!id) return <EmptyState />;

  return (
    <div style={{ padding: "28px 32px", maxWidth: 1180 }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 4px" }}>
          {t("disc.title")}
        </h1>
        <p style={{ margin: 0, color: color.sec2, fontSize: 12.5 }}>{t("disc.subtitle")}</p>
      </div>

      {/* A FAILED READ SAYS SO. The card this replaces returned null on error, so ratios,
          disclosures and notes all vanished together with nothing on screen to explain it — and
          the query does not retry, so one transient failure left the section simply absent until
          a reload. "Not available" and "nothing disclosed" are different answers and must not
          look alike. */}
      {q.isError && (
        <Card>
          <div data-testid="disc-error" style={{ fontSize: 12.5, color: color.ink }}>
            {t("disc.unavailable")}{" "}
            <span style={{ fontFamily: font.mono, fontSize: 11, color: color.sec2 }}>
              {(q.error as Error)?.message}
            </span>
          </div>
        </Card>
      )}

      {!q.data && !q.isError && (
        <Card>
          <div style={{ fontSize: 12.5, color: color.muted }}>{t("disc.loading")}</div>
        </Card>
      )}

      {q.data && (
        <Card>
          <div style={{ display: "grid", gridTemplateColumns: "1.5fr 80px 1.2fr 3fr",
                        gap: 12, padding: "0 0 8px",
                        borderBottom: `1px solid ${color.hairline2}`,
                        fontSize: 11, fontWeight: 700, letterSpacing: 0.3, color: color.muted }}>
            <span>{t("disc.col.item")}</span>
            <span>{t("disc.col.page")}</span>
            <span style={{ textAlign: "right" }}>{t("disc.col.amount")}</span>
            <span>{t("disc.col.evidence")}</span>
          </div>
          {q.data.disclosures.map((d) => (
            <div key={d.key} data-testid={`disc-row-${d.key}`}
                 /* Top-aligned, not centred: the evidence cell now carries the WHOLE disclosure
                    passage, so a row can be many lines tall and a centred label would float away
                    from the text it names. */
                 style={{ display: "grid", gridTemplateColumns: "1.5fr 80px 1.2fr 3fr",
                          gap: 12, alignItems: "start", padding: "8px 0",
                          borderBottom: `1px solid ${color.hairline2}` }}>
              <span style={{ fontSize: 12.5, color: color.ink }}>{d.label}</span>
              <span style={{ fontSize: 10.5, fontWeight: 700,
                             color: d.present ? color.greenFg : color.faint }}>
                {d.present ? t("view.folioN").replace("{n}", String(d.page)) : "—"}
              </span>
              {/* Blank, never 0, when no amount was computed: most of these are qualitative, and
                  for the quantified one an empty cell means "no figure was disclosed", which is a
                  different statement from "the exposure is nil". */}
              <span data-testid={`disc-amount-${d.key}`}
                    style={{ fontSize: 12.5, color: color.ink, textAlign: "right",
                             fontVariantNumeric: "tabular-nums" }}>
                {d.amount
                  ? `${d.currency ? `${d.currency} ` : ""}${Number(d.amount).toLocaleString(
                      undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : ""}
              </span>
              <span style={{ fontSize: 11.5, color: color.muted, lineHeight: 1.5,
                             fontStyle: d.snippet ? "italic" : "normal" }}>
                {d.snippet || t("ex.notFound")}
              </span>
              {/* HOW THE FIGURE WAS ARRIVED AT. Spans all four columns beneath the row rather
                  than squeezing into the evidence cell: it is a variable number of lines — one
                  per type, one per unclassified paragraph — and the amounts have to line up
                  under one another to be read as a breakdown that sums. Rendered only where the
                  pipeline computed one, so every other disclosure looks exactly as before. */}
              <DisclosureWorking d={d} />
            </div>
          ))}
          {!q.data.disclosures.length && (
            <div style={{ fontSize: 12.5, color: color.muted, paddingTop: 10 }}>
              {t("disc.none")}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
