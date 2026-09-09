/** Screen/route registry — the single source used by both the nav rail and the top
 * pipeline stepper, so they always navigate to the same 8 destinations. */
export interface ScreenDef {
  id: string;
  path: string;
  label: string;
  icon: string; // unicode glyph, per the handoff
  step?: number; // position in the pipeline stepper (1..6), if part of it
  badge?: { count: number; tone: "review" | "warn" };
}

export const SCREENS: Record<string, ScreenDef> = {
  upload: { id: "upload", path: "/upload", label: "Documents & Template", icon: "▤", step: 1 },
  integrity: { id: "integrity", path: "/integrity", label: "Document Integrity", icon: "◎", step: 2 },
  scope: { id: "scope", path: "/scope", label: "Page Scope", icon: "▦", step: 3 },
  // Extraction is the step, and the Workspace is what you read afterwards. This entry is what
  // makes the extraction findable at all: the screen existed at /documents/:id with no registry
  // entry, so it was in no nav group, held no stepper slot, and `screenIdForPath` answered
  // "workspace" for it — the rail highlighted the wrong row the whole time you were on it.
  extraction: { id: "extraction", path: "/extraction", label: "Extraction", icon: "◈", step: 4 },
  workspace: { id: "workspace", path: "/workspace", label: "Workspace", icon: "▤" },
  notes: { id: "notes", path: "/notes", label: "All Notes", icon: "☰" },
  review: { id: "review", path: "/review", label: "Review Queue", icon: "✓", step: 5 },
  commentary: { id: "commentary", path: "/commentary", label: "Analysis", icon: "✦" },
  // The qualitative scan and the figures computed for it. Its own destination because it used to
  // live only as a card at the BOTTOM of Extraction, below the rows grid and the source panel —
  // a long scroll past what most readers came for, and not where its name says it is.
  disclosures: { id: "disclosures", path: "/disclosures", label: "Disclosures", icon: "❑" },
  // The output shape: which lines the deliverable has, and the per-line alias/sign editor over
  // them. NO LONGER IN THE NAV — see NAV_GROUPS below. It is reached from Line Items, which is the
  // single configuration destination.
  //
  // The label was "Template & Line Items", which is how this came to read as a SECOND master: it
  // claimed both jobs while sitting in the rail directly above an entry actually called "Line
  // Items". One configuration engine has to mean one place to configure it, so the label now names
  // only what this screen is.
  template: { id: "template", path: "/template", label: "Template", icon: "◆" },
  // THE configuration destination. The line items themselves, what each is assembled from, and the
  // gate that decides where each may be claimed.
  line_items: { id: "line_items", path: "/line-items", label: "Line Items", icon: "≡" },
  settings: { id: "settings", path: "/settings", label: "Settings", icon: "⚙" },
  // The deployment-wide run trail. Not a pipeline step and not per-document: it is admin oversight,
  // which is why it sits in CONFIGURE beside Settings rather than in ANALYSIS beside the
  // per-document trail it resembles.
  audit: { id: "audit", path: "/audit", label: "Run Trail", icon: "⧖" },
  export: { id: "export", path: "/export", label: "Export", icon: "⬎", step: 6 },
};

/** Pipeline stepper, in order. */
export const STEPPER: { id: string; label: string; step: number; path: string }[] = [
  { id: "upload", label: "Upload", step: 1, path: "/upload" },
  { id: "integrity", label: "Integrity", step: 2, path: "/integrity" },
  { id: "scope", label: "Scope", step: 3, path: "/scope" },
  // Step 4 is the extraction itself, so the stepper points at the screen that runs it and reports
  // its progress. It used to point at /workspace and be labelled "Extract", which is how the
  // extraction came to be read as a Workspace tab.
  { id: "extraction", label: "Extract", step: 4, path: "/extraction" },
  { id: "review", label: "Review", step: 5, path: "/review" },
  { id: "export", label: "Export", step: 6, path: "/export" },
];

/** Left-nav groups. */
export const NAV_GROUPS: { group: string; items: string[] }[] = [
  { group: "SETUP", items: ["upload"] },
  { group: "PRE-FLIGHT", items: ["integrity", "scope"] },
  { group: "EXTRACT", items: ["extraction", "workspace", "notes"] },
  { group: "QUALITY", items: ["review"] },
  { group: "ANALYSIS", items: ["commentary", "disclosures"] },
  // ONE CONFIGURATION DESTINATION. `template` used to sit here beside `line_items`, so the rail
  // offered two configuration masters — and the first was labelled "Template & Line Items", which
  // made the pair read as the same thing listed twice. Line items is the single configuration
  // engine, so it gets the single entry; the template's output shape and its per-line editor are
  // reached FROM it (see the Line Items screen header) rather than from a rail entry of their own.
  { group: "CONFIGURE", items: ["line_items", "settings", "audit"] },
  { group: "DELIVER", items: ["export"] },
];

/** Map a pathname to the active screen id. */
export function screenIdForPath(pathname: string): string {
  const found = Object.values(SCREENS).find((s) => pathname.startsWith(s.path));
  return found?.id ?? "workspace";
}

/** For a screen that is not itself a step, the step it sits at.
 *
 * The stepper answers "how far through the run am I", which is not the same question the nav rail
 * answers. Screens like the Workspace and All Notes are what you read AFTER the extraction, so they
 * sit AT the Extract step — and when the Workspace stopped being step 4, nothing claimed its place
 * and `curIdx` went to -1, which blanked every step in the bar for the screen an analyst spends most
 * of their time on. A screen outside the flow entirely (Template, Settings) maps to nothing and
 * correctly leaves the bar unmarked.
 */
const STEP_FOR_SCREEN: Record<string, string> = {
  workspace: "extraction",
  notes: "extraction",
  commentary: "review",
  disclosures: "review",
};

/** The stepper id for a pathname — the screen's own step, or the step it sits at. */
export function stepIdForPath(pathname: string): string {
  const id = screenIdForPath(pathname);
  return STEP_FOR_SCREEN[id] ?? id;
}
