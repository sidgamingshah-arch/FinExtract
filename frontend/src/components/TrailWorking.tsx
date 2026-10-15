/** "HOW IT ADDS UP" — the working of a derived or model-assembled figure, for the period on show.
 *
 * The server sends the arithmetic (`StatementRow.working1/working2`, `services.derivation.working`):
 * what each input added, the figure the line shows, and the verdict. The SENTENCE is built here,
 * because it has to be in the reader's units — `present` applies the units selector and any
 * currency conversion, and a sentence built server-side would read in the filing's units beside a
 * grid that is not.
 *
 * Each input's own row is rendered by `TraceRows` in screens/Workspace.tsx; the helpers below tell
 * it what an input did ("same row, added once", "not used") and why, so the column and the verdict
 * read the same arithmetic. A trail stored before inputs recorded what they added carries no
 * status, and those rows keep the old reading of `counted`. */
import { color, font, fmtIN } from "../theme";
import { useT } from "../i18n";
import type { RowContribution, TrailStatus, TrailTerm, TrailWhy, TrailWorking } from "../types";

type Translate = (key: string) => string;
type Present = (raw: number | null) => string;
type Period = "current" | "prior";

const ENTERED: (TrailStatus | null)[] = ["used", "deducted"];

/** A left-to-right isolate: an Arabic sentence would otherwise reorder "a + b − c = d". */
const ltr = (s: string) => "⁦" + s + "⁩";

export function statusOf(c: RowContribution, period: Period): TrailStatus | null {
  return (period === "current" ? c.status : c.status2) ?? null;
}

export function whyOf(c: RowContribution, period: Period): TrailWhy | null {
  return (period === "current" ? c.why1 : c.why2) ?? null;
}

/** Whether the input put something into this period's figure. A legacy trail says only whether it
 *  was counted. */
export function isEntered(c: RowContribution, period: Period): boolean {
  const st = statusOf(c, period);
  if (st) return ENTERED.includes(st);
  return (period === "current" ? c.counted : c.counted2) !== false;
}

/** Why an input reads the way it does, in words — or "" when its status already says it. */
export function whyText(why: TrailWhy | null | undefined, present: Present, t: Translate): string {
  if (!why) return "";
  const amount = why.amount != null && why.amount !== "" ? present(Number(why.amount)) : "";
  switch (why.code) {
    case "same_row":
      return why.line ? t("trail.reason.via").replace("{line}", why.line) : "";
    case "counted_on_face":
      if (why.whole) return t("trail.reason.wholeLine").replace("{amount}", amount);
      if (why.partial) return t("trail.reason.partial").replace("{amount}", amount);
      return "";
    case "reduced":
      return t("trail.reason.reduced").replace("{amount}", amount);
    case "not_selected":
    case "alternative":
    case "corroborates":
    case "disagrees":
    case "stated_not_located":
    case "no_figure":
    case "not_needed":
    case "not_located":
    case "refused":
      return t("trail.reason." + why.code);
    default:
      return "";
  }
}

/** The pill beside an input that did not simply add or deduct its figure, or null. */
export function statusPill(c: RowContribution, period: Period, t: Translate): string | null {
  const st = statusOf(c, period);
  if (st === "same_row" || st === "not_used" || st === "missing") return t("trail.status." + st);
  if (c.located === false && (st === "used" || st === "deducted")) return t("trail.status.unlocated");
  const v = period === "current" ? c.v1 : c.v2;
  if (!st && (period === "current" ? c.counted : c.counted2) === false && v != null) {
    return t("trail.status.not_added");
  }
  return null;
}

/** A term a `max`/`min`/`first` chose between. Not one that printed no figure this year, nor an
 *  amount the model stated that no note contains — the server's `derivation._candidate`. */
function isCandidate(x: TrailTerm): boolean {
  return x.role !== "adjustment" && x.status !== "same_row" && x.status !== "missing"
    && x.why?.code !== "no_figure" && x.why?.code !== "stated_not_located";
}

/** The figure a `max`/`min`/`first` compared a candidate it did not take at: its own, less what
 *  the face settlement took out of it — the server's `derivation._compared`. */
function compared(x: TrailTerm): number {
  const cut = x.why?.code === "reduced" ? Number(x.why.amount ?? 0) || 0 : 0;
  return ((x.value ?? 0) - cut) * (x.sign ?? 1);
}

/** The working as one line in the reader's units: "788,507 + 128,412 − 549,975 = 366,944 — adds
 *  up.", "the larger of 1,650, 1,200, 900 = 1,650 — adds up.", "529,841,000 ÷ 1,000 = 529,841 —
 *  adds up." A prose figure's inputs are stated in full, so they are printed as stated (`plain`)
 *  and the division is shown. */
export function trailSentence(w: TrailWorking, present: Present, plain: Present,
                              t: Translate): string {
  if (w.how === "not_recorded") return t("trail.verdict.notRecorded");
  const entered = w.terms.filter((x) => ENTERED.includes(x.status));
  const figure = (n: number | null) => (w.scale ? plain(n) : present(n));
  const signed = (x: TrailTerm, first: boolean) => {
    const minus = x.status === "deducted" || (x.used ?? 0) < 0;
    const magnitude = figure(Math.abs(x.used ?? 0));
    return first ? (minus ? "−" : "") + magnitude : (minus ? " − " : " + ") + magnitude;
  };
  let arith: string;
  if (w.op === "sum") {
    arith = entered.map((x, i) => signed(x, i === 0)).join("");
  } else {
    const list = w.terms.filter(isCandidate).map((x) =>
      figure(ENTERED.includes(x.status) ? (x.used ?? 0) : compared(x)));
    arith = t("trail.op." + w.op).replace("{list}", list.join(", "))
      + entered.filter((x) => x.role === "adjustment").map((x) => signed(x, false)).join("");
  }
  if (w.scale) {
    arith = (w.op === "sum" && entered.length > 1 ? "(" + arith + ")" : arith)
      + " ÷ " + plain(w.scale);
  }
  const figured = w.terms.filter((x) => x.value != null || (x.used ?? 0) !== 0);
  if (w.how === "adds_up" && !w.scale && (w.op === "sum" || w.op === "first")
      && figured.length === 1 && figured[0].status === "used") {
    return t("trail.verdict.single").replace("{result}", ltr(present(w.result)));
  }
  const reached = w.sum == null ? null : w.scale ? w.sum / w.scale : w.sum;
  const keys: Record<TrailWorking["how"], string> = {
    adds_up: "trail.verdict.addsUp", differs: "trail.verdict.differs",
    changed: "trail.verdict.changed", edited: "trail.verdict.edited",
    withheld: "trail.verdict.withheld", not_recorded: "trail.verdict.notRecorded",
  };
  return t(keys[w.how])
    .replace("{arith}", ltr(arith || "0"))
    .replace("{result}", ltr(present(w.result)))
    .replace("{sum}", ltr(present(reached)))
    .replace("{diff}", ltr(present(w.difference)))
    .replace("{shown}", ltr(w.shown == null ? "—" : present(w.shown)));
}

/** The box's header. */
export function TrailTitle() {
  const t = useT();
  return (
    <div
      data-testid="trail-title"
      style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.4,
               color: color.sec2, background: color.rowAltBg, padding: "6px 10px",
               borderBottom: `1px solid ${color.cardBorder}` }}
    >
      {t("trail.title")}
    </div>
  );
}

/** The sum, the figure shown, the verdict, and what the working could not include. */
export function TrailFooter({ working: w, present }: { working: TrailWorking; present: Present }) {
  const t = useT();
  const plain: Present = (n) => (n == null ? "—" : fmtIN(n));
  const tone = w.how === "adds_up" ? color.greenFg
    : w.how === "differs" ? color.redFg
      : w.how === "not_recorded" ? color.sec2 : color.amberFg;
  const named = w.missing.filter((m) => m.role !== "any_of");
  const optional = w.missing.filter((m) => m.role === "any_of");
  const line = (label: string, value: string, red = false) => (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 11,
                  color: color.sec2, lineHeight: 1.7 }}>
      <span>{label}</span>
      <span style={{ fontFamily: font.mono, fontWeight: 600, color: red ? color.redFg : color.ink }}>
        {value}
      </span>
    </div>
  );
  const note = { fontSize: 10.5, color: color.sec2, marginTop: 5, lineHeight: 1.5 };
  return (
    <div data-testid="trail-working"
         style={{ borderTop: `1px solid ${color.cardBorder}`, background: color.rowAltBg,
                  padding: "8px 10px" }}>
      {w.how !== "not_recorded" && w.sum != null
        ? line(w.scale ? t("trail.sumStated") : t("trail.sum"), w.scale ? plain(w.sum) : present(w.sum))
        : null}
      {w.scale ? line(t("trail.scaled").replace("{scale}", plain(w.scale)), present(w.result)) : null}
      {w.how !== "not_recorded"
        ? line(t("trail.shown"), w.shown == null ? "—" : present(w.shown))
        : null}
      {(w.how === "differs" || w.how === "changed") && w.difference != null
        ? line(t("trail.difference"), present(w.difference), true)
        : null}
      <div data-testid="trail-verdict"
           style={{ fontSize: 11.5, fontWeight: 600, color: tone, marginTop: 6, lineHeight: 1.5 }}>
        {trailSentence(w, present, plain, t)}
      </div>
      {named.length ? (
        <div data-testid="trail-missing" style={note}>
          {t("trail.missing.named").replace("{lines}", named.map((m) => m.label || m.key).join("; "))}
        </div>
      ) : null}
      {optional.length ? (
        <details data-testid="trail-optional" style={note}>
          <summary style={{ cursor: "pointer" }}>
            {t("trail.missing.optional").replace("{n}", String(optional.length))}
          </summary>
          <ul style={{ margin: "4px 0 0", paddingInlineStart: 18 }}>
            {optional.map((m) => <li key={m.key}>{m.label || m.key}</li>)}
          </ul>
        </details>
      ) : null}
      {w.refused.length ? (
        <div data-testid="trail-refused" style={note}>
          {w.refused.map((r, i) => (
            <div key={r.rung + "#" + i}>
              {r.why === "negative"
                ? t("trail.refused.negative").replace("{rung}", r.rung)
                    .replace("{value}", ltr(present(Number(r.value))))
                : t("trail.refused.contradiction").replace("{rung}", r.rung)}
            </div>
          ))}
        </div>
      ) : null}
      {w.displaced?.length ? (
        <div data-testid="trail-displaced" style={note}>
          {t("trail.displaced").replace("{figures}",
                                         ltr(w.displaced.map((v) => present(v)).join(", ")))}
        </div>
      ) : null}
    </div>
  );
}
