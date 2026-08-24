/** Left navigation rail: grouped screen links + the sample project's extraction-progress card.
 *
 *  COLLAPSED BY DEFAULT, to an icon strip. The screens this product is used on put a statement grid
 *  beside a page of the source document, and a permanent 214px of menu is taken from exactly that —
 *  so the rail starts out of the way and the reader opens it when they want to go somewhere. The
 *  choice is remembered per browser (see `getStoredNavCollapsed`); collapsed keeps every row, its
 *  icon, its active marker and its badge, so nothing becomes unreachable — only the labels go, and
 *  each row still carries its name as a tooltip. */
import { useLocation, useNavigate } from "react-router-dom";

import { useMe, useProject } from "../../lib/queries";
import { color } from "../../theme";
import { useT } from "../../i18n";
import { useUI } from "../../store";
import { NAV_GROUPS, SCREENS, screenIdForPath } from "../../screens/config";

export function NavRail() {
  const nav = useNavigate();
  const loc = useLocation();
  const t = useT();
  const { data: me } = useMe();
  const activeId = screenIdForPath(loc.pathname);
  // WHOSE progress the card can report. `useProject()` serves the SEEDED SAMPLE project and
  // nothing else, so with a real uploaded document active the card was printing the sample's
  // "33 line items · 4 in review" under a label naming the active extraction — on every screen,
  // including the Review screen showing that document's own 4-row figures. The same wrong-source
  // defect was fixed in Export.tsx (`usingReal ? … : sampleProgress`) and left here.
  //
  // The rail has no cheap honest source for a real document's line population: the only payloads
  // that carry one are the extraction run and the review queue, either of which is a heavy fetch
  // for a nav card that renders on all eleven screens. So the clause is OMITTED rather than
  // sourced from the wrong project or paid for with a request the rail cannot justify — and the
  // query is not issued at all while a real document is active, because there is then nothing in
  // its answer this component may print.
  const collapsed = useUI((s) => s.navCollapsed);
  const setCollapsed = useUI((s) => s.setNavCollapsed);
  const activeDocumentId = useUI((s) => s.activeDocumentId);
  const usingReal = !!activeDocumentId;
  const { data } = useProject(!usingReal);
  // `loaded` is the sample project's own "there is an extraction here" flag. Unloaded, the route
  // still serves a counted-from-nothing {0, 0}; "0 line items" under "Extraction progress" asserts
  // an extraction that has no lines, where the truth is that there is no extraction.
  const prog = !usingReal && data?.loaded ? data.project.progress : undefined;

  // Role-gated nav: show a screen only if the caller's role may see it.
  const canSee = (id: string) => !me || me.screens.includes(id);
  const groups = NAV_GROUPS.map((g) => ({ ...g, items: g.items.filter(canSee) }))
    .filter((g) => g.items.length > 0);

  const width = collapsed ? 52 : 214;
  return (
    <div
      data-testid="nav-rail"
      data-collapsed={collapsed ? "1" : "0"}
      style={{
        width,
        flex: `0 0 ${width}px`,
        background: "#fff",
        borderRight: `1px solid ${color.cardBorder}`,
        padding: "6px 0 12px",
        overflowY: "auto",
        overflowX: "hidden",
        transition: "width 120ms ease",
      }}
    >
      <div style={{ display: "flex", justifyContent: collapsed ? "center" : "flex-end",
                    padding: collapsed ? "2px 0 6px" : "2px 10px 6px" }}>
        <button
          type="button"
          data-testid="nav-toggle"
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? t("nav.expand") : t("nav.collapse")}
          aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
          aria-expanded={!collapsed}
          style={{
            border: `1px solid ${color.cardBorder}`, background: "#fff", color: color.sec,
            borderRadius: 6, width: 26, height: 24, fontSize: 12, cursor: "pointer", padding: 0,
          }}
        >
          {collapsed ? "»" : "«"}
        </button>
      </div>
      {groups.map(({ group, items }) => (
        <div key={group}>
          {/* A group heading is a label; collapsed there is no room for one, and a hairline says
              "these belong together" without pretending to be text. */}
          {collapsed
            ? <div style={{ height: 1, background: color.cardBorder, margin: "8px 12px" }} />
            : (
              <div style={{ padding: "12px 16px 5px" }}>
                <span style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".7px", color: color.muted2 }}>
                  {t(`group.${group}`)}
                </span>
              </div>
            )}
          {items.map((id) => {
            const s = SCREENS[id];
            const active = s.id === activeId;
            return (
              <div
                key={id}
                onClick={() => nav(s.path)}
                title={t(`nav.${s.id}`)}
                style={{
                  position: "relative",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: collapsed ? "center" : "flex-start",
                  gap: 10,
                  padding: collapsed ? "9px 0" : "8px 16px 8px 15px",
                  cursor: "pointer",
                  background: active ? color.indigoTint : "transparent",
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    left: 0,
                    top: 6,
                    bottom: 6,
                    width: 3,
                    borderRadius: "0 3px 3px 0",
                    background: active ? color.indigo : "transparent",
                  }}
                />
                <span style={{ width: 17, textAlign: "center", fontSize: 13, color: active ? color.indigo : color.sec }}>
                  {s.icon}
                </span>
                {!collapsed && (
                  <span style={{ fontSize: 12.5, fontWeight: active ? 600 : 500, color: active ? color.indigo : color.sec, flex: 1 }}>
                    {t(`nav.${s.id}`)}
                  </span>
                )}
                {/* Collapsed, the badge becomes a dot: the COUNT needs the width the label gave up,
                    but "there is something here" is the part that must survive, or a review queue
                    with items in it looks empty from the rail. */}
                {s.badge && collapsed && (
                  <span
                    style={{
                      position: "absolute", top: 6, right: 8, width: 7, height: 7, borderRadius: 4,
                      background: s.badge.tone === "review" ? color.redFg : color.amberFg,
                    }}
                  />
                )}
                {s.badge && !collapsed && (
                  <span
                    style={{
                      minWidth: 18,
                      height: 18,
                      padding: "0 5px",
                      borderRadius: 9,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 10,
                      fontWeight: 600,
                      background: s.badge.tone === "review" ? color.redBg : color.amberBg,
                      color: s.badge.tone === "review" ? color.redFg : color.amberFg,
                    }}
                  >
                    {s.badge.count}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      ))}

      {/* Rendered only when there IS a figure to print, and only when the figure belongs to the
          extraction the label names. A card whose title says "Extraction progress" and whose body
          is empty reads as a load that failed; a card whose body is another project's counts is
          worse. So the whole card is absent while a real document is active, and absent before the
          sample project's counts have arrived. */}
      {prog && !collapsed && (
        <div
          data-testid="nav-progress"
          style={{
            margin: "14px 16px 0",
            padding: "11px 12px",
            border: `1px solid ${color.cardBorder}`,
            borderRadius: 9,
            background: color.rowAltBg,
          }}
        >
          {/* No percentage and no bar. Both rendered `progress.pct`, which was a literal 72 in the
              sample payload derived from nothing, and read `?? 0` — so once the server stopped
              serving a figure it could not compute, this card would have drawn an empty bar and
              announced "0%" over a project with 33 mapped line items. The two counts below are the
              server's own, over the data it serves, and they are all this card can honestly say.
              The labels are translated: they were hardcoded English inside a localized shell. */}
          <div style={{ fontSize: 11, color: color.sec2, marginBottom: 6 }}>{t("progress.title")}</div>
          <div style={{ fontSize: 10.5, color: color.muted2 }}>
            {`${prog.line_items} ${t("progress.lineItems")} · `
             + `${prog.in_review} ${t("progress.inReview")}`}
          </div>
        </div>
      )}
    </div>
  );
}
