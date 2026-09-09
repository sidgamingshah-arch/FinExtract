/** Line items — the configuration behind the eight output lines and their sub-line items.
 *
 * WHY THIS SCREEN EXISTS. Those eight lines are each assembled from parts that lived only as
 * Python: `deprec_impairment` alone reads thirteen note-level datasets and resolves them through a
 * five-rung cascade, and the template declares the eight with no children at all — so nothing
 * outside that module knew the parts existed. Widening one caption meant editing a
 * 162-alternative regex and shipping a release. This is the first surface on which the whole
 * arrangement can be READ.
 *
 * READ-ONLY, deliberately, and it says so. The definitions describe derivations that five
 * services still compute (`implemented_by` names which), so nothing downstream reads them yet.
 * Showing them before they drive anything is what lets the configuration be checked against what
 * the pipeline actually does — the opposite order to the one that put an unreviewable regex into
 * production.
 */
import { useState } from "react";

import { Card } from "../components/ui";
import { useT } from "../i18n";
import { useLineItems } from "../lib/queries";
import { color, font, radius } from "../theme";
import type {
  LineItemDef, LineItemTerm, LineItemType, LineItemVersionRef, SearchScope,
} from "../types";

const TYPE_TONE: Record<LineItemType, { bg: string; fg: string; label: string }> = {
  extracted: { bg: color.greenBg, fg: color.greenFg, label: "Extracted" },
  calculated: { bg: color.indigoTint2, fg: color.indigo, label: "Calculated" },
  intermediate: { bg: color.segBg, fg: color.sec2, label: "Intermediate" },
  derived: { bg: color.amberBg, fg: color.amberFg, label: "Derived" },
};

const SCOPE_LABEL: Record<SearchScope, string> = {
  notes: "Notes to the accounts",
  balance_sheet: "Balance sheet",
  profit_and_loss: "Profit & loss",
  cash_flow: "Cash flow",
  equity_changes: "Changes in equity",
  covenants_supplemental: "Covenants / supplemental",
  statement_setup: "Statement setup",
  front_matter: "Chairman / MD&A",
};

/** The seven statements a line item can be gated to, as a reader names them. */
const STATEMENT_LABEL: Record<string, string> = {
  statement_setup: "Statement setup",
  balance_sheet: "Balance sheet",
  profit_and_loss: "Profit & loss",
  cash_flow: "Cash flow",
  equity_changes: "Changes in equity",
  covenants_supplemental: "Covenants / supplemental",
  notes: "Notes",
};

const SIDE_LABEL: Record<string, string> = {
  from_section: "From section", asset: "Asset", liability: "Liability",
  equity: "Equity", none: "n/a",
};

const ROLLUP_HELP: Record<string, string> = {
  sum: "the parts below add up to this line",
  alternatives: "the parts below are alternative sources for ONE figure — never summed",
  none: "this line has no parts configured yet",
};

const ROLE_HELP: Record<string, string> = {
  required: "must be present, or the rung fails",
  any_of: "one of this group is enough; those present are summed",
  adjustment: "applied when present, and never justifies the rung on its own",
};

function Tag({ type }: { type: LineItemType }) {
  const t = TYPE_TONE[type];
  return (
    <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.3, textTransform: "uppercase",
                    padding: "3px 7px", borderRadius: 4, background: t.bg, color: t.fg,
                    whiteSpace: "nowrap" }}>
      {t.label}
    </span>
  );
}

/** A monospace list of patterns, scrollable in its own box — some of these are long, and a
 *  caption list that clips is the thing this screen was built to stop being invisible. */
function Patterns({ label, values, tone }: { label: string; values: string[]; tone?: string }) {
  if (!values.length) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.3, textTransform: "uppercase",
                    color: color.muted, marginBottom: 5 }}>
        {label} <span style={{ fontFamily: font.mono, fontWeight: 500 }}>({values.length})</span>
      </div>
      <div style={{ maxHeight: 132, overflowY: "auto", border: `1px solid ${color.hairline2}`,
                    borderRadius: 7, background: color.rowAltBg }}>
        {values.map((v, i) => (
          <div key={`${v}-${i}`}
               style={{ fontFamily: font.mono, fontSize: 11, padding: "4px 8px",
                        color: tone ?? color.ink2, wordBreak: "break-all",
                        borderBottom: i === values.length - 1 ? "none"
                                                              : `1px solid ${color.hairline}` }}>
            {v}
          </div>
        ))}
      </div>
    </div>
  );
}

function Terms({ terms, byKey }: { terms: LineItemTerm[]; byKey: Map<string, LineItemDef> }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {terms.map((t, i) => (
        <div key={i}
             style={{ display: "grid", gridTemplateColumns: "22px 1fr auto", gap: 8,
                      alignItems: "baseline", padding: "5px 8px", borderRadius: 6,
                      background: color.rowAltBg, border: `1px solid ${color.hairline}` }}>
          <span style={{ fontFamily: font.mono, fontSize: 12, fontWeight: 600,
                          color: t.sign < 0 ? color.redFg : color.ink }}>
            {t.sign < 0 ? "−" : "+"}
          </span>
          <span style={{ fontSize: 12, color: color.ink, minWidth: 0 }}>
            {t.abs && <span style={{ fontFamily: font.mono, color: color.indigo }}>abs( </span>}
            {t.const !== null
              ? <span style={{ fontFamily: font.mono }}>{t.const.toLocaleString()}</span>
              : (byKey.get(t.ref)?.label || t.ref)}
            {t.abs && <span style={{ fontFamily: font.mono, color: color.indigo }}> )</span>}
            {t.const === null && (
              <span style={{ display: "block", fontFamily: font.mono, fontSize: 9.5,
                              color: color.faint }}>{t.ref}</span>
            )}
          </span>
          <span title={ROLE_HELP[t.role]}
                style={{ fontSize: 9.5, fontWeight: 700, textTransform: "uppercase",
                          letterSpacing: 0.3, padding: "2px 5px", borderRadius: 3,
                          background: t.role === "adjustment" ? color.amberBg : color.segBg,
                          color: t.role === "adjustment" ? color.amberFg : color.sec2 }}>
            {t.role === "any_of" ? "any of" : t.role}
          </span>
        </div>
      ))}
    </div>
  );
}

/** The detail for one line item. What it shows is decided by the type, which is the whole
 *  premise of the configuration model. */
function Detail({ item, byKey }: { item: LineItemDef; byKey: Map<string, LineItemDef> }) {
  const lbl = { fontSize: 11, fontWeight: 600, color: color.sec, letterSpacing: 0.2 } as const;
  const box = { border: `1px solid ${color.cardBorder}`, borderRadius: 9, padding: 13,
                marginBottom: 12 } as const;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>{item.label || item.key}</h2>
        <Tag type={item.type} />
        {item.in_output
          ? <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                            letterSpacing: 0.3, padding: "2px 6px", borderRadius: 4,
                            background: color.indigo, color: "#fff" }}>In output</span>
          : <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                            letterSpacing: 0.3, padding: "2px 6px", borderRadius: 4,
                            background: color.segBg, color: color.muted }}>Internal</span>}
      </div>
      <div style={{ fontFamily: font.mono, fontSize: 11, color: color.muted, margin: "3px 0 10px" }}>
        {item.key}
      </div>
      {item.description && (
        <p style={{ fontSize: 12.5, color: color.sec, lineHeight: 1.6, maxWidth: "64ch",
                     margin: "0 0 14px" }}>{item.description}</p>
      )}

      {/* THE GATE — shown for every type, and first, because it decides whether a figure lands
          on this line at all. Several hundred of the configuration's normalised captions are
          claimed by more than one line item, and some of those claims span different statements,
          so the gate is what settles which line a caption reaches. Without this panel the most
          consequential declaration on the screen was the one a reader could not see.
          (The counts this used to quote were measured against the retired second engine's
          rulebook file, which no longer exists as a stored, selectable thing — the line-item set
          is the single configuration, and no fixed count of it belongs in markup.) */}
      <div style={box}>
        <div style={lbl}>Where it may be claimed from</div>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 12px",
                       marginTop: 7, fontSize: 12.5, alignItems: "baseline" }}>
          <span style={{ color: color.muted }}>Statement</span>
          <span>
            {item.statement
              ? (STATEMENT_LABEL[item.statement] ?? item.statement)
              : <span style={{ color: color.muted }}>any — nothing was said</span>}
          </span>
          <span style={{ color: color.muted }}>Section</span>
          <span>
            {item.section_scope.length
              ? item.section_scope.map(s => (
                  <span key={s} style={{ fontFamily: font.mono, fontSize: 11,
                                          background: color.segBg, borderRadius: 3,
                                          padding: "1px 5px", marginRight: 4 }}>{s}</span>
                ))
              : <span style={{ color: color.muted }}>unconstrained</span>}
          </span>
          {item.match_priority !== null && (
            <>
              <span style={{ color: color.muted }}>Priority</span>
              <span style={{ fontFamily: font.mono }} title="higher wins a tie the gate leaves">
                {item.match_priority}
              </span>
            </>
          )}
          {item.inherits && (
            <>
              <span style={{ color: color.muted }}>Inherited from</span>
              <span style={{ fontFamily: font.mono, fontSize: 11 }}
                    title="the gate is authored once per section, not per line item">
                {item.inherits}
              </span>
            </>
          )}
          {item.alias_matching === "disabled" && (
            <>
              <span style={{ color: color.muted }}>Alias matching</span>
              <span style={{ color: color.amberFg }}
                    title="unreachable by every matching tier; filled only by the residual sweep">
                locked
              </span>
            </>
          )}
          {item.is_gross_parent && (
            <>
              <span style={{ color: color.muted }}>Contains</span>
              <span title="a gross parent is never loaded additively with its children">
                {item.children_if_decomposed.join(", ") || "children, when decomposed"}
              </span>
            </>
          )}
        </div>
        {item.section_disambiguation && (
          <p style={{ fontSize: 11.5, color: color.sec, margin: "9px 0 0", lineHeight: 1.55,
                       borderLeft: `2px solid ${color.hairline2}`, paddingLeft: 8 }}>
            {item.section_disambiguation}
          </p>
        )}
      </div>

      {item.type === "extracted" && (
        <>
          <div style={box}>
            <div style={lbl}>Where to look</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 7 }}>
              {item.scopes.map((s, i) => (
                <div key={s} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <span style={{ fontFamily: font.mono, fontSize: 11, fontWeight: 600,
                                  color: color.indigo, width: 16 }}>{i + 1}</span>
                  <span style={{ fontSize: 12.5 }}>{SCOPE_LABEL[s]}</span>
                  {i === 0 && item.scopes.length > 1 && (
                    <span style={{ fontSize: 9.5, fontWeight: 700, textTransform: "uppercase",
                                    letterSpacing: 0.3, color: color.greenFg,
                                    background: color.greenBg, padding: "1px 5px",
                                    borderRadius: 3 }}>preferred</span>
                  )}
                </div>
              ))}
            </div>
            <p style={{ fontSize: 11, color: color.muted, margin: "8px 0 0" }}>
              Searched in this order; the first that yields a figure is published.
            </p>
          </div>

          <div style={box}>
            <div style={lbl}>Asset or liability</div>
            <div style={{ fontSize: 12.5, marginTop: 5 }}>
              {SIDE_LABEL[item.side]}
              {item.side === "from_section" && (
                <span style={{ color: color.muted }}> — read off the section banner</span>
              )}
            </div>
            <p style={{ fontSize: 11, color: color.muted, margin: "6px 0 0" }}>
              {item.allow_contra
                ? "The opposite side MAY fill this line — for an instrument that appears on both."
                : "A caption printed on the opposite side can never fill this line."}
            </p>
          </div>

          <div style={box}>
            <div style={lbl}>How to recognise it</div>
            <Patterns label="Aliases" values={item.aliases} />
            {Object.entries(item.aliases_i18n ?? {}).map(([loc, vals]) => (
              <Patterns key={loc} label={`Aliases (${loc})`} values={vals} />
            ))}
            {item.pattern && <Patterns label="Pattern" values={[item.pattern]} />}
            <Patterns label="Regex hints" values={item.regex_hints} />
            <Patterns label="Keyword hints" values={item.keyword_hints} />
            {/* Regex vetoes and prose criteria are separate fields now: folding the prose into a
                regex-validated list either failed at the door or compiled as an accidental veto. */}
            <Patterns label="Never match" values={item.exclude_hints} tone={color.redFg} />
            <Patterns label="Counts as this" values={item.include_criteria} />
            <Patterns label="Does not count" values={item.exclude_criteria} tone={color.redFg} />
            {item.note_source && (
              <>
                <Patterns label="Note title" values={item.note_source.note_title_any} />
                <Patterns label="Rows that count" values={item.note_source.row_caption_any} />
                <Patterns label="Rows that do not"
                          values={item.note_source.row_caption_none} tone={color.redFg} />
              </>
            )}
          </div>
        </>
      )}

      {(item.type === "calculated" || item.type === "intermediate") && (
        <div style={box}>
          <div style={lbl}>Formula</div>
          <div style={{ marginTop: 7 }}><Terms terms={item.terms} byKey={byKey} /></div>
          <p style={{ fontSize: 11, color: color.muted, margin: "8px 0 0" }}>
            A required term with no figure makes the result blank, never zero.
          </p>
        </div>
      )}

      {item.type === "derived" && (
        <div style={box}>
          <div style={lbl}>Priority cascade</div>
          {item.cascade.length === 0 ? (
            <p style={{ fontSize: 12, color: color.muted, margin: "7px 0 0" }}>
              No rungs configured yet — computed by{" "}
              <span style={{ fontFamily: font.mono }}>{item.implemented_by || "?"}</span>.
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
              {item.cascade.map((r) => (
                <div key={r.id}>
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                    <span style={{ fontFamily: font.mono, fontSize: 11.5, fontWeight: 600,
                                    color: color.indigo }}>{r.id}</span>
                    {r.note && (
                      <span style={{ fontSize: 11, color: color.muted, lineHeight: 1.5 }}>
                        {r.note}
                      </span>
                    )}
                  </div>
                  <div style={{ marginTop: 5, paddingLeft: 10,
                                 borderLeft: `2px solid ${color.hairline2}` }}>
                    <Terms terms={r.terms} byKey={byKey} />
                  </div>
                </div>
              ))}
            </div>
          )}
          {item.implemented_by && item.cascade.length > 0 && (
            <p style={{ fontSize: 11, color: color.muted, margin: "10px 0 0" }}>
              Computed today by{" "}
              <span style={{ fontFamily: font.mono }}>{item.implemented_by}</span>. This
              configuration describes it; nothing reads it yet.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function LineItemsScreen() {
  const t = useT();
  const q = useLineItems();
  const [sel, setSel] = useState<string | null>(null);

  if (q.isError) {
    return (
      <div style={{ padding: "28px 32px", maxWidth: 820 }}>
        <Card>
          <div data-testid="line-items-error" style={{ fontSize: 12.5 }}>
            The line-item configuration could not be loaded.{" "}
            <span style={{ fontFamily: font.mono, fontSize: 11, color: color.sec2 }}>
              {(q.error as Error)?.message}
            </span>
          </div>
        </Card>
      </div>
    );
  }
  if (!q.data) {
    return (
      <div style={{ padding: "28px 32px" }}>
        <Card><div style={{ fontSize: 12.5, color: color.muted }}>{t("common.loading")}</div></Card>
      </div>
    );
  }

  const { items, counts, problems, valid, set } = q.data;
  // WHICH STORED VERSION THIS IS. `GET /line-items` reads a `line_item_versions` row and names it
  // (`routes/line_items.py::_version_identity`), so the screen can caption the definitions with the
  // version a run would pin — instead of leaving a reader to assume the screen and the run agree.
  // Read defensively: a server that does not state it leaves the caption off rather than inventing
  // one, because a configured-empty identity means nothing, never "assume the latest".
  const inForce = (q.data as { version?: LineItemVersionRef }).version;
  const flat: LineItemDef[] = [];
  const walk = (xs: LineItemDef[]) => xs.forEach((x) => { flat.push(x); walk(x.children); });
  walk(items);
  const byKey = new Map(flat.map((d) => [d.key, d]));
  const selected = (sel && byKey.get(sel)) || items[0];

  const row = (d: LineItemDef, depth: number) => (
    <div key={d.key}>
      <div role="button" tabIndex={0} data-testid={`li-row-${d.key}`}
           aria-current={d.key === selected?.key}
           onClick={() => setSel(d.key)}
           onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSel(d.key); } }}
           style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10,
                     alignItems: "center", padding: "8px 10px", cursor: "pointer",
                     borderRadius: 7, marginLeft: depth * 16,
                     background: d.key === selected?.key ? color.indigoTint2 : "transparent",
                     border: `1px solid ${d.key === selected?.key ? color.indigoBorder
                                                                  : "transparent"}` }}>
        <span style={{ minWidth: 0 }}>
          <span style={{ fontSize: depth ? 12 : 12.5, color: depth ? color.sec : color.ink,
                          display: "block", overflow: "hidden", textOverflow: "ellipsis",
                          whiteSpace: "nowrap" }}>
            {d.label || d.key}
          </span>
          <span style={{ fontFamily: font.mono, fontSize: 9.5, color: color.faint,
                          display: "block", overflow: "hidden", textOverflow: "ellipsis",
                          whiteSpace: "nowrap" }}>{d.key}</span>
        </span>
        <Tag type={d.type} />
        <span style={{ width: 16, textAlign: "center", fontSize: 11, fontWeight: 700,
                        color: d.in_output ? color.indigo : color.faint }}>
          {d.in_output ? "✓" : "–"}
        </span>
      </div>
      {d.children.map((k) => row(k, depth + 1))}
    </div>
  );

  return (
    <div style={{ padding: "28px 32px", maxWidth: 1320 }}>
      <div style={{ marginBottom: 6 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: "0 0 4px" }}>Line items</h1>
        <p style={{ margin: 0, color: color.sec2, fontSize: 12.5 }}>
          The lines that reach the output template, and the parts each is assembled from. Each
          line's <b>type</b> decides what it needs. This is the single configuration the pipeline
          reads.
        </p>
        {inForce && (
          <p data-testid="li-in-force" style={{ margin: "5px 0 0", fontSize: 11.5,
                                                 color: color.muted }}>
            Showing{" "}
            <b style={{ fontFamily: font.mono, color: color.sec2 }}>
              {inForce.line_items_key} v{inForce.version}
            </b>
            {" — the stored version in force, the one the next run pins."}
          </p>
        )}
      </div>

      {/* A configuration that does not load is the one thing this screen must not hide. */}
      {!valid && (
        <div style={{ background: color.redBg, color: color.redFg, borderRadius: 8,
                       padding: "9px 12px", fontSize: 12, margin: "10px 0" }}>
          <b>This configuration will not load.</b>{" "}
          {problems.filter((p) => p.severity === "error").length} error(s) —{" "}
          {problems.filter((p) => p.severity === "error")[0]?.message}
        </div>
      )}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", margin: "12px 0 16px",
                     fontSize: 11.5, color: color.sec2 }}>
        <span><b style={{ fontFamily: font.mono }}>{counts.output}</b> output lines</span>
        <span><b style={{ fontFamily: font.mono }}>{counts.sub_line_items}</b> sub-line items</span>
        {(Object.keys(TYPE_TONE) as LineItemType[]).filter((k) => counts.by_type[k]).map((k) => (
          <span key={k}>
            <b style={{ fontFamily: font.mono }}>{counts.by_type[k]}</b> {TYPE_TONE[k].label.toLowerCase()}
          </span>
        ))}
        <span title="how many resolved a statement gate, and how many got it from a section
rather than declaring it themselves">
          <b style={{ fontFamily: font.mono }}>{counts.gated}</b> gated
          {counts.inherited ? <> · <b style={{ fontFamily: font.mono }}>{counts.inherited}</b> inherited</> : null}
        </span>
        <span style={{ color: color.amberFg }}>
          Read-only — these describe what the pipeline computes today
        </span>
      </div>

      {/* THE SET-LEVEL FACTS. `target_template_key` is what binds this configuration to ONE
          template — the key-gate the retired second engine kept in its own file and a bare JSON
          array had nowhere to put, which is why it had to be declared on the set itself when the
          two models were merged into the single configuration engine. */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", margin: "0 0 16px",
                     fontSize: 11, color: color.muted }}>
        <span>binds to <b style={{ fontFamily: font.mono, color: color.sec2 }}>
          {set.target_template_key || "—"}</b></span>
        <span><b style={{ fontFamily: font.mono, color: color.sec2 }}>
          {Object.keys(set.section_defaults).length}</b> section defaults</span>
        <span>locales <b style={{ fontFamily: font.mono, color: color.sec2 }}>
          {set.supported_locales.join(", ")}</b></span>
        {/* The AUTHORED version string inside the definition — not the stored row's version, which
            the header states. Labelled since both are on screen now and they need not agree. */}
        {set.metadata.version && <span>authored v{set.metadata.version}</span>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(380px, 1fr)",
                     gap: 16, alignItems: "start" }}>
        <Card pad={10}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10,
                         padding: "0 10px 8px", borderBottom: `1px solid ${color.hairline2}`,
                         fontSize: 10.5, fontWeight: 700, letterSpacing: 0.3, color: color.muted,
                         textTransform: "uppercase" }}>
            <span>Line item</span><span>Type</span><span>Out</span>
          </div>
          <div style={{ marginTop: 4 }}>{items.map((d) => row(d, 0))}</div>
        </Card>
        <Card>
          {selected
            ? <Detail item={selected} byKey={byKey} />
            : <div style={{ fontSize: 12.5, color: color.muted }}>Select a line item.</div>}
        </Card>
      </div>
    </div>
  );
}
