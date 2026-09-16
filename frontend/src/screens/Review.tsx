/** Screen 5 — Review queue. Automated-check failures grouped by type; each expands into a
 * reconciliation breakdown, the suggested fix, and the judgement path.
 *
 * A finding could be corrected but never ACCEPTED: "Apply fix" and "Accept as is" had no onClick,
 * so a finding a reviewer had examined and deemed acceptable stayed red forever and nothing
 * separated "not looked at" from "reviewed and accepted". Both halves of that are now real, and
 * they are separate controls with separate gates:
 *
 *  - accepting/withdrawing records a JUDGEMENT (review:resolve) — a named person, a timestamp and
 *    a required reason, pinned to the FIGURES that were judged, so a re-run that moves them
 *    reopens the finding as `stale` rather than leaving a stale acceptance standing;
 *  - the flip-sign fix is an ORDINARY edit (extraction:edit), because it lands on the same PATCH
 *    the Workspace uses. Gating it on review:resolve — as it was — denied an analyst the one
 *    mechanical correction their role is entitled to.
 *
 * Only a mis-signed figure has a mechanical fix, and only when the server could resolve exactly
 * one row to flip. Every other card gets a sentence saying the fix is manual instead of a button
 * that would either do nothing or invent a mapping.
 *
 * THE ROW-SHAPED FINDINGS HAVE A THIRD CONTROL, and it is the one that RESOLVES them: re-map the
 * printed row onto a different template line (`RemapPanel`, extraction:edit — the same gate as the
 * flip, because moving a row is an extraction edit). Both cards' own prose had always instructed
 * the analyst to do exactly this — "Pick the correct template line item", "Confirm the concept is
 * correct or reassign it" — and there was nothing on the screen that could, which is a card telling
 * the reader to do something the product cannot do. The offer is per card (`check.remap`) while the
 * candidate list is served once per payload (`remap_targets`), and the accounting findings carry no
 * offer at all: a relation between several concepts gives no answer to WHICH one to re-map.
 *
 * A finding the server serves as `conflict` — its identity is shared with another finding that
 * printed DIFFERENT figures — gets no ACCEPT path at all: no Accept, no reason box, no stored
 * verdict shown, and a sentence saying the queue cannot tell the two apart. Attributing the one
 * stored judgement to whichever card the reader happened to open is how a reviewer's name ended up
 * against figures they never saw, so the refusal is stated rather than papered over.
 *
 * WITHDRAWAL IS NOT ACCEPTANCE, AND IS NOT GATED LIKE IT. Withdraw was gated on
 * `judgeable && canResolve && accepted`, using `accepted` as a proxy for "there is a stored
 * acceptance to take back". On a conflict card both `judgeable` and `accepted` are false, so a
 * WITHHELD judgement — one the server holds but will not attribute to any card — could not be
 * removed by anyone, while `withdraw_review_judgement` (documents.py) deliberately PERMITS
 * withdrawal there and its docstring calls it precisely the case a reviewer needs. The observed
 * consequence: accept a lone finding, let a second finding collide with its subject, and the
 * acceptance is frozen in place — one run later the surviving card is served `stale` carrying the
 * original actor, reason and figures, at rank 0, for a finding nobody examined. The same proxy hid
 * the control on a `stale` card too, where the row is equally withdrawable and re-accepting was
 * the only way out.
 *
 * So each control is gated on WHAT THE SERVER PERMITS, spelled from the payload:
 *  - Accept needs a subject AND a digest AND a non-conflicted, recognised state (the POST refuses
 *    all three otherwise) — plus a typed reason, because the POST refuses an empty one;
 *  - Withdraw needs only a stored in-force acceptance on this subject, which the payload states
 *    either as `judgement` (this card's own, accepted or stale) or as `judgement_withheld` (the
 *    conflict group's, unattributable). The DELETE takes the subject key alone, so nothing else
 *    may gate it.
 *
 * AND WITHDRAWAL IS REACHABLE WHEREVER AN IN-FORCE ROW IS SHOWN — not only on a card. The same
 * proxy that hid the control on conflict and stale cards hid it one row further along: an ORPHANED
 * judgement is an in-force acceptance (judgement.py::apply_judgements builds that list from the
 * verdict='accepted' rows, and DELETE /review/judgements/{subject_key} answers
 * 200 {'ok':true,'withdrawn':true} for it), it is rendered at the foot of this screen — and the
 * only withdraw control lived INSIDE a check card, so a finding with no card had a named,
 * standing acceptance nothing on the product could remove. Not cosmetic: the row keeps
 * verdict='accepted', so raising the same finding again with the same figures serves it
 * status='accepted' under a verdict nobody re-made. The orphan list now carries the control per
 * ROW, gated on the row existing (a subject key, and a document to address the DELETE to) and on
 * review:resolve — the endpoint's own two conditions — and says out loud that these acceptances
 * are still in force, because the caption "corrected, or no longer raised" reads as finished
 * with. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "../components/ui";
import { CoverageBand } from "../components/CoverageBand";
import {
  useAcceptFinding, useDocumentReview, useEditDocumentLineItem, useRemapReviewRow, useReview,
  useProjectLoaded, useWithdrawAcceptance,
} from "../lib/queries";
import { ApiError } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { SCREENS } from "./config";
import { useAppLocale, useUI } from "../store";
import { useT } from "../i18n";
import { useCan } from "../lib/rbac";
import { color, font } from "../theme";
import type { RemapTarget, ReviewCheck } from "../types";

/** The judgement states this build knows how to render, and the subset a control may be offered
 *  on. A WHITELIST, deliberately: the server grew `conflict` (two findings sharing one identity
 *  while printing different figures) after these controls shipped, and a state this build has
 *  never heard of must render as non-judgeable rather than fall through to "acceptable". An
 *  Accept button whose meaning the screen is guessing at is the defect this file exists to close.
 *
 *  WITHDRAWAL IS OUTSIDE THIS WHITELIST, on purpose. What "accept" means depends on the state, so
 *  it must be a state this build understands; what "withdraw" means does not — it removes the
 *  stored acceptance the payload says exists, whatever the server is calling the finding's state.
 *  Withholding that control on an unrecognised state would strand a real acceptance under a name
 *  this build simply has not learned yet, which is the shape of the defect it just fixed. */
const KNOWN_STATUS = new Set(["open", "accepted", "stale"]);
const ACCEPTABLE_STATUS = new Set(["open", "stale"]);

/** tone → { accent, iconBg }. `tone` is a SEVERITY, not a colour name — see `ReviewCheck.tone`.
 *
 *  RED IS FOR A FAILED CHECK. This read `low → red, med → amber, else indigo`, and the server sends
 *  `high` for every failing kind (balance, structural, calculated_mismatch, containment_gap) and
 *  `low` for the row-shaped one — so the failures all fell through to indigo and the mildest card on
 *  the queue was the only red thing on the screen. The mapping is now the severity it is given, and
 *  the fall-through is the quiet end rather than the loud one: an unrecognised tone under-paints a
 *  card instead of crying wolf on it, and the type makes an unrecognised tone a build error anyway. */
function toneColors(tone: ReviewCheck["tone"]): { ac: string; ib: string } {
  if (tone === "high") return { ac: color.redFg, ib: color.redBg };
  if (tone === "med") return { ac: color.amberFg, ib: color.amberBg };
  return { ac: color.indigo, ib: color.indigoTint2 };
}

/** A refused judgement, in words the reviewer can act on. The refusals that matter are
 *  MEANINGFUL, not generic failures: 422 says no reason was stated, and THREE distinct things
 *  arrive as 409 — the figures moved while the card was open, the subject is a conflict no verdict
 *  may be attached to, or the write lost a race and stored nothing. They are told apart by the
 *  server's own `detail.error` code, because explaining a subject conflict as "the figures changed"
 *  is a made-up cause, and the status alone cannot distinguish them. An uncoded 409 keeps the
 *  original reading, which is the only 409 the endpoint sent before the other two existed.
 *
 *  `no_judgement` is the WITHDRAWAL refusal, and it became reachable the moment withdrawal stopped
 *  being gated on `accepted`: two cards in a conflict group share one subject and therefore one
 *  stored row, so withdrawing on the second after the first already removed it is a 404 the
 *  reviewer will actually hit. Its detail is a code, not a sentence, so without this branch it
 *  surfaced as the raw `404 Not Found — {"detail":{"error":"no_judgement"}}`. */
function judgementErrorText(err: unknown, t: (k: string) => string): string {
  if (err instanceof ApiError) {
    if (err.code === "subject_conflict") return t("r.conflictRefused");
    if (err.code === "judgement_write_conflict") return t("r.writeConflict");
    if (err.code === "no_judgement") return t("r.noJudgement");
    if (err.status === 409) return t("r.evidenceChanged");
    if (err.status === 422) return t("r.reasonRequired");
    if (err.detail) return err.detail;
    return `${err.status} ${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

/** The two-column figure list. ONE renderer for the check's own reconciliation and for the
 *  evidence a judgement was recorded against — both arrive from the server already localized and
 *  already formatted, so the browser formats no figure and the two cannot disagree. */
function FigureRows({ rows, accent }: { rows: [string, string, boolean?][]; accent: string }) {
  return (
    <>
      {rows.map(([label, value, hl], idx) => (
        <div
          key={idx}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "5px 0",
            borderBottom: `1px dashed ${color.hairline2}`,
          }}
        >
          <span style={{ fontSize: 12, color: hl ? accent : color.sec, fontWeight: hl ? 600 : 400 }}>
            {label}
          </span>
          <span
            style={{
              fontFamily: font.mono, fontSize: 12,
              color: hl ? accent : color.sec, fontWeight: hl ? 600 : 400,
            }}
          >
            {value}
          </span>
        </div>
      ))}
    </>
  );
}

/** The re-map control on a row-shaped finding: pick a template line, say why, apply.
 *
 *  WHY IT IS A SELECT AND NOT A FREE-TEXT KEY. The list is the server's `remap_targets` — the lines
 *  THIS RUN'S template defines, with calculated subtotals and headers already excluded — so a
 *  concept the run cannot hold is not offerable. The endpoint checks the same thing again; this is
 *  what stops the analyst discovering it through a 422.
 *
 *  Grouped by statement and section with `<optgroup>`: 180-odd flat options is a list nobody can
 *  find a line in, and the section is how an analyst reads a statement.
 *
 *  The empty option is a REAL choice, not a placeholder — "" un-maps the row, which is the only
 *  route back from a re-map that started from unmapped. So the placeholder is a separate disabled
 *  option and the button is gated on a pick having been MADE, not on the pick being non-empty.
 */
function RemapPanel({
  offer, targets, picked, onPick, reason, onReason, onApply, busy, canEdit, t,
}: {
  offer: NonNullable<ReviewCheck["remap"]>;
  targets: RemapTarget[];
  picked: string | undefined;
  onPick: (key: string) => void;
  reason: string;
  onReason: (text: string) => void;
  onApply: () => void;
  busy: boolean;
  canEdit: boolean;
  t: (k: string) => string;
}) {
  const groups: { label: string; items: RemapTarget[] }[] = [];
  for (const tgt of targets) {
    const label = `${tgt.statement} · ${tgt.section}`;
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(tgt);
    else groups.push({ label, items: [tgt] });
  }
  const head = { fontSize: 10.5, fontWeight: 600, letterSpacing: 0.4, color: color.muted } as const;
  return (
    <div data-testid="rv-remap" style={{ marginBottom: 13 }}>
      <div style={{ ...head, marginBottom: 8 }}>{t("r.remapHead")}</div>
      {/* Where the row sits now, so the pick is made against a known starting point rather than
          against a caption alone. */}
      <div style={{ fontSize: 11.5, color: color.sec2, marginBottom: 8, fontFamily: font.mono }}>
        {t("r.remapCurrent")}: {offer.current_key || t("r.remapNowUnmapped")}
      </div>
      {offer.remapped_note && (
        <div
          data-testid="rv-remapped-note"
          style={{ fontSize: 11, color: color.greenFg, marginBottom: 8, lineHeight: 1.5 }}
        >
          {offer.remapped_note}
        </div>
      )}
      {targets.length === 0 ? (
        <div style={{ fontSize: 11, color: color.muted, lineHeight: 1.5 }}>
          {t("r.remapNoTargets")}
        </div>
      ) : (
        <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
          <select
            data-testid="rv-remap-select"
            value={picked ?? "__none__"}
            disabled={!canEdit || busy}
            onChange={(e) => onPick(e.target.value)}
            style={{
              fontSize: 11.5, fontFamily: font.sans, color: color.ink, padding: "7px 9px",
              border: `1px solid ${color.controlBorder}`, borderRadius: 8, background: "#fff",
              maxWidth: 360,
            }}
          >
            <option value="__none__" disabled>{t("r.remapPick")}</option>
            <option value="">{t("r.remapUnmap")}</option>
            {groups.map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.items.map((tgt) => (
                  <option key={tgt.canonical_key} value={tgt.canonical_key}>{tgt.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <Button
            variant="primary"
            testid="rv-remap-apply"
            disabled={!canEdit || busy || picked === undefined || picked === offer.current_key}
            onClick={onApply}
            style={{ fontSize: 12, padding: "8px 15px", borderRadius: 8 }}
          >
            {t("r.remapApply")}
          </Button>
        </div>
      )}
      {targets.length > 0 && canEdit && (
        <textarea
          data-testid="rv-remap-reason"
          value={reason}
          maxLength={2000}
          rows={2}
          placeholder={t("r.remapReasonPlaceholder")}
          onChange={(e) => onReason(e.target.value)}
          style={{
            width: "100%", boxSizing: "border-box", marginTop: 8, fontSize: 11.5,
            fontFamily: font.sans, color: color.ink, resize: "vertical",
            border: `1px solid ${color.controlBorder}`, borderRadius: 8, padding: "7px 9px",
            outline: "none", lineHeight: 1.5,
          }}
        />
      )}
    </div>
  );
}

export default function ReviewScreen() {
  const t = useT();
  const locale = useAppLocale();
  // Two capabilities, two gates. A control that would 403 is the same defect in a new place.
  const canResolve = useCan("review:resolve");
  const canEdit = useCan("extraction:edit");
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  const loaded = useProjectLoaded();
  const realQ = useDocumentReview(activeDocumentId ?? undefined, locale);
  const demoQ = useReview(locale, !usingReal);
  const data = usingReal ? realQ.data : demoQ.data;
  const isPending = usingReal ? realQ.isPending : demoQ.isPending;
  const openCheck = useUI((s) => s.openCheck);
  const toggleCheck = useUI((s) => s.toggleCheck);
  const navigate = useNavigate();
  const acceptMut = useAcceptFinding(activeDocumentId ?? undefined);
  const withdrawMut = useWithdrawAcceptance(activeDocumentId ?? undefined);
  // The flip lands on the ordinary edit endpoint, so it reuses the ordinary edit hook: the edit
  // is snapshotted (revertible), its reason is stored, and every value-driven check re-derives.
  const fixMut = useEditDocumentLineItem(activeDocumentId ?? undefined);
  // Re-mapping a row is the OTHER write the queue offers, and the only one that resolves a
  // row-shaped finding. Its own hook, because it invalidates the same set an edit does — the
  // figure moves into a different concept, so the grid, the KPIs and the export all change.
  const remapMut = useRemapReviewRow(activeDocumentId ?? undefined);
  // Which tab is selected. It was hardcoded to 0 and the chips had no onClick, so five filters
  // that looked clickable — cursor: pointer and all — did nothing.
  const [tab, setTab] = useState(0);
  // The acceptance reason, per card. Kept here rather than in the card so a refetch (which every
  // judgement triggers) cannot discard half-typed text.
  //
  // Keyed on `subject_key` — the identity the POST carries — NOT on `c.id`. Two of the check
  // builders derive their id from the extracted row's INDEX (`chk-unmapped-{i}`), which the
  // backend documents as a render key only: it moves whenever extraction composition changes. A
  // reason typed against chk-unmapped-3 and still on screen after a refetch that made index 3 a
  // different line item would be submitted against the NEW card's subject_key, recording a
  // justification belonging to another line. The error map is keyed the same way, so a 409 cannot
  // surface on an unrelated card either.
  const [reasons, setReasons] = useState<Record<string, string>>({});
  // Why the last action on a card was refused. Per card, because a rejected acceptance that
  // silently vanishes looks exactly like one that was recorded.
  const [errors, setErrors] = useState<Record<string, string>>({});
  // The concept picked in a card's re-map select, keyed on the ROW HANDLE the POST carries — not on
  // the card id, for the same reason the reason box is keyed on the subject: a card id embeds the
  // row's index and moves when extraction composition does, so a pick still on screen after a
  // refetch would be submitted for a different row. Absent means "nothing picked yet", which is
  // distinct from "" — the analyst's deliberate choice to leave the row unmapped.
  const [picks, setPicks] = useState<Record<string, string>>({});
  // WHY THE RE-MAP REASON IS ITS OWN MAP, keyed on `row_ref` beside `picks` rather than sharing the
  // acceptance reason's slot. Same rule as above — a map is keyed on the identity ITS post carries,
  // and the re-map POST carries the row handle, not the subject — and here the rule has a second
  // consequence the shared slot broke outright: a row-shaped finding is judgeable AND re-mappable,
  // so one card renders both textareas, and behind one slot they mirrored each other keystroke for
  // keystroke. The two are different records at different endpoints — why the figure was MOVED, and
  // why it was accepted AS IS — so whichever button the reviewer pressed stored the other's
  // sentence: "wrong concept, this is trade receivables" filed as the justification for letting the
  // figure stand.
  const [remapReasons, setRemapReasons] = useState<Record<string, string>>({});

  // No real document and no admin-seeded demo → greenfield guidance.
  if (!usingReal && !loaded) return <EmptyState />;
  if (usingReal && realQ.isError) return <EmptyState />;
  if (isPending || !data) {
    return (
      <div style={{ padding: 60, textAlign: "center", color: color.muted, fontSize: 13 }}>
        Loading…
      </div>
    );
  }

  const { checks, tabs, summary } = data;
  const orphaned = data.judgements?.orphaned ?? [];
  // How many orphan rows would print THE SAME name. Each of those rows carries its own destructive
  // Withdraw, and two byte-identical rows with a Withdraw each is a reviewer being asked which of
  // two standing verdicts to destroy without being told which is which. The server names the
  // finding (documents.py::_subject_label) and the fix there is what makes these names distinct —
  // but the screen must not DEPEND on that holding for every subject kind now and forever, so
  // where a name is shared the row also prints, and the control also names, the identity the
  // DELETE addresses. Keyed on the served LABEL, never on the row's position in this list.
  const orphanLabelCount = orphaned.reduce<Record<string, number>>((acc, o) => {
    acc[o.subject_label] = (acc[o.subject_label] ?? 0) + 1;
    return acc;
  }, {});
  // Each tab names the check types it selects (`types: null` = everything), so a chip filters by
  // what it MEANS. Picking by chip position instead is how the Page Scope chips came to filter by
  // the wrong page kind. A tab index that no longer exists (a refetch returning fewer tabs) falls
  // back to everything rather than showing an empty list.
  //
  // Status is deliberately NOT a filter dimension: an accepted finding stays in the list its tab
  // counts, so a chip's number is still the length of the list clicking it produces. A second
  // predicate would have to be spelled once in Python for the counts and once here for the rows.
  const active = tabs[tab] ?? tabs[0];
  const selectedTypes = active?.types ?? null;
  const shown = selectedTypes ? checks.filter((c) => selectedTypes.includes(c.type)) : checks;

  const failed = (id: string, err: unknown) =>
    setErrors((s) => ({ ...s, [id]: judgementErrorText(err, t) }));

  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: "26px 30px 60px" }}>
      {/* Header row */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 600, marginBottom: 5 }}>{t("r.title")}</h1>
          <p style={{ margin: 0, color: color.sec2 }}>{t("r.subhead")}</p>
        </div>
        {/* Three numbers, each the SERVER'S and each labelled with what it counts — the tiles do
            no arithmetic, and no tile's label describes a set wider than the number covers.
            `open` and `accepted` count CARDS in the list below (open includes the stale and
            conflict cards, both outstanding, and the two strips underneath name those subsets).
            `passed` counts LINES, not cards and not relations: extracted line items that NO
            served finding names. It used to be rows minus (unmapped + low-confidence) while the
            label already read "lines with no finding", so every line indicted by a balance, note
            tie, structural, guard or calculated_mismatch finding was counted as
            having none — the label asserted a membership the number did not have. The server now
            derives it from the lines each builder says its card indicts, identically on the real
            and sample paths (documents.py::_build_review, projects.py::_demo_review_summary), so
            the tile is read straight off `summary.passed` and nothing here recomputes it. A weakly
            mapped line is excluded from it too — it raises no card, but "no finding names it" must
            not certify it — and how many there are is stated below rather than tiled here, because
            it is not outstanding work and a fourth tile would read as a fourth queue. */}
        <div style={{ display: "flex", gap: 8 }}>
          <Counter value={summary.open} label={t("r.open")} fg={color.redFg} testid="rv-open" />
          <Counter value={summary.accepted} label={t("r.accepted")} fg={color.greenFg}
                   testid="rv-accepted" />
          <Counter value={summary.passed} label={t("r.passed")} fg={color.greenFg}
                   testid="rv-passed" />
        </div>
      </div>

      {/* Someone vouched for a figure that has since moved: the most urgent thing on the screen,
          and shown ONLY when there is one. */}
      {summary.stale > 0 && (
        <div
          data-testid="rv-stale-strip"
          style={{
            marginBottom: 12, padding: "8px 12px", borderRadius: 9,
            background: color.amberBg, border: `1px solid ${color.amberFg}44`,
            color: color.redFg, fontSize: 12, fontWeight: 600,
          }}
        >
          {summary.stale} {t("r.staleStrip")}
        </div>
      )}

      {/* Findings the queue cannot tell apart — nobody can accept them, so it is stated once at
          the top as well as on each card. The count is the server's, over the very cards below,
          and the strip is absent rather than showing a zero. */}
      {summary.conflict > 0 && (
        <div
          data-testid="rv-conflict-strip"
          style={{
            marginBottom: 12, padding: "8px 12px", borderRadius: 9,
            background: color.redBg, border: `1px solid ${color.redFg}44`,
            color: color.redFg, fontSize: 12, fontWeight: 600,
          }}
        >
          {summary.conflict} {t("r.conflictStrip")}
        </div>
      )}

      {/* NOT A QUEUE AND NOT A TILE. A weak mapping is no longer a finding — this screen reports a
          face figure that reaches no line, a validation rule that failed, and a subtotal that does
          not match its components — but the count has to appear somewhere, or the queue getting
          shorter reads as the extraction getting better. Stated in a sentence, in the server's own
          number, and absent rather than showing a zero. */}
      {data.weak_mappings > 0 && (
        <div
          data-testid="rv-weak-strip"
          style={{
            marginBottom: 12, padding: "8px 12px", borderRadius: 9,
            background: color.surface, border: `1px solid ${color.cardBorder}`,
            color: color.sec, fontSize: 11.5, lineHeight: 1.5,
          }}
        >
          <b style={{ color: color.ink }}>{data.weak_mappings}</b> {t("r.weakMappings")}
        </div>
      )}

      {/* What the template's relations could check at all — the counterpart to the failures
          below, so "3 relations passed" cannot read as "the statement is verified". */}
      <CoverageBand block={data.coverage} />

      {/* Filter tabs */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        {tabs.map((tb, i) => {
          const on = i === tab;
          return (
            <span
              key={tb.label}
              data-testid="rv-tab"
              data-on={on}
              onClick={() => setTab(i)}
              style={{
                fontSize: 11.5,
                fontWeight: 600,
                padding: "6px 12px",
                borderRadius: 8,
                background: on ? color.stepperActive : "#fff",
                color: on ? "#fff" : color.sec,
                border: `1px solid ${on ? color.stepperActive : color.controlBorder}`,
                cursor: "pointer",
              }}
            >
              {tb.label} <span style={{ opacity: 0.7 }}>{tb.count}</span>
            </span>
          );
        })}
      </div>

      {/* Check cards — the selected tab's checks, so the chip's count IS this list's length, in
          the SERVER'S order (conflict first, then stale, then open, then accepted). The client
          does no sorting: two orderings of one list is how the top card stops being the most
          urgent one. */}
      {shown.map((c) => {
        const open = openCheck === c.id;
        const { ac, ib } = toneColors(c.tone);
        // The queue cannot tell this finding from another that printed different figures, so no
        // verdict may be attributed to it. Read from the server's flag OR its status: either one
        // alone is enough to withhold the controls, and requiring both would let a half-populated
        // payload offer acceptance on a card the server has refused.
        const conflict = c.conflict || c.status === "conflict";
        // A conflict card shows NO judgement: the stored one belongs to some finding in the group
        // and nothing here says which, so printing it would put a named reviewer's reason and
        // timestamp on figures they may never have seen. `judgement_withheld` says it exists;
        // `conflict_note` says so in words.
        const j = conflict ? null : c.judgement;
        const accepted = !conflict && c.status === "accepted";
        const stale = !conflict && c.status === "stale";
        // A state this build does not know: no controls, and said out loud, because a card with
        // neither controls nor an explanation reads as broken rather than as withheld.
        const unknownState = !conflict && !KNOWN_STATUS.has(c.status);
        // The key for this card's local state: the identity the submission uses. `c.id` is only a
        // render key (see the comment on `reasons`); it is the fallback solely for the sample
        // path, whose checks carry no subject_key and submit nothing. Two cards in an `ambiguous`
        // group therefore share the textarea and the error — correctly: they post ONE subject_key,
        // so it is one reason and one refusal, not two that could disagree. The card also carries
        // `data-subject-key`, so a test can tie a card to the identity it submits under rather
        // than to its position in the list — the mistake this keying exists to prevent.
        const sk = c.subject_key ?? c.id;
        const reason = reasons[sk] ?? "";
        const err = errors[sk];
        // A judgement is recorded against a DOCUMENT's finding, identified by its subject key and
        // the figures it was judged against. The seeded sample path has none of those (its checks
        // carry `subject_key: null`), so it gets no accept control at all rather than a button
        // that could only fail — the same rule that took the two dead buttons off the real path.
        // Conflict and unrecognised states are excluded here, once, so every control below is
        // gated on one predicate instead of each remembering the exclusion for itself.
        const judgeable = usingReal && !!c.subject_key && !!c.evidence_digest
                          && !conflict && !unknownState;
        // Is there a stored acceptance the DELETE would find? The payload says so in exactly two
        // ways, and BOTH are it: `judgement` — this card's own in-force acceptance, whether it
        // still covers the figures (`accepted`) or no longer does (`stale`) — and
        // `judgement_withheld` — an acceptance the server holds against this subject but refuses
        // to attribute to any card in a conflict group. `accepted` alone was a PROXY for this and
        // was false in both of the other two cases, which left a real, named acceptance standing
        // with no control able to remove it.
        //
        // Deliberately NOT gated on `judgeable`, `conflict` or the status whitelist: the DELETE
        // takes the subject key and nothing else, refuses only when no in-force row exists (404),
        // and specifically permits a conflicted subject. Gating a control on more than the server
        // requires hides a capability that exists; gating it on less offers one that does not.
        const withdrawable = usingReal && !!c.subject_key
                             && (!!c.judgement || c.judgement_withheld);
        // WHICH CARD's action is in flight — not "some card's". `mut.isPending` is the mutation's
        // state and there is one mutation per screen, so accepting one finding disabled Accept on
        // every other card at once, saying those findings could not be judged when they could.
        // React Query keeps the variables of the call in flight, and they carry the identity it
        // was made under, so each card can ask about itself instead of about the screen.
        const acceptBusy = acceptMut.isPending
                           && acceptMut.variables?.subjectKey === c.subject_key;
        const withdrawBusy = withdrawMut.isPending && withdrawMut.variables === c.subject_key;
        // The row handle this card's re-map submits under, and whether THIS card's re-map is the
        // one in flight — the same per-card reasoning as `acceptBusy`: one mutation per screen, so
        // asking `remapMut.isPending` would disable every other card's control at once.
        const rr = c.remap?.row_ref ?? "";
        const remapBusy = remapMut.isPending && !!rr && remapMut.variables?.rowRef === rr;
        const fa = c.fix_action;
        const fixBusy = fixMut.isPending && !!fa && fixMut.variables?.key === fa.canonical_key
                        && fixMut.variables?.basis === fa.basis
                        && fixMut.variables?.period === fa.period;
        return (
          <div
            key={c.id}
            data-testid="rv-check"
            data-status={c.status}
            data-subject-key={c.subject_key ?? ""}
            style={{
              background: "#fff",
              border: `1px solid ${open ? (accepted ? color.greenFg : ac) : color.cardBorder}`,
              borderLeft: `3px solid ${accepted ? color.greenFg : ac}`,
              borderRadius: 11,
              marginBottom: 12,
              overflow: "hidden",
            }}
          >
            {/* Header */}
            <div
              onClick={() => toggleCheck(c.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 13,
                padding: "14px 16px",
                cursor: "pointer",
              }}
            >
              <span
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: 7,
                  background: accepted ? color.greenBg : ib,
                  color: accepted ? color.greenFg : ac,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 13,
                  fontWeight: 700,
                  flex: "0 0 auto",
                }}
              >
                {c.icon}
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{c.title}</div>
                <div style={{ fontSize: 11.5, color: color.muted }}>{c.where}</div>
                {/* Who vouched for this, and when — visible without expanding the card, because
                    telling "not looked at" from "reviewed and accepted" is the whole point. */}
                {j && (
                  <div data-testid="rv-judged-by" style={{ fontSize: 11, color: color.greenFg }}>
                    {t("r.acceptedBy")} {j.actor} · {j.at}
                  </div>
                )}
              </div>
              {accepted && (
                <span
                  data-testid="rv-accepted-pill"
                  style={{
                    fontSize: 10, fontWeight: 700, padding: "3px 9px", borderRadius: 20,
                    background: color.greenBg, color: color.greenFg, letterSpacing: 0.3,
                  }}
                >
                  {t("r.statusAccepted")}
                </span>
              )}
              {/* Visible while the card is still collapsed: the reason there is no Accept button
                  inside must not be discoverable only by expanding and finding nothing. */}
              {conflict && (
                <span
                  data-testid="rv-conflict-pill"
                  style={{
                    fontSize: 10, fontWeight: 700, padding: "3px 9px", borderRadius: 20,
                    background: color.redBg, color: color.redFg, letterSpacing: 0.3,
                  }}
                >
                  {t("r.statusConflict")}
                </span>
              )}
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 600,
                  padding: "3px 9px",
                  borderRadius: 20,
                  background: ib,
                  color: ac,
                }}
              >
                {c.severity}
              </span>
              <span style={{ fontFamily: font.mono, fontSize: 12, color: ac, fontWeight: 600 }}>
                {c.delta}
              </span>
              <span style={{ fontSize: 11, color: color.faint }}>{open ? "▲" : "▼"}</span>
            </div>

            {/* Body */}
            {open && (
              <div
                style={{
                  borderTop: `1px solid ${color.hairline2}`,
                  padding: "15px 16px 16px",
                  background: "#fbfcfd",
                }}
              >
                {/* Identity failed: this finding and at least one other share a subject_key while
                    printing DIFFERENT figures, so the queue cannot say which of them any verdict
                    belongs to. First in the body, and the card offers no acceptance at all — a
                    refusal stated in words is honest; attributing a stored judgement to one of
                    them (which is what shipped) puts a reviewer's name on figures they may never
                    have seen. The sentence is the SERVER'S, already localized and already
                    carrying the count, so the screen composes no second version of it; a conflict
                    served without one leaves the message hook absent rather than inventing text. */}
                {conflict && (
                  <div
                    data-testid="rv-conflict"
                    style={{
                      marginBottom: 13, padding: "10px 12px", borderRadius: 9,
                      background: color.redBg, border: `1px solid ${color.redFg}44`,
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 700, color: color.redFg }}>
                      {t("r.conflictTitle")}
                    </div>
                    {c.conflict_note && (
                      <div
                        data-testid="rv-conflict-message"
                        style={{ fontSize: 11.5, color: color.ink2, marginTop: 3, lineHeight: 1.5 }}
                      >
                        {c.conflict_note}
                      </div>
                    )}
                  </div>
                )}

                {/* A state this build cannot interpret. Named, not guessed at: no controls, and
                    the served value printed so the mismatch is reportable. */}
                {unknownState && (
                  <div
                    data-testid="rv-unknown-state"
                    style={{
                      marginBottom: 13, padding: "10px 12px", borderRadius: 9,
                      background: color.amberBg, border: `1px solid ${color.amberFg}44`,
                      fontSize: 11.5, color: color.ink2, lineHeight: 1.5,
                    }}
                  >
                    {t("r.unknownState")} <span style={{ fontFamily: font.mono }}>{c.status}</span>
                  </div>
                )}

                {/* An acceptance that no longer covers the figures on the card: loud, at the top,
                    and carrying the evidence AS JUDGED so the reader can see what moved. */}
                {stale && j && (
                  <div
                    data-testid="rv-stale"
                    style={{
                      marginBottom: 13, padding: "10px 12px", borderRadius: 9,
                      background: color.redBg, border: `1px solid ${color.redFg}44`,
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 700, color: color.redFg }}>
                      {t("r.staleTitle")}
                    </div>
                    <div style={{ fontSize: 11.5, color: color.sec, marginTop: 3 }}>
                      {t("r.acceptedBy")} {j.actor} · {j.at}
                    </div>
                    <div style={{ fontSize: 11.5, color: color.ink2, marginTop: 3, lineHeight: 1.5 }}>
                      {j.reason}
                    </div>
                    <div style={{ fontSize: 11.5, color: color.redFg, marginTop: 5, fontWeight: 600 }}>
                      {t("r.staleChanged")} {j.changed_label}
                    </div>
                    <div style={{ marginTop: 5 }}>
                      <FigureRows rows={j.accepted_rows} accent={color.redFg} />
                    </div>
                  </div>
                )}

                {/* One judgement covers every finding with an identical subject and identical
                    figures — the card says how many, from the server's count. Never shown on a
                    conflict card: "accepting one accepts them all" is false of findings that
                    printed different figures, and that caption over such a pair is the defect. */}
                {c.ambiguous && !conflict && (
                  <div
                    data-testid="rv-ambiguous"
                    style={{ fontSize: 11, color: color.amberFg, marginBottom: 10, lineHeight: 1.5 }}
                  >
                    {c.ambiguous_count} {t("r.ambiguousNote")}
                  </div>
                )}

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 20,
                    marginBottom: 14,
                  }}
                >
                  {/* LEFT — reconciliation */}
                  <div>
                    <div
                      style={{
                        fontSize: 10.5,
                        fontWeight: 600,
                        letterSpacing: 0.4,
                        color: color.muted,
                        marginBottom: 8,
                      }}
                    >
                      {t("r.reconciliation")}
                    </div>
                    <FigureRows rows={c.calc} accent={ac} />
                  </div>

                  {/* RIGHT — suggested fix */}
                  <div>
                    <div
                      style={{
                        fontSize: 10.5,
                        fontWeight: 600,
                        letterSpacing: 0.4,
                        color: color.muted,
                        marginBottom: 8,
                      }}
                    >
                      {t("r.suggestedFix")}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        color: color.ink2,
                        lineHeight: 1.55,
                        background: "#fff",
                        border: `1px solid ${color.hairline3}`,
                        borderRadius: 8,
                        padding: 11,
                      }}
                    >
                      {c.fix}
                    </div>
                    {/* Most findings have no mechanical correction — a balance identity has two
                        sides, and overwriting a computed subtotal with the printed figure would
                        hide the component that caused it. Said in a sentence: a greyed-out button
                        still advertises a capability that does not exist. */}
                    {!c.fix_action && !c.remap && (
                      <div style={{ fontSize: 11, color: color.muted, marginTop: 7, lineHeight: 1.5 }}>
                        {t("r.manualFixOnly")}
                      </div>
                    )}
                    {/* The relation behind a structural card is evaluated by the pipeline and is
                        not re-run on an edit, so the card survives its own fix until the next
                        extraction. Rather than look like a button that did nothing, it says so. */}
                    {c.inputs_edited && c.inputs_edited_note && (
                      <div
                        data-testid="rv-inputs-edited"
                        style={{ fontSize: 11, color: color.amberFg, marginTop: 7, lineHeight: 1.5 }}
                      >
                        {c.inputs_edited_note}
                      </div>
                    )}
                  </div>
                </div>

                {/* THE FIX FOR A ROW-SHAPED FINDING. Both cards' prose has always told the analyst
                    to pick the right line ("Pick the correct template line item", "Confirm the
                    concept is correct or reassign it"); this is the control that does it. Rendered
                    for anyone, disabled without extraction:edit — the same gate as the endpoint. */}
                {c.remap && (
                  <RemapPanel
                    offer={c.remap}
                    targets={data.remap_targets}
                    picked={picks[c.remap.row_ref]}
                    onPick={(key) => setPicks((s) => ({ ...s, [rr]: key }))}
                    reason={remapReasons[rr] ?? ""}
                    onReason={(text) => setRemapReasons((s) => ({ ...s, [rr]: text }))}
                    busy={remapBusy}
                    canEdit={canEdit}
                    t={t}
                    onApply={() => {
                      setErrors((s) => ({ ...s, [sk]: "" }));
                      remapMut.mutate(
                        { rowRef: rr, canonicalKey: picks[rr] ?? "",
                          reason: (remapReasons[rr] ?? "").trim(), locale },
                        {
                          // The row moves, so the card goes; neither box's text may survive onto
                          // whatever card the refetch puts in its place — the acceptance reason
                          // included, which was typed against a finding that no longer exists.
                          onSuccess: () => {
                            setPicks((s) => { const n = { ...s }; delete n[rr]; return n; });
                            setRemapReasons((s) => { const n = { ...s }; delete n[rr]; return n; });
                            setReasons((s) => ({ ...s, [sk]: "" }));
                          },
                          // The refusal is shown in the CARD's error slot, so it is keyed the way
                          // that slot is read — on the subject — not on the row handle the request
                          // carried.
                          onError: (e) => failed(sk, e),
                        },
                      );
                    }}
                  />
                )}

                {/* The judgement on the record, for an accepted card: what was said, and the
                    figures it was said about. Read-only for a role that cannot resolve. */}
                {accepted && j && (
                  <div
                    data-testid="rv-judgement"
                    style={{
                      marginBottom: 13, padding: "10px 12px", borderRadius: 9,
                      background: color.greenBg2, border: `1px solid ${color.greenFg}33`,
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 600, color: color.greenFg }}>
                      {t("r.reason")}
                    </div>
                    <div style={{ fontSize: 12, color: color.ink2, lineHeight: 1.5 }}>{j.reason}</div>
                    <div style={{ marginTop: 6 }}>
                      <FigureRows rows={j.accepted_rows} accent={color.greenFg} />
                    </div>
                  </div>
                )}

                {/* The reason is required, so it is typed BEFORE the button can be pressed —
                    letting an empty acceptance reach the server only to be refused would teach
                    the reviewer that the button is unreliable. */}
                {judgeable && canResolve && ACCEPTABLE_STATUS.has(c.status) && (
                  <div style={{ marginBottom: 11 }}>
                    <textarea
                      data-testid="rv-reason"
                      value={reason}
                      maxLength={2000}
                      rows={2}
                      placeholder={t("r.reasonPlaceholder")}
                      onChange={(e) => setReasons((s) => ({ ...s, [sk]: e.target.value }))}
                      style={{
                        width: "100%", boxSizing: "border-box", fontSize: 11.5,
                        fontFamily: font.sans, color: color.ink, resize: "vertical",
                        border: `1px solid ${color.controlBorder}`, borderRadius: 8,
                        padding: "7px 9px", outline: "none", lineHeight: 1.5,
                      }}
                    />
                  </div>
                )}

                {err && (
                  <div
                    data-testid="rv-error"
                    style={{
                      marginBottom: 11, padding: "7px 11px", background: color.redBg,
                      border: `1px solid ${color.redFg}22`, borderRadius: 8,
                      fontSize: 11.5, color: color.redFg, lineHeight: 1.5,
                    }}
                  >
                    {err}
                  </div>
                )}

                {/* An acceptance the server holds against this subject but will not attribute to
                    any card in the conflict group. The card shows no verdict — attributing one
                    would be the defect — so the control to remove it needs a sentence saying what
                    it acts on, otherwise "Withdraw acceptance" sits under a card displaying no
                    acceptance and reads as a button that does nothing. Rendered only alongside the
                    control it explains. */}
                {withdrawable && canResolve && c.judgement_withheld && (
                  <div
                    data-testid="rv-withheld-withdrawable"
                    style={{
                      marginBottom: 11, fontSize: 11, color: color.amberFg, lineHeight: 1.5,
                    }}
                  >
                    {t("r.withheldWithdrawable")}
                  </div>
                )}

                {/* Actions */}
                <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
                  {/* The ONE mechanical fix: flip a mis-signed figure. Gated on extraction:edit
                      alone, because that is the only gate on the PATCH it calls. */}
                  {fa && canEdit && (
                    <>
                      <Button
                        variant="primary"
                        testid="rv-fix"
                        disabled={fixBusy}
                        onClick={() => {
                          setErrors((s) => ({ ...s, [sk]: "" }));
                          fixMut.mutate(
                            {
                              key: fa.canonical_key, value: fa.to, formula: "",
                              basis: fa.basis, period: fa.period, comment: fa.comment,
                            },
                            { onError: (e) => failed(sk, e) },
                          );
                        }}
                        style={{ fontSize: 12, padding: "8px 15px", borderRadius: 8 }}
                      >
                        {t("r.flipSignOf")} {fa.label}
                      </Button>
                      {/* Both figures come formatted from the server; this is a caption, not a
                          control. */}
                      <span style={{ fontFamily: font.mono, fontSize: 11.5, color: color.sec2 }}>
                        {fa.from_display} → {fa.to_display}
                      </span>
                    </>
                  )}
                  <Button
                    variant="secondary"
                    onClick={() => navigate(SCREENS.workspace.path)}
                    style={{ fontSize: 12, padding: "8px 15px", borderRadius: 8 }}
                  >
                    {t("r.openInWorkspace")}
                  </Button>
                  {judgeable && canResolve && ACCEPTABLE_STATUS.has(c.status) && (
                    <Button
                      variant="secondary"
                      testid="rv-accept"
                      disabled={reason.trim() === "" || acceptBusy}
                      onClick={() => {
                        setErrors((s) => ({ ...s, [sk]: "" }));
                        acceptMut.mutate(
                          {
                            subjectKey: c.subject_key as string,
                            evidenceDigest: c.evidence_digest ?? "",
                            reason: reason.trim(),
                            locale,
                          },
                          {
                            onSuccess: () => setReasons((s) => ({ ...s, [sk]: "" })),
                            onError: (e) => failed(sk, e),
                          },
                        );
                      }}
                      style={{
                        fontSize: 12,
                        padding: "8px 15px",
                        borderRadius: 8,
                        color: color.sec2,
                        border: `1px solid ${color.cardBorder}`,
                      }}
                    >
                      {t("r.acceptAsIs")}
                    </Button>
                  )}
                  {/* Withdraw. Gated on `withdrawable` — a stored in-force acceptance on this
                      subject, which is the ONE condition the DELETE has — and NOT on `judgeable`,
                      `accepted` or the status whitelist. `data-withheld` says which of the two
                      shapes it is standing in for, so a test can reach the case that had no
                      control at all: a conflict card whose judgement the server holds. */}
                  {withdrawable && canResolve && (
                    <Button
                      variant="secondary"
                      testid="rv-withdraw"
                      data={{ withheld: c.judgement_withheld ? "true" : "false",
                              status: c.status }}
                      disabled={withdrawBusy}
                      onClick={() => {
                        setErrors((s) => ({ ...s, [sk]: "" }));
                        withdrawMut.mutate(c.subject_key as string,
                                           { onError: (e) => failed(sk, e) });
                      }}
                      style={{
                        fontSize: 12,
                        padding: "8px 15px",
                        borderRadius: 8,
                        color: color.sec2,
                        border: `1px solid ${color.cardBorder}`,
                      }}
                    >
                      {t("r.withdrawAcceptance")}
                    </Button>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}

      {/* Judgements whose finding is gone — corrected, or no longer raised. Never deleted, and
          counted nowhere above: the count here is this array's own length.

          EACH ROW IS AN IN-FORCE ACCEPTANCE, AND EACH ROW CARRIES ITS OWN WITHDRAW CONTROL. The
          control used to live only inside a check card, gated on a card-level proxy for "the server
          holds a row" — and an orphan has no card, so a named, standing acceptance was rendered on
          screen with nothing anywhere able to remove it. It is not inert either: the stored verdict
          is still `accepted`, so the moment the same subject is raised again with the same figures
          the card comes back status='accepted' under a verdict nobody re-made. `judgement.py`
          builds this list from the IN-FORCE rows (`apply_judgements`, verdict 'accepted'), which is
          exactly the row DELETE /review/judgements/{subject_key} acts on, so the gate here is the
          row itself plus a document to address the DELETE to — nothing about any card. */}
      {orphaned.length > 0 && (
        <div
          data-testid="rv-orphaned"
          style={{
            marginTop: 18, padding: "13px 16px", background: "#fff",
            border: `1px solid ${color.cardBorder}`, borderLeft: `3px solid ${color.muted}`,
            borderRadius: 11,
          }}
        >
          <div style={{ fontSize: 12.5, fontWeight: 600 }}>
            {orphaned.length} {t("r.orphanedTitle")}
          </div>
          <div style={{ fontSize: 11, color: color.muted, lineHeight: 1.5, marginBottom: 7 }}>
            {t("r.orphanedNote")}
          </div>
          {/* Said before the rows, because "no longer raised" reads as "finished with" and these
              verdicts are still standing. The instruction to withdraw is appended only where the
              control below actually appears — telling a reader to press a button their role is not
              granted is the same defect as showing them the button. */}
          <div
            data-testid="rv-orphaned-inforce"
            style={{ fontSize: 11, color: color.amberFg, lineHeight: 1.5, marginBottom: 7 }}
          >
            {t("r.orphanedInForce")}
            {canResolve && usingReal ? ` ${t("r.orphanedWithdrawHint")}` : ""}
          </div>
          {orphaned.map((o) => {
            const busy = withdrawMut.isPending && withdrawMut.variables === o.subject_key;
            // Keyed on the subject key, the same key the card errors use: it IS the identity the
            // DELETE carries, so a refusal cannot surface against another row's judgement.
            const oerr = errors[o.subject_key];
            // Another row in this list prints the same name, so the name alone cannot say which
            // verdict a Withdraw here takes back. The subject key is distinct by construction —
            // the payload is built one row per key — so it is what separates them. Abbreviated for
            // a 11.5px row; the full key is on the row's `data-subject-key`, in this chip's
            // tooltip and in the control's accessible name, so the short form is never the only
            // copy of it on the page.
            const shared = (orphanLabelCount[o.subject_label] ?? 0) > 1;
            // The name a screen reader announces for THIS row's Withdraw. Every copy of the button
            // shows the same words, so without this the control is "Withdraw acceptance" eleven
            // times over eleven different standing verdicts.
            const withdrawName = shared
              ? `${t("r.withdrawAcceptance")} — ${o.subject_label} · `
                + `${t("r.orphanIdentity")} ${o.subject_key}`
              : `${t("r.withdrawAcceptance")} — ${o.subject_label}`;
            return (
              <div
                key={o.subject_key}
                data-testid="rv-orphan"
                data-subject-key={o.subject_key}
                data-shared-label={shared ? "true" : "false"}
                style={{
                  fontSize: 11.5, color: color.sec, lineHeight: 1.6,
                  borderTop: `1px dashed ${color.hairline2}`, padding: "6px 0",
                  display: "flex", alignItems: "flex-start", gap: 10,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{o.subject_label}</span>
                  {shared && (
                    <>
                      {" "}
                      <span
                        data-testid="rv-orphan-identity"
                        title={`${t("r.orphanIdentityHelp")} ${o.subject_key}`}
                        style={{ fontFamily: font.mono, fontSize: 10.5, color: color.muted }}
                      >
                        {t("r.orphanIdentity")} {o.subject_key.slice(0, 12)}
                      </span>
                    </>
                  )}{" "}
                  · {o.actor}{" "}
                  ({o.actor_role}) · {o.at} · {o.reason}
                  {oerr && (
                    <div
                      data-testid="rv-orphan-error"
                      style={{
                        marginTop: 5, padding: "6px 10px", background: color.redBg,
                        border: `1px solid ${color.redFg}22`, borderRadius: 8,
                        fontSize: 11.5, color: color.redFg, lineHeight: 1.5,
                      }}
                    >
                      {oerr}
                    </div>
                  )}
                </div>
                {canResolve && usingReal && !!o.subject_key && (
                  <Button
                    variant="secondary"
                    testid="rv-orphan-withdraw"
                    // THE NAME COMPUTED ABOVE, WHICH WAS NEVER PASSED. `ariaLabel` was added to
                    // `ui.Button` for this control — its docstring names it — and `withdrawName`
                    // was computed three lines above the list with the comment explaining why a
                    // destructive control repeated down a list needs a per-row name. Neither
                    // reached the button, so every copy announced "Withdraw acceptance" and
                    // `withdrawName` sat unread; `noUnusedLocals` found it.
                    ariaLabel={withdrawName}
                    // Lower-case on purpose: HTML attribute names fold case, so a camelCase key
                    // would reach the DOM as `data-subjectkey` and a test written against the
                    // spelling in this file would never match.
                    data={{ subject: o.subject_key }}
                    disabled={busy}
                    onClick={() => {
                      setErrors((s) => ({ ...s, [o.subject_key]: "" }));
                      withdrawMut.mutate(o.subject_key,
                                         { onError: (e) => failed(o.subject_key, e) });
                    }}
                    style={{
                      flex: "0 0 auto", fontSize: 11.5, padding: "5px 11px", borderRadius: 8,
                      color: color.sec2, border: `1px solid ${color.cardBorder}`,
                    }}
                  >
                    {t("r.withdrawAcceptance")}
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Counter({ value, label, fg, testid }:
                 { value: number; label: string; fg: string; testid: string }) {
  return (
    <div
      data-testid={testid}
      style={{
        textAlign: "center",
        background: "#fff",
        border: `1px solid ${color.cardBorder}`,
        borderRadius: 10,
        padding: "9px 14px",
      }}
    >
      <div style={{ fontSize: 18, fontWeight: 700, color: fg, fontFamily: font.mono }}>{value}</div>
      <div style={{ fontSize: 10, color: color.muted }}>{label}</div>
    </div>
  );
}
