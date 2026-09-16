/** THE MANUAL GROUPING MASTER — which line items share one model request.
 *
 *  NEW FILE -> frontend/src/components/RequestGroups.tsx
 *
 *  WHY IT IS A SCREEN-LEVEL CARD and not a field on a line, which is the same argument
 *  `MasterPrompt` makes: a group is a relationship BETWEEN line items. Putting a "group" field on
 *  the item form would imply each line owns its grouping, would let two lines disagree about which
 *  group they are in, and would give a reader no single place to see the grouping at all. It saves
 *  the way the master prompt does — `PATCH /line-items/versions/{id}` publishes a new version.
 *
 *  THE COVERAGE LINE IS THE LOAD-BEARING PART OF THIS SCREEN. A master naming three groups out of
 *  seventy-seven asked-about lines and one naming all seventy-seven look identical as a list of
 *  groups, and only one of them is finished. So the header states how many lines are covered and
 *  how many get their own request, because that is the question an author returns to answer.
 *
 *  WHAT IT DOES NOT OFFER: the MODE. `extraction.llm_request_grouping` is a run-time setting and
 *  lives with the other `llm_*` values on the Settings screen — it changes per run, where the
 *  master does not. The card says which mode reads it so the two are not authored in isolation.
 */
import { useState } from "react";

import { Button, Card } from "./ui";
import { InfoToggle, TextArea, type KeyOption } from "./configFields";
import { color, font, radius } from "../theme";
import { useEditLineItemSet } from "../lib/queries";
import { ApiError, refusalText } from "../lib/api";
import type { LineItemSetInfo, RequestGroup } from "../types";

export function RequestGroups({ versionId, set, eligible, ineligible, canEdit }: {
  versionId: string;
  set: LineItemSetInfo;
  /** The lines the model IS asked about — the only ones a group may name. Filtered by the CALLER,
   *  which has the line definitions; `KeyOption` carries only a key and a label, and widening it
   *  to carry `type` would push schema knowledge into a control that does not need it.
   *
   *  MIRRORS `services.line_item_requests.asked_about`, and the server refuses a group naming
   *  anything else. Filtered here as well so the picker cannot offer a member the save will
   *  reject: offering a choice the validator refuses is the defect this screen exists to remove. */
  eligible: KeyOption[];
  /** How many lines are NOT eligible — derived parents and section residuals. Stated on the card
   *  so their absence from the picker reads as a rule rather than as a missing option. */
  ineligible: number;
  canEdit: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showNote, setShowNote] = useState(false);
  const served = set.request_groups ?? [];
  const [draft, setDraft] = useState<RequestGroup[]>(served);
  // RE-SEED WHEN THE SERVER SERVES A DIFFERENT VERSION, so a publish elsewhere is not overwritten
  // by a stale draft still sitting in this card.
  //
  // KEYED ON `versionId`, NOT ON `served`, AND THAT DISTINCTION WAS AN INFINITE LOOP. The guard
  // read `if (seed !== served)`, a REFERENCE comparison — and `served` is
  // `set.request_groups ?? []`, so on every set that declares no groups (which is every shipped
  // one: the field is absent, not empty) the `??` minted a BRAND NEW ARRAY each render. The
  // condition was therefore permanently true: setSeed -> render -> new [] -> setSeed, until React
  // gave up with "Too many re-renders", taking the whole Line Items screen down with it.
  //
  // `MasterPrompt` has the identical shape and survives only because its `served` is a STRING
  // (`?? ""`), which compares by value. That is luck, not design, so both are now keyed on the
  // version — which is what the sentence above always said the trigger was.
  // AND NEVER OVER A DRAFT THE AUTHOR HAS EDITED. `versionId` advances on every publish from the
  // Line Items screen, the author's own line-item save included, so re-seeding unconditionally is
  // a no-op whenever this card is clean and a silent discard whenever it is not. The served value
  // is carried in the cell as well as compared against, because "unchanged from what was served"
  // is a question only the PREVIOUS served value can answer — and compared by VALUE here, the same
  // way `dirty` is below, for the `?? []` reason the paragraph above records.
  const [seed, setSeed] = useState({ id: versionId, served, stale: false });
  if (seed.id !== versionId) {
    // Clean against either served value: equal to the old one means nothing was edited, equal to
    // the new one means this version change is the author's own save landing.
    const clean = JSON.stringify(draft) === JSON.stringify(seed.served)
      || JSON.stringify(draft) === JSON.stringify(served);
    setSeed({ id: versionId, served, stale: !clean });
    if (clean) setDraft(served);
  }
  const save = useEditLineItemSet();

  const named = new Set(draft.flatMap((g) => g.members));
  const covered = eligible.filter((k) => named.has(k.key)).length;
  const dirty = JSON.stringify(draft) !== JSON.stringify(served);

  const patch = (i: number, next: Partial<RequestGroup>) =>
    setDraft(draft.map((g, j) => (j === i ? { ...g, ...next } : g)));

  return (
    <Card pad={12} style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 12.5 }}>Request groups</b>
        <InfoToggle open={showNote} about="request groups"
                    onToggle={() => setShowNote((v) => !v)} />
        <span style={{ fontSize: 11, color: color.muted }}>
          {draft.length === 0
            ? "none declared — every line gets its own request"
            : `${draft.length} group${draft.length === 1 ? "" : "s"}, ${covered} of ` +
              `${eligible.length} lines covered`}
        </span>
        <span style={{ flex: 1 }} />
        <button type="button" data-testid="li-request-groups-toggle"
                onClick={() => setOpen((v) => !v)}
                style={{ fontSize: 11.5, cursor: "pointer", padding: "3px 10px",
                          border: `1px solid ${color.cardBorder}`, borderRadius: radius.control,
                          background: "transparent", color: color.sec2 }}>
          {open ? "Close" : canEdit ? "Edit" : "View"}
        </button>
      </div>

      {showNote && (
        <p style={{ margin: "7px 0 0", paddingLeft: 9, fontSize: 10.5, color: color.muted,
                     lineHeight: 1.5, borderLeft: `2px solid ${color.indigoBorder2}` }}>
          Line items in one group are asked about in ONE request, so they share one copy of the
          note context — which is what a request mostly pays for. Read only when the run's
          grouping mode is <b>manual</b> (Settings → LLM); the other modes group by the notes each
          line selected, without a master. A line named in no group still gets its own request, so
          a part-built master is usable.
        </p>
      )}

      {open && (
        <div style={{ marginTop: 10 }}>
          {draft.length === 0 && (
            <p style={{ margin: "0 0 9px", fontSize: 11, color: color.muted }}>
              Nothing declared. In <b>manual</b> mode that behaves exactly as <b>none</b>: one
              request per line item.
            </p>
          )}

          {draft.map((group, i) => (
            <div key={i} data-testid={`li-request-group-${i}`}
                 style={{ border: `1px solid ${color.hairline2}`, borderRadius: 8,
                           padding: 10, marginBottom: 9, background: color.rowAltBg }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input value={group.name} disabled={!canEdit}
                       placeholder="What this group is for"
                       onChange={(e) => patch(i, { name: e.target.value })}
                       style={{ flex: 1, fontSize: 12, padding: "4px 8px",
                                 border: `1px solid ${color.cardBorder}`,
                                 borderRadius: radius.controlSm }} />
                <span style={{ fontSize: 10.5, color: color.muted }}>
                  {group.members.length} member{group.members.length === 1 ? "" : "s"}
                </span>
                {canEdit && (
                  <Button variant="secondary" ariaLabel={`Remove group ${group.name || i + 1}`}
                          onClick={() => setDraft(draft.filter((_, j) => j !== i))}>×</Button>
                )}
              </div>

              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0 0" }}>
                {group.members.length === 0 && (
                  <span style={{ fontSize: 10.5, color: color.muted }}>
                    No members — this group asks about nothing.
                  </span>
                )}
                {group.members.map((m) => (
                  <span key={m} style={{ fontSize: 11, fontFamily: font.mono,
                                          padding: "3px 8px", borderRadius: radius.chip,
                                          background: color.indigoTint2, color: color.indigo }}>
                    {m}
                    {canEdit && (
                      <span role="button" aria-label={`Remove ${m}`} title="Remove"
                            onClick={() => patch(i, {
                              members: group.members.filter((x) => x !== m) })}
                            style={{ opacity: 0.55, cursor: "pointer", marginInlineStart: 5,
                                      fontWeight: 700 }}>×</span>
                    )}
                  </span>
                ))}
              </div>

              {canEdit && (
                // A SELECT AND NOT A FREE-TEXT BOX: every member must be an existing key the model
                // is asked about, and the server refuses anything else. Offering a text field
                // would invite a typo the author only discovers on save, which is the defect this
                // whole screen exists to remove. Already-named keys are excluded so a key cannot
                // be put in two groups — the other thing the server refuses.
                <select value="" data-testid={`li-request-group-add-${i}`}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v) patch(i, { members: [...group.members, v] });
                        }}
                        style={{ marginTop: 8, fontSize: 11.5, padding: "3px 8px",
                                  border: `1px solid ${color.cardBorder}`,
                                  borderRadius: radius.controlSm, maxWidth: 380 }}>
                  <option value="">Add a line item…</option>
                  {eligible.filter((k) => !named.has(k.key)).map((k) => (
                    <option key={k.key} value={k.key}>{k.key} — {k.label}</option>
                  ))}
                </select>
              )}

              <div style={{ marginTop: 8 }}>
                <TextArea label="Why these belong together" testid={`request_group_note_${i}`}
                          editable={canEdit} rows={2} nullable
                          value={group.note}
                          onChange={(v) => patch(i, { note: v ?? "" })} />
              </div>
            </div>
          ))}

          {canEdit && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <Button variant="secondary" testid="li-request-group-new"
                      onClick={() => setDraft([...draft, { name: "", members: [], note: "" }])}>
                + New group
              </Button>
              <Button testid="li-request-groups-save" disabled={!dirty || save.isPending}
                      onClick={() => save.mutate({ lineItemVersionId: versionId,
                                                    edit: { request_groups: draft } })}>
                {save.isPending ? "Publishing…" : "Save — publishes a new version"}
              </Button>
              {dirty && (
                <button type="button" onClick={() => setDraft(served)}
                        style={{ fontSize: 11.5, cursor: "pointer", border: 0,
                                  background: "transparent", color: color.sec2 }}>
                  Discard
                </button>
              )}
              {seed.stale && dirty && (
                // The edit survived a publish; say what it is now measured against.
                <span data-testid="li-request-groups-stale"
                      style={{ fontSize: 11, color: color.sec2 }}>
                  A new version was published while you were editing — saving replaces it with this.
                </span>
              )}
              {save.isError && (
                <span style={{ fontSize: 11, color: color.redFg }}>
                  {refusalText(save.error as ApiError) ?? (save.error as Error)?.message}
                </span>
              )}
            </div>
          )}

          {/* WHAT IS NOT COVERED, stated rather than left to be counted. An author returns to this
              card to answer "what is left", and a list of groups cannot answer it. */}
          <p style={{ margin: "10px 0 0", fontSize: 10.5, color: color.muted }}>
            {eligible.length - covered} of {eligible.length} lines the model is asked about are in
            no group and get their own request.
            {ineligible > 0 && (
              <> {ineligible} further lines are derived parents or section
              residuals, which are never asked about and cannot be grouped.</>
            )}
          </p>
        </div>
      )}
    </Card>
  );
}
