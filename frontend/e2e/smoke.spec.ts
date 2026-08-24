import { test, expect, Page } from "@playwright/test";

const DCL = { waitUntil: "domcontentloaded" as const };

/** Log in via the demo quick-sign-in buttons (passwordless in demo mode). */
async function loginAs(page: Page, role: "admin" | "reviewer" | "analyst") {
  await page.goto("/", DCL);
  await page.getByRole("button", { name: new RegExp(role, "i") }).click();
  await expect(page.getByText(/FinExtract/)).toBeVisible();
}

/** End the session this browser holds, so `loginAs` reaches the sign-in screen again.
 *
 *  Needed only by a test that has to act as TWO roles in one page. Each test gets a fresh context, so
 *  the first `loginAs` in a test always finds the sign-in buttons; a second one does not, because "/"
 *  redirects a signed-in browser to /workspace and the buttons are simply not on the screen. The
 *  session lives in localStorage, so clearing it is what a fresh context would have done — and it
 *  also drops `finex-active-doc`, which the next role has no business inheriting. */
async function signOut(page: Page) {
  await page.evaluate(() => localStorage.clear());
}

/** Put the sample project into a known state. Whether it is loaded is a PERSISTED admin
 *  setting, so it carries across a restart and across suite runs — a test that needs it on or
 *  off has to say so rather than inherit whatever the last run left behind. */
async function setSampleLoaded(page: Page, want: boolean) {
  await page.goto("/settings", DCL);
  const row = page.getByTestId("seed-demo");
  await expect(row).toBeVisible({ timeout: 15_000 });
  if ((await row.getAttribute("data-on")) !== String(want)) {
    await row.click();
    await expect(row).toHaveAttribute("data-on", String(want), { timeout: 15_000 });
  }
}

/** Put the extraction thresholds back to what config.toml shipped.
 *
 * These are PERSISTED, so a test that changes one and walks away hands its override to the next
 * run — which is how the suite started failing on its second consecutive run. Any test touching
 * them restores them, and any test asserting "back to the default" establishes that baseline
 * first rather than trusting the value it happens to find. */
async function resetThresholds(page: Page) {
  await page.goto("/settings", DCL);
  const reset = page.getByTestId("ex-reset");
  await expect(reset).toBeVisible({ timeout: 15_000 });
  await reset.click();
  await expect(page.getByTestId("ex-save")).toBeDisabled({ timeout: 15_000 });
}

/** Open one template's detail page.
 *
 * Template & Ontology is two pages: an index of the templates that exist, and the structure tree
 * plus the ontology editors on a detail raised over it. Anything that drives an editor has to
 * click into a row first — and specifically into a row that HAS a rulebook, since a template no
 * ontology targets yet is legitimately read-only. */
async function openTemplateDetail(page: Page) {
  await page.goto("/template", DCL);
  const rows = page.getByTestId("tpl-row").filter({ hasNotText: "None yet" });
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  await rows.first().click();
  await expect(page.getByTestId("template-detail")).toBeVisible({ timeout: 15_000 });
}

/** Can a row of the index take keyboard focus?
 *
 * While the detail overlay is up it must not: the list stays MOUNTED under it (that is what keeps
 * the filter and the scroll), which left every row in the focus order behind a dirty editor.
 * Asserting the `inert` attribute would only prove it was spelled, so this asks the browser
 * instead — it tries to focus a row and reports whether the focus took. */
async function indexRowFocusable(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const row = document.querySelector('[data-testid="tpl-row"]') as HTMLElement | null;
    if (!row) return false;
    (document.activeElement as HTMLElement | null)?.blur();
    row.focus();
    return document.activeElement === row;
  });
}

/** The review check types whose findings are about ONE EXTRACTED ROW.
 *
 *  Two things turn on the distinction, which is why it is spelled once rather than twice. These are
 *  the types that carry a `remap` offer — the fix for "this row is on the wrong line" — while an
 *  accounting finding, being a relation between several concepts, carries none and gives no answer to
 *  WHICH concept to move. And these are the types with a review chip of their own: the tabs PARTITION
 *  the queue (documents.py::_build_review), so everything else is counted by the accounting chip,
 *  which is how the fixture near the end of this file finds that chip without naming it. */
const ROW_SHAPED_TYPES = new Set(["unmapped", "low_confidence", "off_template"]);

test.describe.configure({ mode: "serial" });

test("greenfield: the app is empty before any project is loaded", async ({ page }) => {
  await loginAs(page, "admin");
  // Establish the precondition instead of assuming it: "no project loaded" is now durable
  // state, so a previous run having loaded the sample must not decide this test's outcome.
  await setSampleLoaded(page, false);
  await page.goto("/workspace", DCL);
  await expect(page.getByRole("heading", { name: "No project yet" })).toBeVisible();
  await expect(page.getByText("Trade receivables")).toHaveCount(0);
});

test("admin can load the sample project and the workspace populates", async ({ page }) => {
  await loginAs(page, "admin");
  // "Not loaded" is the precondition of LOADING it, and "Load sample project" is a TOGGLE's title,
  // not a button: against an already-loaded project this test would UNLOAD the sample and then
  // assert that the workspace populates. It passed only because the test above happens to leave the
  // flag off — an inherited precondition, on the one setting the file's own helper warns is
  // persisted. Established here instead.
  await setSampleLoaded(page, false);
  const row = page.getByTestId("seed-demo");
  await row.click();
  // Wait for the SETTING, not for a stopwatch. This was `waitForTimeout(600)` — a guess about how
  // long PATCH /settings plus the refetch takes on whatever machine the suite runs on, which is
  // both slower than it needs to be and, on a loaded machine, not long enough. `data-on` flips off
  // the refetched settings, so it flipping IS the server having stored the change.
  await expect(row).toHaveAttribute("data-on", "true", { timeout: 15_000 });
  await page.goto("/workspace", DCL);
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "No project yet" })).toHaveCount(0);
});

test("note references are hyperlinks to the All Notes screen", async ({ page }) => {
  await loginAs(page, "admin");
  // The note chips under test are the SAMPLE project's — no document is uploaded at this point — so
  // the sample being loaded is this test's precondition and not the previous test's leftover.
  await setSampleLoaded(page, true);
  await page.goto("/workspace", DCL);
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/notes/);
});

test("analyst uploads a document and views its extracted data with provenance", async ({ page }) => {
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  // The dropzone's hidden file input is real (browse is clickable).
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "sample.pdf" })).toBeVisible({ timeout: 15_000 });

  // Skip the integrity review and go straight to extraction for the just-uploaded document
  // (auto mode → the extraction view). The per-row "View" affordance was removed.
  await page.getByRole("button", { name: /Extract directly/ }).click();
  await expect(page).toHaveURL(/\/extraction/);
  await expect(page.getByRole("heading", { name: "Extracted data" })).toBeVisible({ timeout: 15_000 });
  // Real extracted line item + click-to-source provenance from the native PDF.
  await expect(page.getByText("Trade receivables").first()).toBeVisible();
  // Click the value's source chip → the page renders with its bbox highlighted.
  await page.getByText(/^p\.1/).first().click();
  await expect(page.getByTestId("prov-highlight")).toBeVisible({ timeout: 15_000 });
});

test("analyst uploads a spreadsheet and gets Excel cell-level click-to-source", async ({ page }) => {
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.xlsx");
  await expect(page.getByTestId("doc-row").filter({ hasText: "sample.xlsx" })).toBeVisible({ timeout: 15_000 });

  await page.getByRole("button", { name: /Extract directly/ }).click();
  await expect(page).toHaveURL(/\/extraction/);
  await expect(page.getByRole("heading", { name: "Extracted data" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Trade receivables").first()).toBeVisible();

  // Click a Sheet!Cell chip → the surrounding cells render with the target highlighted.
  await page.getByText(/!B\d/).first().click();
  await expect(page.getByTestId("cell-target")).toBeVisible({ timeout: 15_000 });
});

test("end-to-end: upload a new file → Run integrity check shows real results → extract", async ({ page }) => {
  test.setTimeout(180_000);   // full real path: upload → integrity → extract → scope → workspace → review → export
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);

  // Upload a brand-new file.
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "sample.pdf" })).toBeVisible({ timeout: 15_000 });

  // Hit "Run integrity check" → the Document Integrity screen must render THIS file's real
  // pre-flight results, not a blank/empty page.
  await page.getByRole("button", { name: /Run integrity check/ }).click();
  await expect(page).toHaveURL(/\/integrity/);
  await expect(page.getByRole("heading", { name: "Document integrity" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Pages", { exact: true })).toBeVisible();        // real stat, not EmptyState
  await expect(page.getByRole("heading", { name: "No project yet" })).toHaveCount(0);

  // Continue into the real extraction for the uploaded document.
  await page.getByRole("button", { name: /Extract now/ }).click();
  await expect(page).toHaveURL(/\/extraction/);
  await expect(page.getByRole("heading", { name: "Extracted data" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Trade receivables").first()).toBeVisible();
  // Derived analysis (ratios / disclosures / notes) renders from the extraction.
  await expect(page.getByText("Ratios (computed)")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Disclosures")).toBeVisible();

  // Page Scope reads the REAL document's classified pages.
  await page.goto("/scope", DCL);
  await expect(page.getByRole("heading", { name: "Statement page detection" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("All pages")).toBeVisible();
  await expect(page.getByRole("heading", { name: "No project yet" })).toHaveCount(0);
  // Clicking a page opens the side-by-side rendered preview (real PDF page image).
  await page.getByTestId("scope-page").first().click();
  await expect(page.getByText(/Page preview/)).toBeVisible({ timeout: 15_000 });

  // Workspace renders the REAL extracted statement (its own line items, real filename).
  await page.goto("/workspace", DCL);
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Reliance Industries Ltd")).toHaveCount(0);         // no demo leakage
  // The left pane is the LIVE document viewer with click-to-source: selecting a row
  // scrolls the real page in and highlights the value's bounding box.
  await page.getByText("Trade receivables").first().click();
  await expect(page.getByTestId("prov-highlight")).toBeVisible({ timeout: 15_000 });

  // All Notes reads the REAL document's note references (make_native_pdf cites Note 15).
  await page.goto("/notes", DCL);
  await expect(page.getByText("N15").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "No project yet" })).toHaveCount(0);

  // Review runs on the REAL document (its own queue), not the demo project.
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "No project yet" })).toHaveCount(0);

  // Export previews the REAL extracted rows (real data, not the demo Reliance sample).
  await page.goto("/export", DCL);
  await expect(page.getByRole("heading", { name: "Export" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("3410").first()).toBeVisible({ timeout: 15_000 });   // real extracted value
  await expect(page.getByText("4,23,180")).toHaveCount(0);                          // demo preview value absent
  await expect(page.getByText("Reliance Industries Ltd")).toHaveCount(0);           // demo chrome absent in real run
});

test("analyst cannot reach the config template screen but can select a template", async ({ page }) => {
  await loginAs(page, "analyst");

  // Template & Ontology is an admin-only configuration screen: it must NOT appear in the
  // analyst's nav, and a direct visit is redirected away (to the analyst's first screen).
  //
  // The rail is COLLAPSED by default, which hides every label — so it is expanded first. Asserting
  // the absence of a label against a rail that shows none of them would pass whatever the role may
  // see, which is the assertion quietly meaning nothing.
  await page.goto("/workspace", DCL);
  const rail = page.getByTestId("nav-rail");
  if ((await rail.getAttribute("data-collapsed")) === "1") {
    await page.getByTestId("nav-toggle").click();
  }
  await expect(rail).toHaveAttribute("data-collapsed", "0");
  await expect(page.getByText("Workspace").first()).toBeVisible();  // labels are showing…
  await expect(page.getByText("Template & Ontology")).toHaveCount(0); // …and this one is not there
  await page.goto("/template", DCL);
  await expect(page).not.toHaveURL(/\/template/);

  // But the analyst still SELECTS a template on the Documents & Template (Upload) screen.
  await page.goto("/upload", DCL);
  await expect(page.getByText("Selected")).toBeVisible({ timeout: 15_000 }); // a template is active

  // WHICH VERSION IS ACTIVE BY DEFAULT: the latest, which is what the server flags. This test used
  // to click a row and assert only that the picker CLOSED — so it passed throughout the period when
  // the screen defaulted to v1 and no click could change the selection, because neither claim was
  // ever checked. `find(x => x.template_key === key)` over a list of every version answered the
  // oldest row, and every option stored that same key.
  const served = await apiGet<{ id: string; template_key: string; version: number;
                               is_latest: boolean }[]>(page, "/api/v1/templates");
  const active = page.getByTestId("tpl-active-meta");
  const latest = served.filter((x) => x.is_latest);
  expect(latest.length, "no template is flagged latest").toBeGreaterThan(0);
  const shown = (await active.innerText()).trim();
  const [shownKey] = shown.split(" · ");
  const latestForShown = latest.find((x) => x.template_key === shownKey);
  expect(latestForShown, `nothing flagged latest for ${shownKey}`).toBeTruthy();
  expect(shown).toBe(`${latestForShown!.template_key} · v${latestForShown!.version}`);

  // …AND A DIFFERENT VERSION CAN ACTUALLY BE CHOSEN. Only meaningful where a second version of the
  // shown template exists; earlier tests publish one, and this says so rather than passing silently.
  const siblings = served.filter((x) => x.template_key === shownKey)
                         .sort((a, b) => b.version - a.version);
  await page.getByRole("button", { name: "Choose another" }).click();
  const options = page.getByTestId("tpl-option");
  await expect(options.first()).toBeVisible({ timeout: 15_000 });
  if (siblings.length > 1) {
    const older = siblings[1];
    await options.filter({ hasText: `v${older.version}` }).first().click();
    await expect(page.getByRole("button", { name: "Choose another" })).toBeVisible();
    // The selection MOVED — the assertion the old test was missing.
    await expect(active).toHaveText(`${older.template_key} · v${older.version}`);
  } else {
    expect(siblings.length, "only one version stored, so version selection is untested here").toBe(1);
    await options.first().click();
    await expect(page.getByRole("button", { name: "Choose another" })).toBeVisible();
  }
});

test("the template screen is an index first: a row opens the detail, which dismisses back",
     async ({ page }) => {
  await loginAs(page, "admin");
  await page.goto("/template", DCL);

  // PAGE 1 is a list of templates — the facts you pick between, not the structure of one of
  // them. The tree and the editors must not be on this page at all.
  const rows = page.getByTestId("tpl-row");
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  await expect(rows.first()).toContainText(/hkfrs/i);          // the template's name/key
  // Published state is one of the columns. WHICH state the first row is in depends on history —
  // a version published from an edited workbook is stored unpublished, and after any earlier run
  // of this suite that draft is the newest version — so assert on the row that must be published,
  // the seeded template, rather than on whichever version happens to sort first.
  await expect(rows.filter({ hasText: "Published" }).first()).toBeVisible();
  await expect(page.getByTestId("tpl-node")).toHaveCount(0);
  await expect(page.getByTestId("template-detail")).toHaveCount(0);

  // Filter the list. This is the list's own state — dismissing the detail must come back to the
  // list as it was, not to a freshly mounted one.
  await page.getByTestId("tpl-filter").fill("hkfrs");
  await expect(rows.first()).toBeVisible();

  // Rows are focusable on the index itself — that is how the list is keyboard-operable.
  expect(await indexRowFocusable(page)).toBe(true);

  // PAGE 2 opens on the row click, carrying the tree and the concept editor.
  await rows.first().click();
  await expect(page.getByTestId("template-detail")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("tpl-node").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByPlaceholder("New alias")).toBeVisible();
  await expect(page).toHaveURL(/[?&]template=/);              // reloadable / linkable

  // The tree says WHOSE structure it is. With several versions of several templates in the index,
  // the detail is the only thing on screen that can answer that, and the sidebar is where a reader
  // scrolling the tree is looking.
  await expect(page.getByTestId("tpl-tree-template")).toContainText(/hkfrs/i);

  // …and the list underneath is out of reach while it is covered: Tab must not walk out of the
  // editor into a row that would swap the subject being edited.
  expect(await indexRowFocusable(page)).toBe(false);

  // The starter ontology for THIS template is downloadable from its detail — it is derived from
  // one template, so it lives where a template is already chosen. Both halves of this feature
  // existed for a while with no UI at all and nothing noticed, because everything was exported
  // and tsc stayed quiet; these two assertions are what would have caught that.
  const [starter] = await Promise.all([
    page.waitForEvent("download", { timeout: 30_000 }),
    page.getByTestId("tpl-skeleton-download").click(),
  ]);
  expect(starter.suggestedFilename()).toMatch(/_ontology_skeleton\.json$/);

  // Dismissed → back on the index, still filtered, and reachable again.
  await page.getByTestId("tpl-detail-close").click();
  await expect(page.getByTestId("template-detail")).toHaveCount(0);
  await expect(page.getByTestId("tpl-filter")).toHaveValue("hkfrs");
  await expect(rows.first()).toBeVisible();
  await expect(page).not.toHaveURL(/[?&]template=/);
  expect(await indexRowFocusable(page)).toBe(true);

  // And the SHAPE any ontology must have is downloadable from the index, because it constrains
  // every one of them rather than any single template.
  const [schema] = await Promise.all([
    page.waitForEvent("download", { timeout: 30_000 }),
    page.getByTestId("tpl-schema-download").click(),
  ]);
  expect(schema.suggestedFilename()).toMatch(/^ontology_schema_v\d+\.json$/);
  await expect(page.getByTestId("tpl-schema-error")).toHaveCount(0);
});

test("admin edits the ontology inline and the new version persists", async ({ page }) => {
  await loginAs(page, "admin");
  await openTemplateDetail(page);

  // The real configured template renders its tree; pick the first editable concept.
  const nodes = page.getByTestId("tpl-node");
  await expect(nodes.first()).toBeVisible({ timeout: 15_000 });
  await nodes.first().click();

  // Add an alias: type into the alias input and press Enter → it appears as a chip, and the
  // unsaved-changes bar shows up (edits are real local state, not decoration).
  const alias = `E2E alias ${Date.now()}`;
  const input = page.getByPlaceholder("New alias");
  await expect(input).toBeVisible();
  await input.fill(alias);
  await input.press("Enter");
  await expect(page.getByText(alias)).toBeVisible();
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  // Save → the server publishes a NEW ontology version and reports it back.
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText(/Saved as v\d+/)).toBeVisible({ timeout: 15_000 });

  // The edit survives a full reload (it is stored, not just in local state).
  await page.reload(DCL);
  await expect(nodes.first()).toBeVisible({ timeout: 15_000 });
  await nodes.first().click();
  await expect(page.getByText(alias)).toBeVisible({ timeout: 15_000 });
});

test("admin edits the mapping CRITERIA and the new version persists", async ({ page }) => {
  await loginAs(page, "admin");
  await openTemplateDetail(page);

  const nodes = page.getByTestId("tpl-node");
  await expect(nodes.first()).toBeVisible({ timeout: 15_000 });
  await nodes.first().click();

  // Criteria — not aliases — are what let an unfamiliar caption be mapped by MEANING, so they
  // have to be editable: add an inclusion criterion the same way an alias is added.
  const criterion = `E2E includes ${Date.now()}`;
  const include = page.getByTestId("criteria-include").getByRole("textbox");
  await expect(include).toBeVisible();
  await include.fill(criterion);
  await include.press("Enter");
  await expect(page.getByText(criterion)).toBeVisible();
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  // The value-scope choice is a real select over the four scopes the backend accepts.
  await expect(page.getByTestId("criteria-scope")).toBeVisible();

  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText(/Saved as v\d+/)).toBeVisible({ timeout: 15_000 });

  // Stored, not just local: it comes back after a full reload.
  await page.reload(DCL);
  await expect(nodes.first()).toBeVisible({ timeout: 15_000 });
  await nodes.first().click();
  await expect(page.getByText(criterion)).toBeVisible({ timeout: 15_000 });
});

test("admin adds a netting rule and it persists; unknown keys are impossible", async ({ page }) => {
  await loginAs(page, "admin");
  await openTemplateDetail(page);
  await expect(page.getByTestId("tpl-node").first()).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("netting-add").click();
  const target = page.getByTestId("netting-target");
  await expect(target).toBeVisible();
  // Keys are PICKED from the concepts that exist — there is no free-text key field to typo.
  const options = target.locator("option");
  expect(await options.count()).toBeGreaterThan(1);
  await target.selectOption({ index: 1 });

  // The draft rule is rendered last; fill and save it there (existing rules have the same
  // fields, so the locator has to be scoped to the row).
  const draft = page.getByTestId("netting-rule").last();
  const label = `E2E netting ${Date.now()}`;
  await draft.getByTestId("netting-label").fill(label);
  await draft.getByTestId("netting-save").click();
  await expect(page.getByText(/Saved as v\d+/)).toBeVisible({ timeout: 15_000 });

  // Stored: after a reload the rule comes back (the server appends it, so it is the last row)
  // with the explanation we typed.
  await page.reload(DCL);
  await expect(page.getByTestId("tpl-node").first()).toBeVisible({ timeout: 15_000 });
  const rows = page.getByTestId("netting-rule");
  const saved = rows.last();
  await expect(saved.getByTestId("netting-label")).toHaveValue(label, { timeout: 15_000 });
  const before = await rows.count();

  // Clean up after ourselves so the rule doesn't leak into later runs (delete is two-step).
  await saved.getByTestId("netting-delete").click();
  await saved.getByTestId("netting-delete").click();
  await expect(page.getByText(/Saved as v\d+/)).toBeVisible({ timeout: 15_000 });
  await expect(rows).toHaveCount(before - 1, { timeout: 15_000 });
});

test("an analyst gets no ontology editing affordances at all", async ({ page }) => {
  await loginAs(page, "analyst");

  // The authoring screen is admin-only, so the analyst is redirected away from it — and none
  // of the criteria / netting edit controls exist anywhere in their app.
  await page.goto("/template", DCL);
  await expect(page).not.toHaveURL(/\/template/);
  await expect(page.getByTestId("criteria-include")).toHaveCount(0);
  await expect(page.getByTestId("criteria-scope")).toHaveCount(0);
  await expect(page.getByTestId("netting-add")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save rule" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0);
});

test("admin tunes the extraction thresholds and they persist", async ({ page }) => {
  await loginAs(page, "admin");
  // The shipped configuration is the baseline this test compares against, so start from it
  // rather than from whatever a previous run left saved.
  await resetThresholds(page);

  // Every control is rendered from the backend's field descriptors, so the knob is present
  // without the screen knowing anything about mapping.
  //
  // `ex-evidence_floor`, not `ex-fuzzy_accept`: the string-similarity tier was deleted and its
  // knob went with it, while this spec kept naming the old testid. That is not a flake — it is a
  // hard failure, and in a serial suite it stopped the 33 tests after it from running at all,
  // which is what made the suite look non-deterministic from the outside.
  const floor = page.getByTestId("ex-evidence_floor");
  await expect(floor).toBeVisible({ timeout: 15_000 });
  const shipped = await floor.inputValue();

  // Save is inert until something actually changes.
  await expect(page.getByTestId("ex-save")).toBeDisabled();

  await floor.fill("0.62");
  await expect(page.getByTestId("ex-save")).toBeEnabled();
  await page.getByTestId("ex-save").click();

  // It round-trips: a reload reads the value back from the server, so the pipeline really
  // holds it — not just this form.
  await page.reload(DCL);
  await expect(page.getByTestId("ex-evidence_floor")).toHaveValue("0.62", { timeout: 15_000 });

  // Out of range is refused before it can be sent, and says why.
  await page.getByTestId("ex-evidence_floor").fill("1.4");
  await expect(page.getByTestId("ex-save")).toBeDisabled();
  await expect(page.getByTestId("ex-message")).toContainText("at most 1");

  // Restore defaults puts the shipped configuration back — and leaves nothing behind for the
  // next run to inherit.
  await page.getByTestId("ex-reset").click();
  await expect(page.getByTestId("ex-evidence_floor")).toHaveValue(shipped, { timeout: 15_000 });
});

test("a saved threshold is still in force for a browser that never saw the edit", async ({
  page, browser,
}) => {
  // Four full page loads (two logins, two Settings) against the vite dev server, whose module
  // graph is fetched unbundled: one `domcontentloaded` navigation measures ~13 s here, so the 60 s
  // default was never a budget this test could hold — it failed on the clock, mid-cleanup, with
  // every assertion still passing. Nothing is relaxed: each assertion keeps its own 15 s.
  test.setTimeout(120_000);
  // The reason these are persisted: an admin's change must not evaporate. A second browser
  // context shares nothing with the first except the server, so reading the saved value back
  // there is what proves it left the first tab.
  await loginAs(page, "admin");
  await page.goto("/settings", DCL);
  await expect(page.getByTestId("ex-mapping_margin")).toBeVisible({ timeout: 15_000 });
  const original = await page.getByTestId("ex-mapping_margin").inputValue();

  await page.getByTestId("ex-mapping_margin").fill("0.17");
  await page.getByTestId("ex-save").click();
  await expect(page.getByTestId("ex-save")).toBeDisabled({ timeout: 15_000 });

  const fresh = await browser.newContext();
  const other = await fresh.newPage();
  try {
    await loginAs(other, "admin");
    await other.goto("/settings", DCL);
    await expect(other.getByTestId("ex-mapping_margin")).toHaveValue("0.17", { timeout: 15_000 });
  } finally {
    await page.getByTestId("ex-reset").click();
    await expect(page.getByTestId("ex-mapping_margin")).toHaveValue(original, { timeout: 15_000 });
    await fresh.close();
  }
});

test("an analyst cannot reach the extraction thresholds at all", async ({ page }) => {
  // Settings is an admin-only SCREEN (see SCREENS_BY_ROLE): these knobs change how every
  // future extraction behaves, so an analyst gets neither the controls nor the screen.
  await loginAs(page, "analyst");
  await page.goto("/settings", DCL);

  // Named against a knob that EXISTS for an admin. Asserting the absence of a control nothing
  // renders any more passes whatever the role gate does, which is a test that cannot fail.
  await expect(page.getByTestId("ex-evidence_floor")).toHaveCount(0);
  await expect(page.getByTestId("ex-save")).toHaveCount(0);
  await expect(page.getByText("Alias evidence floor")).toHaveCount(0);
});

test("the thresholds are still editable against a backend that omits the descriptors", async ({
  page,
}) => {
  // An older API returns the extraction VALUES without the field descriptions. Rendering from an
  // empty descriptor list produced a card with a Save button and no fields in it — visibly
  // broken, and silent about why. The controls are inferred from the values instead, and the
  // card says the ranges are not being checked locally.
  await page.route("**/api/v1/settings", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const body = await res.json();
    delete body.extraction_fields;
    delete body.extraction_defaults;
    await route.fulfill({ response: res, json: body });
  });

  await loginAs(page, "admin");
  await page.goto("/settings", DCL);

  // Inferred from the value's own type: a number input for a threshold…
  const floor = page.getByTestId("ex-evidence_floor");
  await expect(floor).toBeVisible({ timeout: 15_000 });
  await expect(floor).toHaveAttribute("type", "number");
  // …a toggle for the boolean, and a readable label rather than the raw key.
  await expect(page.getByTestId("ex-llm_mapping")).toBeVisible();
  await expect(page.getByText("Evidence floor")).toBeVisible();
  // And it is honest about the degraded mode.
  await expect(page.getByText(/did not describe these settings/i)).toBeVisible();

  // Still genuinely editable: the save round-trips through the real endpoint.
  await floor.fill("0.58");
  await page.getByTestId("ex-save").click();
  await expect(page.getByTestId("ex-save")).toBeDisabled({ timeout: 15_000 });

  // Hand nothing to the next run: the save above is persisted.
  await page.unroute("**/api/v1/settings");
  await resetThresholds(page);
});

test("real extraction: prior-year links, an edit that sticks, KPIs and Additional items",
     async ({ page }) => {
  test.setTimeout(240_000);
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  // A two-column comparative, including a line printed for the PRIOR YEAR ONLY.
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/comparative.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "comparative.pdf" }))
    .toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Extract directly/ }).click();
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 30_000 });

  await page.goto("/workspace", DCL);
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 20_000 });

  // --- last year's figure is its own hyperlink ---------------------------------------------
  // The title only exists when THAT period's value resolves to a source location, so clicking
  // it has to drive the viewer to the page it was printed on.
  const priorLink = page.locator('[title*="last year"]').first();
  await expect(priorLink).toBeVisible({ timeout: 15_000 });
  await priorLink.click();
  await expect(page.getByTestId("prov-highlight")).toBeVisible({ timeout: 15_000 });

  // --- the inspector describes the selected figure, along the bottom of the panel ------------
  await page.getByText("Trade receivables").first().click();
  const inspector = page.getByTestId("cell-inspector");
  await expect(inspector).toBeVisible({ timeout: 15_000 });
  // It sits BELOW the statement rows rather than beside them.
  const gridBox = await page.getByText("Trade receivables").first().boundingBox();
  const inspBox = await inspector.boundingBox();
  expect(inspBox!.y).toBeGreaterThan(gridBox!.y);

  // --- editing happens in the inspector, and the typed figure is the figure shown ------------
  await page.getByTestId("edit-value").click();
  const input = page.getByTestId("edit-v1");
  await expect(input).toBeVisible({ timeout: 15_000 });
  await input.fill("55555");
  // --- and an edit can say WHY ---------------------------------------------------------------
  await page.getByTestId("edit-comment").fill("Agreed with management");
  await page.getByTestId("edit-save").click();
  // Accepted → the editor closes. A refused save keeps it open and shows why, so this doubles as
  // the assertion that nothing was silently rejected.
  await expect(page.getByTestId("edit-v1")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByTestId("edit-error")).toHaveCount(0);
  await expect(page.getByText("55,555").first()).toBeVisible({ timeout: 15_000 });
  // The reason survives a reload and is shown against the figure it explains.
  await expect(page.getByTestId("inspector-comment")).toContainText("Agreed with management",
                                                                   { timeout: 15_000 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText("Trade receivables").first().click();
  await expect(page.getByTestId("inspector-comment")).toContainText("Agreed with management",
                                                                   { timeout: 15_000 });

  await page.getByRole("button", { name: /Revert/ }).click();
  await expect(page.getByText("55,555")).toHaveCount(0, { timeout: 15_000 });

  // --- a subtotal shows what its components come to ------------------------------------------
  // Total assets is a calculated line: the grid marks it, and the inspector names the arithmetic
  // and lists the lines behind it.
  const calc = page.locator('[data-testid="origin-calculated"]').first();
  await expect(calc).toBeVisible({ timeout: 15_000 });
  await calc.click();
  await expect(page.getByTestId("inspector-origin-calculated")).toBeVisible();
  await expect(page.getByTestId("inspector-arithmetic")).toContainText(/\+|−|-/);

  // The rollup is a RENDERING of the figures, not an expression, so opening the editor must not
  // prefill the formula box with it: the server would evaluate what came back, and a computed
  // result outranks a typed value — the analyst's number would be silently replaced by the sum.
  await page.getByTestId("edit-value").click();
  await expect(page.getByTestId("edit-formula")).toHaveValue("");
  await page.getByRole("button", { name: "Cancel" }).click();

  // --- KPIs -------------------------------------------------------------------------------
  await page.getByTestId("seg-kpi").click();
  await expect(page.getByTestId("seg-kpi")).toHaveAttribute("data-on", "true");
  await expect(page.getByText("Liquidity").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Current ratio").first()).toBeVisible();
  // A ratio has no magnitude, so the presentation controls are absent rather than inert.
  await expect(page.getByText("Units", { exact: true })).toHaveCount(0);
  // Selecting one shows the extracted figures it was computed from.
  await page.getByText("Current ratio").first().click();
  await expect(page.getByText(/numerator:/).first()).toBeVisible({ timeout: 15_000 });

  // --- and back to a statement: the magnitude selector returns, because these ARE amounts -----
  // This used to hop through an "Additional items" tab on the way. That tab is gone front and back
  // (Workspace.tsx's segment list and `_build_statement` both say so): the spread now renders the
  // template's declared lines and nothing else, and a mapped figure the template does not declare is
  // reported as an `off_template` review finding instead of appended to the statement. So the tab's
  // ABSENCE is what is asserted — a returning tab would be the spread quietly gaining rows again —
  // and the claim this block was written for is made directly, KPI → statement.
  await expect(page.getByTestId("seg-additional_items")).toHaveCount(0);
  await page.getByTestId("seg-balance_sheet").click();
  await expect(page.getByTestId("seg-balance_sheet")).toHaveAttribute("data-on", "true");
  await expect(page.getByTestId("seg-kpi")).toHaveAttribute("data-on", "false");
  await expect(page.getByText("Units", { exact: true })).toBeVisible({ timeout: 15_000 });
});

test("an admin downloads the template as a workbook and publishes an edited version",
     async ({ page }) => {
  test.setTimeout(120_000);
  await loginAs(page, "admin");
  await page.goto("/template", DCL);
  await expect(page.getByTestId("template-authoring")).toBeVisible({ timeout: 20_000 });

  // Download the active template's workbook.
  const picker = page.getByTestId("template-picker");
  await expect(picker).toBeVisible();
  const before = await picker.locator("option").allTextContents();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("tpl-download-xlsx").click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.xlsx$/);
  const path = await download.path();
  expect(path).toBeTruthy();

  // Upload it straight back: a NEW version of the same template, nothing overwritten.
  await page.getByTestId("tpl-xlsx-input").setInputFiles(path as string);
  const msg = page.getByTestId("tpl-auth-message");
  await expect(msg).toBeVisible({ timeout: 30_000 });
  await expect(msg).toContainText(/Published .* v\d+ — \d+ line items/);
  const after = await picker.locator("option").allTextContents();
  expect(after.length).toBe(before.length + 1);
});

test("an analyst gets no template authoring affordances", async ({ page }) => {
  await loginAs(page, "analyst");
  await page.goto("/template", DCL);
  // The screen itself is admin-only; whichever way it is refused, the controls must not exist.
  await expect(page.getByTestId("template-authoring")).toHaveCount(0);
  await expect(page.getByTestId("tpl-upload-xlsx")).toHaveCount(0);
});

test("the index names the rulebook the SERVER says is in force, and ranks nothing itself",
     async ({ page }) => {
  // The rulebooks are fixed for this test so the answer cannot depend on how many times earlier
  // tests published a new version.
  //
  // WHAT THIS NOW PROVES. The column used to be computed here, by ranking the served list — first on
  // `version >`, later on [declares a supersession, version, key] — and this test's three rows were
  // shaped to break those rankings. That whole approach was the defect: selection is the server's
  // rule ("the latest stored rulebook wins", `ontology_select.select_for_template`), it turns on
  // `created_at`, and this payload does not carry that field, so no ranking on this side could ever
  // have agreed with the extractor. The two did disagree, and the screen named a rulebook the run had
  // not used.
  //
  // So the flag is the answer and the rows are shaped to punish any attempt to second-guess it:
  // `in_force` sits on the row that carries the LOWEST edit version, declares no supersession, and is
  // itself reported as replaced — the row that every ranking the client has ever applied would have
  // ranked last. If the column prints it, the column is reading the server's answer and nothing else.
  //
  // (A row both in force and superseded is a real state, not a contrivance: an admin republishing a
  // rulebook whose key an older declaration named as replaced makes it the newest thing stored. The
  // server resolves the two — running outranks the label — in extractions.rulebook_record.)
  await page.route("**/api/v1/ontologies", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      json: [
        { id: "o-v1", ontology_key: "hkfrs_hk_china_v1", target_template_key: "hkfrs_hk_china_v1",
          version: 9, schema_version: 1, supersedes: null, superseded: true, in_force: false },
        { id: "o-draft", ontology_key: "hkfrs_hk_china_skeleton",
          target_template_key: "hkfrs_hk_china_v1", version: 4, schema_version: 2,
          supersedes: null, superseded: false, in_force: false },
        { id: "o-v2", ontology_key: "hkfrs_hk_china_v2", target_template_key: "hkfrs_hk_china_v1",
          version: 1, schema_version: 2, supersedes: null, superseded: true, in_force: true },
      ],
    });
  });

  await loginAs(page, "admin");
  await page.goto("/template", DCL);
  const row = page.getByTestId("tpl-row").filter({ hasText: "hkfrs_hk_china_v1" }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  // The column has to name what the extractor would actually use for this template…
  const column = row.getByTestId("tpl-row-ontology");
  await expect(column).toContainText("hkfrs_hk_china_v2 · v1", { timeout: 15_000 });
  // …and it must not print the bare word "superseded" beside it. This row is in force AND carries
  // the replacement label, and the column only ever names the rulebook in force — so the label has
  // to be the reconciled phrase the extraction view's picker already uses, or an admin reads that
  // the extractor is governed by a retired rulebook when it is the current one. Asserted through the
  // rendered text rather than an i18n key, because what is wrong is what a reader sees.
  await expect(column).toContainText("in force, though superseded");
  await expect(column).not.toHaveText(/·\s*v1\s*superseded\s*$/);
  await page.unroute("**/api/v1/ontologies");
});

test("the index renders its rows without fetching a per-template document for any of them",
     async ({ page }) => {
  // The index used to carry a LINE ITEMS column, and that count is not in GET /templates: it is
  // only on the per-template detail, ~230 KB of tree and criteria. Printing it cost one such
  // document per row — measured at 922 KB across four rows, 99% of everything the page fetched, to
  // render four integers — and fetching them concurrently moved none of those bytes. The column is
  // gone, so the index must now ask for nothing per row at all.
  const perTemplate: string[] = [];
  page.on("request", (r) => {
    if (/\/api\/v1\/templates\/[^/]+(\/detail)?(\?|$)/.test(r.url())) perTemplate.push(r.url());
  });

  await loginAs(page, "admin");
  await page.goto("/template", DCL);
  const rows = page.getByTestId("tpl-row");
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  // More than one version has to be stored for the question to mean anything — the cost was per
  // ROW. The workbook round-trip test above publishes one, and this suite is serial.
  expect(await rows.count()).toBeGreaterThan(1);

  // Everything the index prints came out of the ONE list call: name, key, version, state, rulebook.
  await expect(rows.first()).toContainText(/hkfrs/i);
  await expect(rows.first().getByTestId("tpl-row-ontology")).not.toBeEmpty();
  await expect(page.getByTestId("tpl-row-lines")).toHaveCount(0);
  // Wait before declaring victory: a fetch fired on mount can still be in flight, and asserting
  // "none yet" would pass on timing rather than on there being none.
  await page.waitForTimeout(2_000);
  expect(perTemplate).toEqual([]);

  // What a reader loses is comparing spread sizes ACROSS rows at a glance. The number itself is not
  // lost: it is one click away, on the page that has to fetch that document anyway.
  await rows.first().click();
  await expect(page.getByTestId("template-detail")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("template-detail")).toContainText(/\d+ line items/,
                                                                 { timeout: 15_000 });
  expect(perTemplate.length).toBe(1);
});

test("leaving a template with unsaved ontology edits asks before discarding them", async ({
  page,
}) => {
  await loginAs(page, "admin");
  await openTemplateDetail(page);
  const nodes = page.getByTestId("tpl-node");
  await expect(nodes.first()).toBeVisible({ timeout: 15_000 });
  await nodes.first().click();

  // An alias that exists only in this browser until Save publishes a new ontology version.
  const alias = `E2E discard ${Date.now()}`;
  const input = page.getByPlaceholder("New alias");
  await input.fill(alias);
  await input.press("Enter");
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  // "← All templates" must not throw it away without a word.
  await page.getByTestId("tpl-detail-close").click();
  await expect(page.getByTestId("tpl-leave-confirm")).toBeVisible();
  await expect(page.getByTestId("template-detail")).toBeVisible();

  // Keep editing → still here, edit intact.
  await page.getByTestId("tpl-leave-stay").click();
  await expect(page.getByTestId("tpl-leave-confirm")).toHaveCount(0);
  await expect(page.getByText(alias)).toBeVisible();
  await expect(page.getByText("Unsaved changes")).toBeVisible();

  // Discard → the detail goes, and nothing was published: the alias is not in the reloaded rules.
  await page.getByTestId("tpl-detail-close").click();
  await page.getByTestId("tpl-leave-discard").click();
  await expect(page.getByTestId("template-detail")).toHaveCount(0);
  await openTemplateDetail(page);
  await expect(nodes.first()).toBeVisible({ timeout: 15_000 });
  await nodes.first().click();
  await expect(page.getByText(alias)).toHaveCount(0);

  // And with nothing pending the question is not asked — leaving stays one click.
  await page.getByTestId("tpl-detail-close").click();
  await expect(page.getByTestId("template-detail")).toHaveCount(0);
});

test("an analyst chooses which rulebook a run reads the filing against", async ({ page }) => {
  test.setTimeout(180_000);
  // What the run was actually started with. The picker is only real if the choice reaches the POST
  // that starts the extraction — a select that changed a caption would be decoration.
  const posted: (string | null)[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/documents\/[^/]+\/extractions$/.test(r.url())) {
      try {
        posted.push((JSON.parse(r.postData() ?? "{}").ontology_version_id as string) ?? null);
      } catch {
        posted.push(null);
      }
    }
  });

  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "sample.pdf" }))
    .toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Extract directly/ }).click();
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 60_000 });

  // The default is the rulebook in force, and the RUN says so — the sentence under the picker is
  // read from the run's own record, so it is evidence about the run rather than a caption.
  const pick = page.getByTestId("ex-rulebook-pick");
  await expect(pick).toBeVisible();
  const used = page.getByTestId("ex-rulebook-used");
  const values = await pick.locator("option").evaluateAll(
    (os) => os.map((o) => (o as HTMLOptionElement).value));
  // MORE THAN ONE STORED RULEBOOK VERSION is what makes pinning possible, and where it comes from
  // has changed. The comment here used to name "the seeded pair (the adopted v2 and the v1 it
  // replaces)" — a pair that no longer exists: the repo consolidated to ONE rulebook and RETIRED
  // both of those keys, and the pair was only ever present because the suite ran against a
  // developer's pre-consolidation database. What the list holds now is several VERSIONS of the one
  // shipped rulebook, published by the three inline-edit tests above in this serial file, and
  // pinning an older version of one rulebook is the same question this screen exists to answer.
  // Not established inside this test on purpose: publishing a rival rulebook would change what is in
  // force for every test after it, and there is no endpoint to take one back.
  expect(values.length,
         "only one rulebook version is stored, so nothing can be pinned AGAINST the one in force — "
         + "the ontology-edit tests above are what publish the others").toBeGreaterThan(1);
  const inForceId = await pick.inputValue();
  expect(posted).toContain(inForceId);
  await expect(used).toHaveAttribute("data-rulebook-id", inForceId, { timeout: 60_000 });
  await expect(used).toContainText(/the rulebook in force for/);

  // Pin the other one. Two things have to be true, and only one of them was checkable before: the
  // choice has to reach the POST that starts the run, and the RUN has to come back saying it read
  // the filing against that rulebook. A picker that changed only the caption would pass the first.
  const other = values.find((v) => v && v !== inForceId) as string;
  const label = await pick.locator(`option[value="${other}"]`).textContent() ?? "";
  const [, otherKey, otherVersion] = /^\s*(\S+) · v(\d+)/.exec(label) ?? [];
  expect(otherKey).toBeTruthy();
  await pick.selectOption(other);
  await expect.poll(() => posted[posted.length - 1], { timeout: 60_000 }).toBe(other);
  // What the run RECORDED, straight off the element that prints it: the pinned rulebook, named.
  await expect(used).toHaveAttribute("data-rulebook-id", other, { timeout: 60_000 });
  await expect(used).toContainText(`${otherKey} v${otherVersion}`);
  await expect(used).toContainText(/not the rulebook in force/);
  // …and that run really read the filing: the rows come back for the pinned rulebook too.
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 60_000 });

  // The pin has to survive a reload, and as component state it did not: reopening the screen moved
  // the reader silently back to the rulebook in force, with nothing on screen saying the figures
  // had been produced under a different one. The pin is in the URL now, so it is both durable and
  // shareable — which is what comparing two rulebooks on one filing actually needs.
  expect(new URL(page.url()).searchParams.get("rulebook")).toBe(other);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("ex-rulebook-pick")).toHaveValue(other, { timeout: 60_000 });
  const usedAgain = page.getByTestId("ex-rulebook-used");
  await expect(usedAgain).toHaveAttribute("data-rulebook-id", other, { timeout: 60_000 });
  await expect(usedAgain).toContainText(/not the rulebook in force/);
});

/** One row of GET /ontologies — only the fields this file reasons about, spelled here so a field the
 *  server stops serving fails on the cast rather than arriving as `undefined`. */
interface OntologyRow {
  id: string; ontology_key: string; version: number; superseded: boolean;
  // WHICH rulebook the next run maps against, stated by the server (routes/ontologies.py) because
  // the rule is "the latest stored wins" and turns on `created_at`, which this payload does not
  // carry. Read it; never rank the list to work it out.
  in_force: boolean;
  target_template_key: string; concept_count: number; alias_count: number;
}

/** An ontology_key the repo has RETIRED (app/sample/reference.py::RETIRED_ONTOLOGY_KEYS).
 *
 * One of the two pre-consolidation rulebook names. `superseded_keys` reports a stored rulebook under
 * such a key as replaced the moment the shipped rulebook is also present — which is the only way a
 * "superseded rulebook" can be brought into existence from a browser: there is no DELETE for an
 * ontology, so publishing a rival that DECLARES it supersedes the shipped one would take the shipped
 * one out of force for every test after this file's, irreversibly. A retired name replaces nothing. */
const RETIRED_RULEBOOK_KEY = "hkfrs_hk_china_v2";

test("a run that used a superseded rulebook says so, however the client sees the list",
     async ({ page }) => {
  test.setTimeout(240_000);
  // The blocker this closes: the sentence naming the rulebook was written by the SCREEN, from the
  // ontology list and its own idea of which one was in force. So a reload after the adopted
  // rulebook changed hands described a run that had read the filing against a REPLACED rulebook as
  // governed by the current one — an audit statement, wrong, with nothing on screen to contradict
  // it.
  //
  // Reproduce it by making the client's view disagree with the truth: the ids stay real, only the
  // `in_force` flag is moved, so the client picks (and pins) the rulebook the server knows has been
  // replaced, while its own list insists that one is current. Re-derivation on this page therefore
  // says "in force"; the run's record says "replaced". The screen must print the record.
  //
  // THE REPLACED RULEBOOK IS ESTABLISHED HERE, NOT INHERITED. This test used to flip the flags
  // around the literal `hkfrs_hk_china_v1` and simply expect such a rulebook to be stored. It was —
  // because the suite ran against the developer's `backend/finex.db`, seeded before the
  // one-rulebook consolidation. Against the suite's own database (playwright.config.ts) it is not:
  // the repo ships ONE rulebook, `hkfrs_hk_china`, and names both pre-consolidation keys as retired.
  // So the state is now created through the real publish endpoint — and created as the state the
  // shipped code documents at length, a database that still holds one of those retired rulebooks.
  await loginAs(page, "admin");                     // publishing a rulebook is CONFIG_ONTOLOGY
  const before = await apiGet<OntologyRow[]>(page, "/api/v1/ontologies");
  // WHICH ROW IS IN FORCE is the server's decision (ontology_select.select_for_template): among the
  // rows targeting one template that nothing has replaced, the SHIPPED key wins, then a declared
  // supersession, then incumbency, and only then the highest edit version. This file cannot name the
  // shipped key, so it does not re-implement that rule — it asserts the premise under which "the
  // highest version" is the same answer, and says so out loud if the premise ever stops holding.
  // WHICH ROW IS IN FORCE IS READ, NOT RE-DERIVED. The server states it (`in_force` on each row,
  // computed by `ontology_select.select_for_template`), and this file must not re-implement the rule
  // — it used to, as "among live rows the highest version", back when selection ranked on five tests
  // (shipped key, declared supersession, incumbency, version). The rule is now simply "the latest
  // stored rulebook wins", which turns on `created_at` — a field this payload does not carry, so no
  // client-side ranking here could agree with the extractor even in principle.
  const inForceRows = before.filter((o) => o.in_force);
  expect(inForceRows.length, "no rulebook is in force at all, so there is nothing to publish a "
                             + "retired copy of").toBeGreaterThan(0);
  expect(new Set(inForceRows.map((o) => o.target_template_key)).size,
         "more than one template has a rulebook in force, so 'the rulebook in force' is ambiguous "
         + "for this test").toBe(1);
  expect(inForceRows.length, "two rows claim to be in force for one template").toBe(1);
  const inForce = inForceRows[0];
  const shipped = await apiGet<{ definition: Record<string, unknown> }>(
    page, `/api/v1/ontologies/${inForce.id}`);
  // Byte-identical to the rulebook in force except for its KEY: what makes this one "replaced" is
  // the name being one the repo retired, so nothing about how it maps differs and no later test's
  // figures can move because this row exists.
  const published = await apiSend(page, "POST", "/api/v1/ontologies", {
    definition: { ...shipped.definition, ontology_key: RETIRED_RULEBOOK_KEY },
  });
  expect(published.status(), await published.text()).toBe(201);

  // AND THEN THE SHIPPED RULEBOOK IS PUBLISHED AGAIN, which is what makes the retired copy pinnable
  // as a REPLACED rulebook rather than as the current one.
  //
  // Latest-stored wins, so the POST above just made the retired copy the newest thing stored — i.e.
  // in force. Pinning it would then record `in_force`, and the run this test needs would not exist.
  // Re-publishing the shipped definition under its own key puts a newer row in front of it: the
  // shipped KEY is in force again (at a higher version), and the retired copy is now both declared
  // replaced and not the latest, which is exactly the state a reproduction run legitimately pins.
  const republished = await apiSend(page, "POST", "/api/v1/ontologies", {
    definition: shipped.definition,
  });
  expect(republished.status(), await republished.text()).toBe(201);

  const after = await apiGet<OntologyRow[]>(page, "/api/v1/ontologies");
  const legacyRows = after.filter((o) => o.ontology_key === RETIRED_RULEBOOK_KEY);
  expect(legacyRows.length, `nothing is stored under ${RETIRED_RULEBOOK_KEY} after a 201`)
    .toBeGreaterThan(0);
  const legacy = legacyRows.reduce((best, o) => (o.version > best.version ? o : best));
  // The three halves of "the state is what this test needs", asserted rather than assumed.
  // (a) the published rulebook is one the SERVER calls replaced — without this the run below would
  //     record `pinned` and this test would be a second copy of the one above it;
  expect(legacy.superseded, `${RETIRED_RULEBOOK_KEY} is not reported as retired — this test needs a `
                            + "rulebook the server calls replaced").toBe(true);
  // (b) …and it is NOT the one in force, because being in force outranks the replacement label
  //     (extractions.rulebook_record): a rulebook that runs is never reported as the replaced one.
  expect(legacy.in_force, `${RETIRED_RULEBOOK_KEY} is still in force, so pinning it would record `
                          + "in_force and there would be no superseded run to check").toBe(false);
  // (c) the rulebook in force is the shipped KEY, so every later test runs against the same rules.
  const stillInForce = after.find((o) => o.in_force);
  expect(stillInForce, "nothing is in force after republishing").toBeTruthy();
  expect(stillInForce!.ontology_key, "publishing the retired rulebook took the shipped key out of "
                                     + "force, which would hand every later test different rules")
    .toBe(inForce.ontology_key);
  expect(stillInForce!.superseded, "the rulebook in force reports itself replaced").toBe(false);

  // MAKE THE CLIENT'S VIEW DISAGREE WITH THE TRUTH. `in_force` is the flag the picker selects on
  // (queries.ts::ontologyInForce reads it and no longer ranks the list itself), so that is the flag
  // to lie about: the ids stay real, only this one boolean moves. The client therefore picks — and
  // pins — the rulebook the server knows has been replaced, while its own list insists that one is
  // current. Re-derivation on this page says "in force"; the run's record says "replaced". The
  // screen must print the record.
  await page.route("**/api/v1/ontologies", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const rows = await res.json();
    await route.fulfill({
      response: res,
      json: rows.map((o: { id: string }) => ({ ...o, in_force: o.id === legacy.id })),
    });
  });

  // Sign the admin out before signing the analyst in. `loginAs` starts at "/", which redirects a
  // SIGNED-IN browser to /workspace — so with the admin's token still in localStorage the second
  // login waits four minutes for a sign-in button that is not on the screen. Measured, not guessed:
  // that is exactly how this test failed once the establish phase above was added. Clearing the
  // store is what a fresh context does, and it also drops the admin's active document, which the
  // analyst's browser has no business carrying.
  await signOut(page);
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "sample.pdf" }))
    .toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Extract directly/ }).click();
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 60_000 });

  // The client's list is what selected the rulebook, and it believes that one is current…
  const pick = page.getByTestId("ex-rulebook-pick");
  await expect(pick).toBeVisible();
  const chosen = await pick.locator("option:checked").textContent() ?? "";
  expect(chosen).toContain(RETIRED_RULEBOOK_KEY);
  expect(chosen).toContain("in force");

  // …and the run, which is the only thing that knows, says it was read against a rulebook that has
  // been replaced, and names what is actually in force instead. Both names come from the API rather
  // than from literals here, because the claim is that the screen prints the server's record.
  const used = page.getByTestId("ex-rulebook-used");
  await expect(used).toHaveAttribute("data-rulebook-status", "superseded", { timeout: 60_000 });
  await expect(used).toContainText(`${RETIRED_RULEBOOK_KEY} v${legacy.version}`);
  await expect(used).toContainText(/has since been replaced/);
  await expect(used).toContainText(
    `The rulebook in force is ${stillInForce!.ontology_key} v${stillInForce!.version}`);

  // It survives a reload — the reload IS the failure this closes, and nothing about it is derived
  // from the list the browser holds.
  await page.reload(DCL);
  await expect(page.getByTestId("ex-rulebook-used"))
    .toHaveAttribute("data-rulebook-status", "superseded", { timeout: 120_000 });
  await expect(page.getByTestId("ex-rulebook-used")).toContainText(/has since been replaced/);
  await page.unroute("**/api/v1/ontologies");
});

test("the upload screen describes the rulebook the run will use, not a fabricated one",
     async ({ page }) => {
  // The blocker this closes: the card printed the SAMPLE project's ontology filename over a fixed
  // "1,240 rules · 380 aliases" and a green "Valid" badge. Three claims, none of them about any
  // rulebook this product has ever held, on the screen where an analyst decides whether the run is
  // configured correctly — and plausible enough in magnitude that nobody questioned them.
  //
  // Checked against the API rather than against expected text, because the point is that the two
  // agree: the card is only right if it names the rulebook /ontologies reports in force and prints
  // that rulebook's own counts.
  await loginAs(page, "analyst");
  const rows = await (await page.request.get("/api/v1/ontologies")).json();
  expect(rows.length).toBeGreaterThan(0);

  await page.goto("/upload", DCL);
  const card = page.getByTestId("u-rulebook");
  // Wait for the card to have RESOLVED a rulebook, not merely to be on screen: it renders while
  // the list is in flight, and reading the id then says "no rulebook" about a request in progress.
  await expect(card).toHaveAttribute("data-rulebook-id", /.+/, { timeout: 15_000 });
  const id = await card.getAttribute("data-rulebook-id");
  const ont = rows.find((o: { id: string }) => o.id === id);
  expect(ont, "the card must name a rulebook the server actually serves").toBeTruthy();
  // And it must be the rulebook the server says is IN FORCE, not merely one that has not been
  // replaced — those are different claims, and the card's whole job is to say what the run will use.
  expect(ont.in_force, "the card names a rulebook the server does not report as in force").toBe(true);
  expect(ont.superseded).toBeFalsy();
  await expect(card).toHaveText(ont.ontology_key);

  // Its size, off its own definition — and the counts have to be load-bearing, so a real rulebook
  // names its concepts in more than one way.
  const meta = page.getByTestId("u-rulebook-meta");
  expect(ont.concept_count).toBeGreaterThan(100);
  expect(ont.alias_count).toBeGreaterThan(ont.concept_count);
  await expect(meta).toContainText(`v${ont.version}`);
  await expect(meta).toContainText(`${ont.concept_count.toLocaleString("en")} concepts`);
  await expect(meta).toContainText(`${ont.alias_count.toLocaleString("en")} aliases`);
  await expect(page.getByText("1,240 rules")).toHaveCount(0);
});

test("a review filter chip filters, and its count is the length of the list it produces",
     async ({ page }) => {
  // The chips looked clickable — cursor: pointer, an active style — and did nothing: the active
  // tab was hardcoded to index 0 and no chip carried an onClick. So five controls advertised a
  // filter the screen did not have, above counts that had never been derived from the checks.
  //
  // Each tab now names the check TYPES it selects, and this asserts the two halves agree: the
  // number on a chip is the number of cards clicking it leaves on screen.
  await loginAs(page, "admin");
  await setSampleLoaded(page, true);
  await page.goto("/review", DCL);

  const chips = page.getByTestId("rv-tab");
  await expect(chips.first()).toBeVisible({ timeout: 15_000 });
  const n = await chips.count();
  expect(n).toBeGreaterThan(1);

  // "All" is selected to begin with and shows every check.
  await expect(chips.nth(0)).toHaveAttribute("data-on", "true");
  const all = await page.getByTestId("rv-check").count();
  expect(all).toBeGreaterThan(0);
  expect(await chips.nth(0).textContent()).toContain(String(all));

  for (let i = 1; i < n; i++) {
    await chips.nth(i).click();
    await expect(chips.nth(i)).toHaveAttribute("data-on", "true");
    const label = (await chips.nth(i).textContent()) ?? "";
    const want = Number(/(\d+)\s*$/.exec(label.trim())?.[1]);
    expect(Number.isFinite(want)).toBeTruthy();
    // The chip's own count, against the cards it actually leaves on screen.
    await expect(page.getByTestId("rv-check")).toHaveCount(want);
  }

  // Every check is reachable under exactly one filter, so a finding cannot hide from all of them.
  let summed = 0;
  for (let i = 1; i < n; i++) {
    summed += Number(/(\d+)\s*$/.exec(((await chips.nth(i).textContent()) ?? "").trim())?.[1]);
  }
  expect(summed).toBe(all);
});

/* ===========================================================================================
 * The judgement layer, the coverage contract, and the five dead controls.
 *
 * Helpers first. They sit here rather than beside the ones at the top of the file only so this
 * block is purely appended — the suite is serial and nothing above depends on them.
 * =========================================================================================== */

/** Upload a fixture, run its REAL extraction, and return the document the app is now bound to.
 *
 * The id is read out of where the app persists it rather than parsed from the URL: every screen
 * below resolves the document from that key, so a test asking the API about a different id would
 * be comparing the screen against a payload the screen never saw. */
async function extractFixture(page: Page, file: string): Promise<string> {
  await page.goto("/upload", DCL);
  await page.setInputFiles('input[type="file"]', `e2e/fixtures/${file}`);
  await expect(page.getByTestId("doc-row").filter({ hasText: file }))
    .toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Extract directly/ }).click();
  await expect(page).toHaveURL(/\/extraction/);
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 60_000 });
  const id = await page.evaluate(() => localStorage.getItem("finex-active-doc"));
  expect(id, "the upload must bind the stepper to the document it just created").toBeTruthy();
  return id as string;
}

/** GET an API path AS THE SIGNED-IN USER.
 *
 * The session lives in localStorage, not a cookie, so an unadorned page.request is anonymous and
 * would 401 — and a test that compared a screen against a 401 body would compare it against
 * nothing. The token is lifted from the same place the fetch client reads it. */
async function apiGet<T>(page: Page, path: string): Promise<T> {
  const token = await page.evaluate(() => localStorage.getItem("finex-token"));
  const res = await page.request.get(path, { headers: { Authorization: `Bearer ${token}` } });
  expect(res.ok(), `${path} → ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

/** The leading integer of a chip/counter's text ("3 low-confidence" → 3). */
function leadingCount(text: string | null): number {
  const n = Number(/^(\d+)/.exec((text ?? "").trim())?.[1]);
  expect(Number.isFinite(n), `no count at the head of ${JSON.stringify(text)}`).toBeTruthy();
  return n;
}

/* Only the fields these tests actually rely on, spelled here rather than imported, so each
 * assertion states which part of the contract it is holding the screen to. */
interface ApiCheck {
  id: string; type: string; title: string; status: string;
  subject_key: string | null; evidence_digest?: string; fix_action: unknown | null;
  // What the card tells the reader to DO, and the offer that lets them do it. `fix_action` is the
  // one MECHANICAL correction (flip a mis-signed figure); `remap` is the row-shaped fix a human
  // drives — pick the template line this row belongs on. Two different things, and a card carrying
  // neither is the only card that says so in a sentence (Review.tsx: `!c.fix_action && !c.remap`).
  fix: string; remap: unknown | null;
}
interface ApiCovRow {
  statement: string; label: string; status: string; status_label: string;
  passed: number; failed: number; skipped: number; evaluated: number; declarable: number;
  validation_rate: number | null;
}
interface ApiCoverage {
  available: boolean; reason?: string; reason_label?: string;
  run_id?: string; engine_version?: string;
  aggregate?: ApiCovRow; statements?: ApiCovRow[];
  skips?: { bucket: string; count: number; counts_in_denominator: boolean }[];
  alarms?: { code: string; assurance_gap: boolean }[];
  failed_reported_elsewhere?: number;
}
interface ApiReview {
  run_id: string;
  checks: ApiCheck[];
  tabs: { label: string; count: number; types: string[] | null }[];
  summary: { open: number; accepted: number; stale: number; passed: number };
  judgements: { orphaned: unknown[] };
  coverage: ApiCoverage;
  // The template lines a row-shaped finding may be re-mapped onto, served ONCE per payload rather
  // than per card. Empty when the run named no template, which is also when the offer is refused.
  remap_targets: { canonical_key: string }[];
}

/** Put this document's queue back to "nobody has judged anything" before asserting on it.
 *
 * A judgement is PERSISTED, and an upload of identical bytes dedups onto the document that is
 * already there — so a run that died between an acceptance and its withdrawal would hand the next
 * run a queue with a judgement already in it, and "the first open finding" would be a different
 * finding or none. Same discipline as resetThresholds above: establish the baseline, do not
 * inherit it. Requires review:resolve, so call it as an admin or a reviewer. */
async function clearJudgements(page: Page, doc: string): Promise<void> {
  const token = await page.evaluate(() => localStorage.getItem("finex-token"));
  const rev = await apiGet<ApiReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  for (const c of rev.checks) {
    if (c.status === "open" || !c.subject_key) continue;
    const res = await page.request.delete(
      `/api/v1/documents/${doc}/review/judgements/${c.subject_key}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    expect(res.ok(), `withdrawing ${c.subject_key} → ${res.status()}`).toBeTruthy();
  }
}

test("a finding can be ACCEPTED, and the judgement is still there after a reload", async ({
  page,
}) => {
  // The defect: "Apply fix" and "Accept as is" had no onClick, there was no resolve endpoint and
  // no table behind one, so a finding a reviewer had examined and judged acceptable stayed red
  // for ever — nothing on the screen separated "not looked at" from "reviewed and accepted".
  //
  // The reload is the assertion that matters. A judgement held in component state would satisfy
  // every check above it and evaporate the moment the reader refreshed, which is precisely the
  // failure mode a pin on this product had before: the state changed, the record did not.
  test.setTimeout(240_000);
  // Admin because this test needs BOTH capabilities: uploading (documents:manage) and judging
  // (review:resolve). The role map gives no single other role both.
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");

  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  // Judgements outlive a suite run, and the upload dedups onto the same document, so start from
  // an unjudged queue rather than from whatever an interrupted run left behind.
  await clearJudgements(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  // What the server says about THIS run, asked the way the screen asks. Every number below is
  // checked against this payload rather than written into the test: how many findings the fixture
  // raises depends on the extraction thresholds, which other tests move, and a literal here would
  // be the same class of defect the product was just cleaned of.
  const rev = await apiGet<ApiReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const target = rev.checks.find((c) => c.status === "open" && !!c.subject_key);
  expect(target, "the fixture must raise at least one judgeable finding").toBeTruthy();
  expect(rev.summary.open).toBeGreaterThan(0);

  const cards = page.getByTestId("rv-check");
  await expect(cards).toHaveCount(rev.checks.length, { timeout: 15_000 });
  // Located by its TITLE, not by index: accepting re-ranks the queue server-side (stale, then
  // open, then accepted), so an index captured before the acceptance would afterwards point at a
  // different card and the test would quietly assert about the wrong finding.
  const card = cards.filter({ hasText: target!.title });
  await expect(card).toHaveCount(1);
  // Wait for the STATE, not for the card: it renders while its query is still in flight, and
  // data-status read then is null.
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 15_000 });
  await expect(page.getByTestId("rv-open")).toContainText(String(rev.summary.open));
  await expect(page.getByTestId("rv-accepted")).toContainText("0");

  await card.click();                       // a collapsed card is its own header
  const accept = card.getByTestId("rv-accept");
  await expect(accept).toBeVisible();

  // --- the fix that must NOT be offered, and the one that must -------------------------------
  // A low-confidence mapping has no MECHANICAL correction: nothing can be recomputed to settle "is
  // this the right concept?", so no `fix_action` is derived and no flip-the-sign button is drawn.
  // Asserted against the payload as well as the DOM, so "no button" is the server's judgement
  // rather than this test's assumption — and nothing is rendered disabled-and-grey, which would
  // still advertise a capability that does not exist.
  expect(target!.fix_action).toBeNull();
  // On this run the server derives no mechanical fix for any finding, so no card can show one.
  expect(rev.checks.filter((c) => c.fix_action !== null).length).toBe(0);
  await expect(card.getByTestId("rv-fix")).toHaveCount(0);
  await expect(page.getByTestId("rv-fix")).toHaveCount(0);
  await expect(page.getByText("Apply fix")).toHaveCount(0);   // the retired string, gone
  // …and what IS offered instead. This block used to assert the sentence "No automatic correction —
  // apply the fix above by hand", which Review.tsx renders only for a card with neither a fix nor a
  // re-map offer. A row-shaped finding now carries one — reassigning it still needs a human to pick
  // the concept, and that pick is a control now rather than advice — so asserting that sentence
  // here would be asserting the ABSENCE of the fix this round added.
  //
  // Row-shaped, asserted rather than assumed: `target` is the first OPEN judgeable finding of any
  // type, and the accounting types carry no offer at all. It holds today because this fixture raises
  // exactly one finding and it is low-confidence; the day it raises an accounting one that sorts
  // first, this line says so instead of blaming the re-map control for being legitimately absent.
  expect(ROW_SHAPED_TYPES.has(target!.type),
         `${target!.id} is a ${target!.type} finding, which carries no re-map offer — pick a `
         + "row-shaped finding for the half of this test below").toBeTruthy();
  expect(target!.remap, "a row-shaped finding must carry the re-map offer its fix text promises")
    .toBeTruthy();
  expect(rev.remap_targets.length,
         "the run named a template, so there are lines to offer").toBeGreaterThan(0);
  await expect(card.getByTestId("rv-remap")).toBeVisible();
  await expect(card.getByTestId("rv-remap-select")).toBeVisible();
  await expect(card.getByText(/No automatic correction/)).toHaveCount(0);

  // --- accepting ------------------------------------------------------------------------------
  // The reason is required, and the button says so by being unpressable rather than by letting a
  // 422 land: an acceptance with nothing stated is an unsigned claim.
  await expect(accept).toBeDisabled();
  const reason = `Agreed with the filing — e2e ${Date.now()}`;
  await card.getByTestId("rv-reason").fill(reason);
  await expect(accept).toBeEnabled();
  await accept.click();

  // Recorded: a status, a named person, and what they said.
  await expect(card).toHaveAttribute("data-status", "accepted", { timeout: 20_000 });
  await expect(card.getByTestId("rv-accepted-pill")).toBeVisible();
  await expect(card.getByTestId("rv-judged-by")).toContainText("admin");
  await expect(card.getByTestId("rv-judgement")).toContainText(reason);
  await expect(card.getByTestId("rv-error")).toHaveCount(0);
  // It is not hidden — an accepted finding stays in the queue it was counted in.
  await expect(cards).toHaveCount(rev.checks.length);

  // --- the counters and the chips still count the lists they head ------------------------------
  const after = await apiGet<ApiReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  // One acceptance, or more if two findings shared a subject — that is the documented collision,
  // and the counters have to survive it, so the count comes from the payload either way.
  expect(after.summary.accepted).toBeGreaterThanOrEqual(1);
  expect(after.summary.open).toBe(rev.summary.open - after.summary.accepted);
  expect(after.summary.open + after.summary.accepted).toBe(after.checks.length);
  await expect(page.getByTestId("rv-accepted")).toContainText(String(after.summary.accepted));
  await expect(page.getByTestId("rv-open")).toContainText(String(after.summary.open));

  const chips = page.getByTestId("rv-tab");
  const n = await chips.count();
  const all = await cards.count();
  expect(all).toBe(after.checks.length);
  expect(await chips.nth(0).textContent()).toContain(String(all));
  let summed = 0;
  for (let i = 1; i < n; i++) {
    await chips.nth(i).click();
    await expect(chips.nth(i)).toHaveAttribute("data-on", "true");
    const want = Number(/(\d+)\s*$/.exec(((await chips.nth(i).textContent()) ?? "").trim())?.[1]);
    expect(Number.isFinite(want)).toBeTruthy();
    // With an accepted finding present: the chip's number is still the length of the list
    // clicking it produces. Status is deliberately not a filter dimension, and this is what
    // would catch it being added on one side only.
    await expect(page.getByTestId("rv-check")).toHaveCount(want);
    summed += want;
  }
  expect(summed).toBe(all);
  await chips.nth(0).click();
  await expect(chips.nth(0)).toHaveAttribute("data-on", "true");

  // --- THE reload ------------------------------------------------------------------------------
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const again = page.getByTestId("rv-check").filter({ hasText: target!.title });
  await expect(again).toHaveAttribute("data-status", "accepted", { timeout: 20_000 });
  await expect(again.getByTestId("rv-accepted-pill")).toBeVisible();
  await expect(again.getByTestId("rv-judged-by")).toContainText("admin");
  await expect(page.getByTestId("rv-accepted")).toContainText(String(after.summary.accepted));

  // --- withdrawing, which is the other half of the transition and also the cleanup -------------
  // The row is not deleted server-side (the history keeps who accepted what), but the finding
  // returns to the queue — and hands the next run the queue this one found.
  await again.click();
  await again.getByTestId("rv-withdraw").click();
  await expect(again).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await expect(page.getByTestId("rv-accepted")).toContainText("0");
  await page.reload(DCL);
  const final = page.getByTestId("rv-check").filter({ hasText: target!.title });
  await expect(final).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await expect(final.getByTestId("rv-accepted-pill")).toHaveCount(0);
});

test("the coverage band counts RELATIONS, and every number in it is the API's for that run",
     async ({ page }) => {
  // The defect: the pipeline computed the coverage report — how many of the template's relations
  // could be evaluated against this filing at all — and sent it to run.logs, which no endpoint
  // served. So the Review screen listed failures and said nothing about relations that were never
  // evaluable, and "3 relations passed" read as "the statement is verified".
  //
  // Compared against the API rather than against expected numbers, because the point is that the
  // two agree: the band is only right if it prints the report the same request carried, and a
  // hardcoded expectation here would pass over a band that had drifted with the fixture.
  test.setTimeout(180_000);
  // As an ANALYST: the contract this closes is that the coverage gap reaches the person working
  // the queue, and that role holds no review:resolve — so the judgement controls must be absent
  // while everything else renders.
  await loginAs(page, "analyst");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const rev = await apiGet<ApiReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const cov = rev.coverage;
  expect(cov.available, "a run with a template attached must carry a coverage report").toBe(true);
  const agg = cov.aggregate!;

  const band = page.getByTestId("rv-coverage");
  await expect(band).toBeVisible({ timeout: 15_000 });
  // Wait for the band to have RESOLVED this run before reading a number off it — its footer
  // naming the run is what says the numbers below belong to it.
  await expect(page.getByTestId("rv-cov-footer")).toContainText(cov.run_id!, { timeout: 20_000 });
  await expect(page.getByTestId("rv-cov-footer")).toContainText(cov.engine_version!);

  // Aggregate: the fraction, the status the server named, and the word RELATIONS — the universe
  // this band counts, which is neither the findings nor the lines the tiles above it count.
  const aggLine = page.getByTestId("rv-cov-aggregate");
  await expect(aggLine).toContainText(`${agg.evaluated} / ${agg.declarable}`);
  await expect(aggLine).toContainText("relations evaluated");
  await expect(aggLine).toContainText(agg.status_label);
  await expect(band).toContainText("RELATIONS");

  // The two fractions, side by side, never one alone: a single rate is the collapse into a score
  // that the whole report exists to prevent.
  const fractions = page.getByTestId("rv-cov-fractions");
  if (agg.validation_rate === null) await expect(fractions).toContainText("no relation ran");
  else await expect(fractions).toContainText(`${agg.passed} / ${agg.evaluated}`);
  await expect(fractions).toContainText(`${agg.evaluated} / ${agg.declarable}`);
  // NO PERCENTAGE ANYWHERE. Two fractions carry the same information and cannot be read as a
  // score; one "12%" here is the misread the report was built to stop.
  expect(await band.innerText()).not.toContain("%");

  // One row per statement, each carrying the server's own status code and its own fractions —
  // including a statement this filing does not contain, which must be visible as ABSENT rather
  // than dragging a rate down.
  await expect(page.getByTestId("rv-coverage-stmt")).toHaveCount(cov.statements!.length);
  for (const s of cov.statements!) {
    const row = page.locator(`[data-testid="rv-coverage-stmt"][data-statement="${s.statement}"]`);
    await expect(row).toHaveAttribute("data-status", s.status);
    await expect(row).toContainText(`${s.evaluated} / ${s.declarable}`);
    await expect(row).toContainText(s.status_label);
    if (s.status === "UNVALIDATED") await expect(row).toContainText(/nothing here is verified/);
  }

  // WHY relations could not be evaluated, as labels — no cursor, no handler. A chip that looked
  // like a filter here would be another control with nothing behind it.
  const skips = page.getByTestId("rv-cov-skip");
  await expect(skips).toHaveCount(cov.skips!.length);
  for (const s of cov.skips!) {
    const chip = page.locator(`[data-testid="rv-cov-skip"][data-bucket="${s.bucket}"]`);
    await expect(chip).toContainText(String(s.count));
    expect(await chip.evaluate((el) => getComputedStyle(el).cursor)).not.toBe("pointer");
    // The one bucket outside the denominator says so, so the fractions above it add up.
    if (!s.counts_in_denominator) await expect(chip).toContainText("not counted");
  }

  // Alarms come from that list ONLY — one synthesised from a statement's status would be the same
  // alarm twice.
  await expect(page.getByTestId("rv-alarm")).toHaveCount(cov.alarms!.length);
  await expect(page.getByTestId("rv-cov-elsewhere"))
    .toHaveCount((cov.failed_reported_elsewhere ?? 0) > 0 ? 1 : 0);

  // The analyst sees all of that and gets no judgement controls: not disabled ones, none.
  expect(rev.checks.length, "this half needs a card to look inside").toBeGreaterThan(0);
  expect(rev.checks[0].fix_action).toBeNull();
  const card = page.getByTestId("rv-check").first();
  await expect(card).toHaveAttribute("data-status", /open|accepted|stale/, { timeout: 15_000 });
  await card.click();
  // The card's OWN advice, off the payload — proof the body really expanded, so "no Accept button"
  // below is a discrimination rather than an assertion about a collapsed card. This used to read the
  // "No automatic correction" sentence, which Review.tsx now renders only for a card carrying
  // neither a mechanical fix nor a re-map offer; a row-shaped finding carries the latter.
  await expect(card).toContainText(rev.checks[0].fix);
  await expect(page.getByTestId("rv-accept")).toHaveCount(0);
  await expect(page.getByTestId("rv-withdraw")).toHaveCount(0);
  await expect(page.getByTestId("rv-reason")).toHaveCount(0);
});

test("the coverage band on the sample says it has no report rather than rendering zeros",
     async ({ page }) => {
  // "0 of 0 relations evaluated" is the exact misread the coverage report exists to prevent, and
  // an empty band where a coverage statement belongs reads as "everything was checked". The
  // seeded sample has no structural run at all, so it has to say so in words.
  await loginAs(page, "admin");
  await setSampleLoaded(page, true);
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const band = page.getByTestId("rv-coverage");
  await expect(band).toBeVisible({ timeout: 15_000 });
  const stated = page.getByTestId("rv-cov-unavailable");
  await expect(stated).toHaveAttribute("data-reason", "sample", { timeout: 15_000 });
  await expect(stated).not.toBeEmpty();
  expect(await band.innerText()).not.toContain("0 / 0");
  await expect(page.getByTestId("rv-cov-aggregate")).toHaveCount(0);
  await expect(page.getByTestId("rv-cov-fractions")).toHaveCount(0);

  // …and both of the demo path's dead buttons are gone here too: the sample's checks carry no
  // subject key and no fix, so neither control is offered rather than offered and inert.
  const cards = page.getByTestId("rv-check");
  await expect(cards.first()).toHaveAttribute("data-status", "open", { timeout: 15_000 });
  expect(await cards.count()).toBeGreaterThan(1);
  // nth(1): the FIRST sample card is already expanded (the store's default openCheck is the id of
  // the demo's first check), so clicking that one would close it rather than open it.
  const card = cards.nth(1);
  await card.click();
  await expect(card.getByRole("button", { name: /Open in workspace/ })).toBeVisible();
  await expect(card.getByTestId("rv-accept")).toHaveCount(0);
  await expect(card.getByTestId("rv-fix")).toHaveCount(0);
  await expect(page.getByText("Apply fix")).toHaveCount(0);
});

test("the notes header names the filing's OWN periods, the same ones the workspace prints",
     async ({ page }) => {
  // The defect: the note detail's two column headers were the literals "FY25"/"FY24" while the
  // Workspace showed the filing's real period labels — so on a 2023/2022 filing the two screens
  // labelled the same figures differently, and one of them was simply wrong.
  //
  // The API halves are asserted against EACH OTHER rather than against expected words: the
  // finding is that two screens disagreed, so the assertion is that they cannot.
  test.setTimeout(240_000);
  await loginAs(page, "analyst");
  // A two-column comparative whose columns are named in the document, so the labels under test
  // are the filing's own words and not the localized Current/Prior fallback.
  const doc = await extractFixture(page, "comparative.pdf");

  const notes = await apiGet<{ notes: { no: number }[] }>(page, `/api/v1/documents/${doc}/notes`);
  expect(notes.notes.length).toBeGreaterThan(0);
  const no = notes.notes[0].no;
  const detail = await apiGet<{ periods?: string[] }>(
    page, `/api/v1/documents/${doc}/notes/${no}?locale=en`);
  const stmt = await apiGet<{ periods: string[] }>(
    page, `/api/v1/documents/${doc}/statement?statement=balance_sheet&basis=consolidated&locale=en`);
  expect(detail.periods, "the note endpoint has to serve the labels at all").toBeTruthy();
  const labels = detail.periods!.map((p) => p.trim());
  expect(labels).toEqual(stmt.periods.map((p) => p.trim()));
  expect(labels.length).toBe(2);
  expect(labels[0]).toBeTruthy();          // never a blank header cell
  expect(labels[1]).toBeTruthy();

  // The Notes screen prints those two words over the two figure columns.
  await page.goto("/notes", DCL);
  await page.getByText(`N${no}`, { exact: true }).click();
  const heads = page.getByTestId("note-period");
  await expect(heads).toHaveCount(2, { timeout: 20_000 });
  await expect(heads.nth(0)).toHaveText(labels[0]);
  await expect(heads.nth(1)).toHaveText(labels[1]);
  await expect(page.getByText("FY25")).toHaveCount(0);
  await expect(page.getByText("FY24")).toHaveCount(0);

  // And the Workspace prints the same words over the same figures.
  await page.goto("/workspace", DCL);
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(labels[0], { exact: true }).first()).toBeVisible();
  await expect(page.getByText("FY25")).toHaveCount(0);
});

test("the viewer zoom moves the rendered page, and reads the level off its own state",
     async ({ page }) => {
  // The defect: '−', '100%' and '+' were spans with cursor:pointer and no handler, and the middle
  // one was a literal — three controls advertising a zoom the screen did not have, over a number
  // nothing derived.
  //
  // The load-bearing assertion is the PAGE's geometry, not the caption: a percentage driven by
  // state while nothing moved would be the same defect with a working label on it.
  test.setTimeout(180_000);
  await loginAs(page, "analyst");
  await extractFixture(page, "sample.pdf");
  await page.goto("/workspace", DCL);
  await expect(page.getByText("Trade receivables").first()).toBeVisible({ timeout: 20_000 });

  const zoom = page.getByTestId("viewer-zoom");
  await expect(zoom).toBeVisible();
  const level = page.getByTestId("viewer-zoom-level");
  await expect(level).toHaveText("100%");
  await expect(level).toBeDisabled();          // already at 100%: there is nothing to reset
  const zin = zoom.getByRole("button", { name: "Zoom in" });
  const zout = zoom.getByRole("button", { name: "Zoom out" });

  // Wait for the page IMAGE, not the slot: the placeholder is width-driven too, and measuring it
  // would prove nothing about the page.
  const pageImg = page.locator("img").first();
  await expect(pageImg).toBeVisible({ timeout: 30_000 });
  const base = (await pageImg.boundingBox())!.width;
  expect(base).toBeGreaterThan(0);

  await zin.click();
  await expect(level).toHaveText("125%");
  await zin.click();
  await expect(level).toHaveText("150%");
  const wide = (await pageImg.boundingBox())!.width;
  expect(wide).toBeGreaterThan(base * 1.3);    // the rendered page really scaled

  await zout.click();
  await expect(level).toHaveText("125%");
  await level.click();                          // the percentage is the reset
  await expect(level).toHaveText("100%");
  await expect(level).toBeDisabled();
  expect(Math.abs((await pageImg.boundingBox())!.width - base)).toBeLessThan(2);

  // The end of the range is visibly disabled rather than silently inert.
  await zout.click();
  await zout.click();
  await expect(level).toHaveText("50%");
  await expect(zout).toBeDisabled();
  await level.click();
  await expect(level).toHaveText("100%");
});

test("the workspace chip counts the rows it filters to, and the invented counts are gone",
     async ({ page }) => {
  // The defect: the low-confidence chip rendered `usingReal ? lowConfCount : 3` — a fabricated 3
  // with the true count sitting on the line above it — and beside it a hardcoded "2 unreconciled".
  // Both carried cursor:pointer and no handler, so they read as filters.
  //
  // The count is checked against the rows it produces, never against a constant, and the chip's
  // ABSENCE on a statement with no low-confidence row is what would catch a literal returning:
  // a fabricated 3 would still be printed there.
  await loginAs(page, "admin");
  await setSampleLoaded(page, true);
  await page.goto("/workspace", DCL);
  // Wait for a row of the OUTPUT grid, not merely for the screen: the chip renders from the same
  // payload and reading its count while that is in flight measures nothing.
  await expect(page.getByTestId("v1-trade_recv")).toBeVisible({ timeout: 20_000 });

  const chip = page.getByTestId("ws-lowconf");
  await expect(chip).toHaveAttribute("aria-pressed", "false");
  const n = leadingCount(await chip.textContent());
  expect(n).toBeGreaterThan(0);
  const rows = page.locator('[data-testid^="v1-"]');       // one per figure-bearing grid row
  const before = await rows.count();
  expect(before).toBeGreaterThan(n);

  await chip.click();
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  await expect(rows).toHaveCount(n);                       // the number IS the list it produces
  await expect(page.getByTestId("v1-trade_recv")).toHaveCount(0);   // a confident row is out
  // The source column is never filtered — it shows the document as printed, and hiding a printed
  // line there would misrepresent the page.
  await expect(page.getByText("Trade receivables").first()).toBeVisible();
  await chip.click();
  await expect(chip).toHaveAttribute("aria-pressed", "false");
  await expect(rows).toHaveCount(before);

  // Derived, not written: the sample's P&L carries no low-confidence row, so there is no chip at
  // all rather than a red pill counting nothing.
  await page.getByTestId("seg-profit_and_loss").click();
  await expect(page.getByTestId("v1-rev")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("ws-lowconf")).toHaveCount(0);
  // And nothing claims to know how many lines are "unreconciled": no quantity in the statement
  // payload means it, so the chip is gone rather than fabricated.
  await expect(page.getByText(/unreconciled/i)).toHaveCount(0);

  // A viewer chip is a LABEL. The sample served an inactive-looking "Note 12 · p.171" beside the
  // active one, reading as a tab that cannot be selected — over a paper mock with no pages behind
  // it. One chip now, and no pointer promising a navigation that does not exist.
  const chips = page.getByTestId("ws-viewer-chip");
  await expect(chips).toHaveCount(1);
  expect(await chips.first().evaluate((el) => getComputedStyle(el).cursor)).not.toBe("pointer");
  // No zoom over that mock either: nothing there would move.
  await expect(page.getByTestId("viewer-zoom")).toHaveCount(0);

  await page.getByTestId("seg-balance_sheet").click();     // leave the screen as we found it
  await expect(page.getByTestId("seg-balance_sheet")).toHaveAttribute("data-on", "true");
});

test("the upload footer no longer offers a draft that had nowhere to be saved", async ({ page }) => {
  // "Save draft" sat between two working buttons with no onClick, and there was nothing to save
  // to: the schema has no draft entity, the document is already persisted by the upload, and the
  // rest of the screen's state is durable client state. A button that pretends to save is worse
  // than no button, so the assertion is its absence.
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  await expect(page.getByRole("button", { name: /Run integrity check/ }))
    .toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: /Save draft/i })).toHaveCount(0);
  await expect(page.getByText(/Save draft/i)).toHaveCount(0);
  // The two footer controls that do something are still there.
  await expect(page.getByRole("button", { name: /Extract directly/ })).toBeVisible();
});

test("the sample export checklist is a real choice, over only the options the workbook reads",
     async ({ page }) => {
  // The defect: the sample include-checklist was presentational (no onChange) yet wrapped in
  // pointerEvents:auto for an admin, so it advertised a choice and delivered none — and the
  // download posted `include: {}`, discarding it anyway. Four of its six boxes named options the
  // workbook builder never read.
  await loginAs(page, "admin");
  await setSampleLoaded(page, true);
  await page.goto("/export", DCL);
  await expect(page.getByRole("heading", { name: "Export" })).toBeVisible({ timeout: 15_000 });

  // The rows are the options the server serves, and it now serves only the two build_xlsx reads.
  const opts = await apiGet<{ options: { key: string; on: boolean }[] }>(
    page, "/api/v1/projects/demo/export-options");
  expect(opts.options.map((o) => o.key).sort()).toEqual(["confidence", "notes_sheet"]);
  const rows = page.getByTestId("e-include");
  await expect(rows).toHaveCount(opts.options.length, { timeout: 15_000 });

  // A tick that clears is the whole claim: the box reflects a choice this browser made, not the
  // server's default redrawn.
  const first = rows.first();
  await expect(first).toHaveAttribute("data-on", String(opts.options[0].on));
  await first.click();
  await expect(first).toHaveAttribute("data-on", String(!opts.options[0].on));
  await first.click();
  await expect(first).toHaveAttribute("data-on", String(opts.options[0].on));
});

/* ===========================================================================================
 * What three reviewers reproduced end to end: ONE identity carrying TWO different sets of
 * figures, an acceptance that could never go stale, and the two invented counts under the sample
 * export preview.
 *
 * Helpers first, appended for the same reason as the block above: the suite is serial and nothing
 * before this point depends on them.
 * =========================================================================================== */

/** Send a non-GET request AS THE SIGNED-IN USER and hand back the raw response.
 *
 * Unlike `apiGet` this does NOT assert the response is ok: the refusals are the subject here (a
 * 409 that must quote this card's figures and not another line's, a 404 for a subject nothing
 * raises), and a helper that threw on them could not be used to assert about them. `page.request`
 * is the context's own request stack, which `page.route` does not intercept — that is what lets a
 * test doctor what the SCREEN receives while still asking the server what is really stored. */
async function apiSend(
  page: Page, method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown,
) {
  const token = await page.evaluate(() => localStorage.getItem("finex-token"));
  return page.request.fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    data: body === undefined ? undefined : body,
  });
}

/* The judgement-bearing parts of one review check and of the payload around it. Spelled here
 * rather than imported, so every assertion below states which part of the contract it is holding
 * the screen to — and so a field the server stops sending fails here instead of arriving as
 * `undefined` and quietly satisfying a comparison. */
interface JCheck {
  id: string; type: string; title: string; where: string; status: string; delta: string;
  subject: Record<string, unknown>; subject_key: string | null;
  evidence: Record<string, unknown>; evidence_digest: string;
  calc: [string, string, boolean][];
  conflict: boolean; conflict_count: number; conflict_note: string;
  ambiguous: boolean; ambiguous_count: number; judgement_withheld: boolean;
  judgement: {
    actor: string; reason: string; at: string; changed: string[]; changed_label: string;
    accepted_rows: [string, string][];
  } | null;
}
interface JReview {
  run_id: string;
  checks: JCheck[];
  tabs: { label: string; count: number; types: string[] | null }[];
  summary: { open: number; accepted: number; stale: number; conflict: number; passed: number };
  judgements: { orphaned: unknown[] };
}

/** The card that submits under `subjectKey` — located by the IDENTITY it posts, never by index.
 *
 * Accepting re-ranks the queue server-side, so a position captured beforehand points at a
 * different finding afterwards. `data-subject-key` is on the card precisely so a test can tie an
 * assertion to the thing being judged rather than to where it currently sits. */
function cardForSubject(page: Page, subjectKey: string) {
  return page.locator(`[data-testid="rv-check"][data-subject-key="${subjectKey}"]`);
}

/** Every card EXCEPT the ones submitting under `subjectKey`. */
function cardsOtherThan(page: Page, subjectKey: string) {
  return page.locator(`[data-testid="rv-check"]:not([data-subject-key="${subjectKey}"])`);
}

test("accepting one finding records the verdict against THAT identity and re-labels no other card",
     async ({ page }) => {
  // FINDING A, the half that can be driven against a real extraction. Three reviewers reproduced
  // the same shape: two findings sharing a subject_key while printing different figures, where
  // accepting one made the server report the OTHER as "stale" carrying the accepting reviewer's
  // name, timestamp, reason and the first card's figures — a human judgement fabricated on a
  // finding nobody had examined, ranked to the top of the queue under the loudest strip on the
  // screen, with the header announcing that figures had changed when nothing had.
  //
  // Two independent things must hold for that to be impossible, and both are asserted here:
  //
  //  1. IDENTITY DISCRIMINATES. The subject is anchored on a content-derived source locator, not
  //     on the human-facing page label the card prints ("p.1"), which was one string for every
  //     line on a page. So no served card may carry its printed location as its identity, and two
  //     findings may share a subject_key only when they also agree about their figures.
  //  2. THE VERDICT LANDS ON ONE IDENTITY. After the acceptance, the card that submits under the
  //     accepted subject_key carries the judgement and NO other card carries an actor, a reason,
  //     a stale block or a stale status — and the server counts none.
  test.setTimeout(240_000);
  // Admin: uploading needs documents:manage and judging needs review:resolve, and no other single
  // role holds both.
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");

  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  // Judgements persist and an upload of identical bytes dedups onto the document already there, so
  // establish "nobody has judged anything" rather than inheriting what an interrupted run left.
  await clearJudgements(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const rev = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(rev.checks.length, "the fixture must raise a finding to judge").toBeGreaterThan(0);

  // --- 1. the identity the queue keys judgements on -------------------------------------------
  // Nothing on this filing is served as a conflict: its lines are distinguishable, and a conflict
  // here would mean the anchor had stopped discriminating.
  expect(rev.summary.conflict).toBe(0);
  await expect(page.getByTestId("rv-conflict-strip")).toHaveCount(0);
  await expect(page.getByTestId("rv-conflict")).toHaveCount(0);

  const groups = new Map<string, Set<string>>();
  for (const c of rev.checks) {
    if (!c.subject_key) continue;
    const values = Object.values(c.subject).map(String);
    // The printed location must not BE the identity. `where` is display text ("sample.pdf · p.1")
    // and its page part is page-level: as the subject it made every line on the page one finding,
    // which is how accepting one of two "Others" lines forged a verdict on the other.
    expect(values, `${c.id}: the card's printed location is its identity`).not.toContain(c.where);
    for (const v of values) {
      expect(v, `${c.id}: a page label is standing in for a source anchor`)
        .not.toMatch(/^p\.\s*\d+$/);
    }
    if (c.type === "unmapped" || c.type === "low_confidence") {
      // These are the two builders whose subject is {kind, label, anchor}. The anchor has to
      // locate the line WITHIN its source — a quantized bbox under its page, or sheet!cell — or
      // two lines printed one under the other under one caption collapse onto one identity again.
      // The documented sentinels (`#noprov`, `p{n}#nobox`) are honest fallbacks for an adapter
      // that reports no geometry, but a native PDF records boxes, so needing one here would mean
      // provenance had been lost — and that is the case the conflict refusal exists to cover.
      const anchored = values.filter(
        (v) => (/^p\d+#\S+/.test(v) && !/^p\d+#nobox$/.test(v)) || /^[^!]+![A-Z]+\d+$/.test(v));
      expect(anchored.length, `${c.id}: the subject carries no within-source anchor`)
        .toBeGreaterThan(0);
    }
    const seen = groups.get(c.subject_key) ?? new Set<string>();
    seen.add(c.evidence_digest);
    groups.set(c.subject_key, seen);
  }
  // Two findings on one subject are allowed ONLY when the figures agree — then one judgement
  // legitimately covers both. Differing figures on one subject is the fabrication this closes,
  // and the server would then have to serve a refusal, not an acceptance.
  for (const [key, digests] of groups) {
    expect(digests.size, `${key}: one identity over ${digests.size} different sets of figures`)
      .toBe(1);
  }

  // --- the endpoint quotes the card's OWN figures ---------------------------------------------
  // Resolution used to match on subject_key alone and compare the posted digest against whichever
  // card sorted FIRST, so the second finding on a shared subject was told "the figures changed
  // while this card was open" — quoting the other line's figures — on every retry, for ever. A
  // digest matching no card on this subject is a genuine 409, and what it quotes must be the
  // state of the card the reviewer is looking at.
  const target = rev.checks.find((c) => c.status === "open" && !!c.subject_key);
  expect(target, "the fixture must raise a judgeable finding").toBeTruthy();
  const stale409 = await apiSend(page, "POST", `/api/v1/documents/${doc}/review/judgements`, {
    subject_key: target!.subject_key, evidence_digest: "0".repeat(64), reason: "e2e digest probe",
  });
  expect(stale409.status()).toBe(409);
  const refused = (await stale409.json()).detail;
  expect(refused.error).toBe("evidence_changed");
  expect(refused.current.evidence_digest).toBe(target!.evidence_digest);
  const ownFigures = new Set(Object.values(target!.evidence).map(String));
  for (const [, value] of refused.current.accepted_rows as [string, string][]) {
    // Figures are formatted server-side, so commas come off before comparing; the point is that
    // every figure quoted back belongs to this card's own evidence.
    expect(ownFigures.has(value.replace(/,/g, "")),
           `the refusal quotes ${value}, which is not this card's figure`).toBeTruthy();
  }
  // A subject no finding in this run carries is a 404 that says so — never a 409 explaining that
  // figures moved, which would send the reviewer hunting a change that never happened.
  const missing = await apiSend(page, "POST", `/api/v1/documents/${doc}/review/judgements`, {
    subject_key: "f".repeat(64), evidence_digest: target!.evidence_digest, reason: "e2e absent",
  });
  expect(missing.status()).toBe(404);
  expect((await missing.json()).detail.error).toBe("finding_not_found");

  // --- 2. the acceptance, and what it must NOT touch ------------------------------------------
  const card = cardForSubject(page, target!.subject_key as string);
  // Wait for the STATE, not for the card: it renders while its query is in flight and the status
  // attribute read then is null.
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await card.click();
  const reason = `Agreed with the filing — e2e collision guard ${Date.now()}`;
  await card.getByTestId("rv-reason").fill(reason);
  await card.getByTestId("rv-accept").click();
  await expect(card).toHaveAttribute("data-status", "accepted", { timeout: 20_000 });
  await expect(card.getByTestId("rv-judged-by")).toContainText("admin");
  await expect(card.getByTestId("rv-judgement")).toContainText(reason);
  await expect(card.getByTestId("rv-error")).toHaveCount(0);

  // Nothing else on the screen acquired a verdict. This is the assertion the reviewers'
  // reproduction fails: the second card came back "stale", named the accepting reviewer, and
  // printed the accepted card's figures.
  const others = cardsOtherThan(page, target!.subject_key as string);
  await expect(others.getByTestId("rv-judged-by")).toHaveCount(0);
  await expect(others.getByTestId("rv-judgement")).toHaveCount(0);
  await expect(others.getByTestId("rv-stale")).toHaveCount(0);
  await expect(page.locator('[data-testid="rv-check"][data-status="stale"]')).toHaveCount(0);
  // The reason exists on exactly ONE card. A judgement copied onto a second finding shows up here
  // as two cards carrying one reviewer's words.
  await expect(page.locator('[data-testid="rv-check"]', { hasText: reason })).toHaveCount(1);

  // …and the counters say the same: nothing went stale, so the amber "accepted findings rest on
  // figures that have changed since" strip must not be on the screen at all.
  const after = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(after.summary.stale).toBe(0);
  expect(after.summary.conflict).toBe(0);
  await expect(page.getByTestId("rv-stale-strip")).toHaveCount(0);
  await expect(page.getByTestId("rv-conflict-strip")).toHaveCount(0);
  // Exactly the accepted subject is accepted, and it is the subject that was posted.
  const accepted = after.checks.filter((c) => c.status === "accepted");
  expect(accepted.length).toBe(after.summary.accepted);
  for (const c of accepted) expect(c.subject_key).toBe(target!.subject_key);
  // Nor did the acceptance land on a subject the queue does not raise: an orphaned judgement here
  // would mean the row was written against something else entirely.
  expect(after.judgements.orphaned).toEqual([]);

  // --- leave the queue as we found it ---------------------------------------------------------
  await card.getByTestId("rv-withdraw").click();
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  const final = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(final.summary.accepted).toBe(0);
  expect(final.summary.stale).toBe(0);
});

test("a finding the queue cannot tell apart from another offers no acceptance and shows no verdict",
     async ({ page }) => {
  // FINDING A's user-visible half, held against the screen. The server can no longer be made to
  // produce this group from e2e/fixtures — the source anchor keeps two printed lines apart, and
  // none of the three fixtures prints two identically-captioned lines in the first place — so the
  // SCREEN is tested against the contract the server serves for it: `status: "conflict"` with
  // `conflict`, `conflict_count`, `conflict_note` and `judgement_withheld`, counted in
  // `summary.conflict`. That contract is proved end to end in the backend suite; what only a
  // browser can answer is whether the reviewer is offered a control that must not exist and shown
  // a verdict that is not about their card.
  //
  // The payload therefore carries the SHIPPED defect's shape as well as the fix's: each conflict
  // card arrives with a full `judgement` belonging to some other finding, and with
  // `ambiguous: true` claiming "accepting one accepts them all". The screen must print neither —
  // that caption was false over figures that differed, and that verdict names a person who never
  // saw these numbers.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);

  // The real queue, read before anything is doctored: the collision is modelled ON one of its
  // cards, so every field the renderer needs is a real one.
  const real = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const base = real.checks.find((c) => !!c.subject_key);
  expect(base, "the fixture must raise a card to model the collision on").toBeTruthy();

  const SHARED = "e2e-one-identity-two-sets-of-figures";
  const A_FIG = "8,675,309";
  const B_FIG = "5,551,212";
  const NOTE = "2 findings here share one identity but printed different figures, so the queue "
             + "cannot tell them apart. None of them can be accepted until the extraction "
             + "distinguishes them. A recorded acceptance for this identity is being withheld.";
  const FOREIGN_ACTOR = "e2e-not-this-reviewer";
  const FOREIGN_REASON = "e2e verdict recorded about the other line entirely";

  // Any POST that escapes the screen is itself the failure: a card with no control must also make
  // no request, and a fabricated acceptance is exactly a POST nobody authorised.
  const posted: string[] = [];
  page.on("request", (r) => {
    // `(\?|$)`, not `$`: the client posts to `…/review/judgements?locale=en`, so an end-anchored
    // pattern here matched nothing and this guard could never have caught the POST it exists to
    // catch. The same oversight made the injected refusals in the test below never fire at all.
    if (r.method() === "POST" && /\/review\/judgements(\?|$)/.test(r.url())) posted.push(r.url());
  });

  await page.route("**/api/v1/documents/*/review*", async (route) => {
    // The queue READ only. `*` does not cross a "/", so the judgement POST and DELETE under this
    // prefix are not matched at all — if the screen did offer a control, it would reach the real
    // endpoint. The method guard is belt and braces for a widened pattern.
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const body = (await res.json()) as JReview;
    const twin = (id: string, figure: string, digest: string): JCheck => ({
      ...(base as JCheck),
      id,
      // Both lines print the same caption on the same page — the collision's whole premise.
      title: "Others",
      where: base!.where,
      subject: { k: "unmapped", label: "others", anchor: "p0#nobox" },
      subject_key: SHARED,
      evidence: { value: figure },
      evidence_digest: digest,
      calc: [["Source label", "Others", false], ["Value", figure, true]],
      status: "conflict",
      conflict: true, conflict_count: 2, conflict_note: NOTE, judgement_withheld: true,
      // The false caption, served on purpose: it was shown over both cards of a real collision.
      ambiguous: true, ambiguous_count: 2,
      judgement: {
        actor: FOREIGN_ACTOR, reason: FOREIGN_REASON, at: "2026-01-01T09:00:00",
        changed: [], changed_label: "",
        // The OTHER card's figure — which is exactly what the fabricated stale card printed.
        accepted_rows: [["Value", A_FIG]],
      },
    });
    // Conflict first, because that is the order the server serves (rank 0) and the client does no
    // sorting of its own: two orderings of one list is two answers to one question.
    body.checks = [twin("chk-e2e-conflict-a", A_FIG, "e2e-digest-a"),
                   twin("chk-e2e-conflict-b", B_FIG, "e2e-digest-b"),
                   ...body.checks];
    body.summary = { ...body.summary, conflict: 2, open: body.summary.open + 2 };
    // The chips count by TYPE over the whole list, so the two extra cards are counted where they
    // would be counted for real; a chip whose number stopped being the length of its own list
    // would be a second defect introduced by this fixture.
    body.tabs = body.tabs.map((tb) =>
      tb.types === null || tb.types.includes("unmapped") ? { ...tb, count: tb.count + 2 } : tb);
    await route.fulfill({ response: res, json: body });
  });

  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const cards = page.getByTestId("rv-check");
  await expect(cards).toHaveCount(real.checks.length + 2, { timeout: 20_000 });

  // Stated once at the top, over the count the server derived — and it is the FIRST card in the
  // list, because a group nobody can act on outranks a finding nobody has looked at.
  await expect(page.getByTestId("rv-conflict-strip")).toContainText("2", { timeout: 15_000 });
  await expect(cards.nth(0)).toHaveAttribute("data-status", "conflict", { timeout: 15_000 });

  const conflicting = page.locator(`[data-testid="rv-check"][data-subject-key="${SHARED}"]`);
  await expect(conflicting).toHaveCount(2);
  for (let i = 0; i < 2; i++) {
    const c = conflicting.nth(i);
    await expect(c).toHaveAttribute("data-status", "conflict", { timeout: 15_000 });
    // Visible while collapsed: the reason there is no Accept button must not be discoverable only
    // by expanding the card and finding nothing.
    await expect(c.getByTestId("rv-conflict-pill")).toBeVisible();
    await c.click();
    // The refusal, in the SERVER's own sentence — the screen composes no second version of it.
    await expect(c.getByTestId("rv-conflict")).toBeVisible();
    await expect(c.getByTestId("rv-conflict-message")).toHaveText(NOTE);
    // No ACCEPTANCE path at all: not a disabled button, none.
    await expect(c.getByTestId("rv-accept")).toHaveCount(0);
    await expect(c.getByTestId("rv-reason")).toHaveCount(0);
    // WITHDRAWAL IS NOT ACCEPTANCE, and this used to assert `rv-withdraw` was absent too. That was
    // the shipped defect, not the fix: the payload here carries `judgement_withheld`, i.e. the
    // server holds a real acceptance against this subject and refuses to attribute it to either
    // card — and `withdraw_review_judgement` deliberately PERMITS taking that row back. Asserting
    // the control's absence is asserting the state round 2's finding 5 closed, where a named
    // acceptance stood with nothing anywhere able to remove it. So the control is required, and
    // required to say which of the two withdrawable shapes it stands for. That it WORKS is driven
    // for real one test below; what is asserted here is that a card refusing to show a verdict
    // still offers the way out.
    await expect(c.getByTestId("rv-withdraw")).toHaveAttribute("data-withheld", "true");
    // And no verdict, although the payload carried one. That is the fabrication itself: an actor,
    // a timestamp, a reason and another line's figures against a card whose numbers that person
    // never saw.
    await expect(c.getByTestId("rv-judged-by")).toHaveCount(0);
    await expect(c.getByTestId("rv-judgement")).toHaveCount(0);
    await expect(c.getByTestId("rv-stale")).toHaveCount(0);
    await expect(c.getByTestId("rv-accepted-pill")).toHaveCount(0);
    // "accepting one accepts them all" is FALSE here, and the payload claims it.
    await expect(c.getByTestId("rv-ambiguous")).toHaveCount(0);
  }
  // Neither the person nor their words appear anywhere on the screen…
  await expect(page.getByText(FOREIGN_ACTOR)).toHaveCount(0);
  await expect(page.getByText(FOREIGN_REASON)).toHaveCount(0);
  // …and neither card prints the other's figure, which is what the fabricated stale card did.
  await expect(conflicting.nth(0).getByText(B_FIG)).toHaveCount(0);
  await expect(conflicting.nth(1).getByText(A_FIG)).toHaveCount(0);

  // THE CONTROL. Same payload, same role, same screen: the untouched real finding still offers
  // acceptance. Without this, "no Accept button" above could just mean the harness never renders
  // one.
  const ok = cardForSubject(page, base!.subject_key as string);
  await expect(ok).toHaveAttribute("data-status", /open|accepted|stale/, { timeout: 15_000 });
  await ok.click();
  await expect(ok.getByTestId("rv-accept")).toBeVisible();
  await expect(ok.getByTestId("rv-reason")).toBeVisible();

  // Nothing was submitted, and nothing was stored: the real queue is exactly as it was. Unrouted
  // first, so what is read back is the server's answer and not the doctored one.
  expect(posted).toEqual([]);
  await page.unroute("**/api/v1/documents/*/review*");
  const untouched = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(untouched.summary.conflict).toBe(0);
  expect(untouched.summary.accepted).toBe(0);
  expect(untouched.checks.length).toBe(real.checks.length);
  await page.reload(DCL);
  await expect(page.getByTestId("rv-check")).toHaveCount(real.checks.length, { timeout: 20_000 });
  await expect(page.getByTestId("rv-conflict")).toHaveCount(0);
});

test("a refused acceptance is explained by the cause the server named, not the only one the screen knew",
     async ({ page }) => {
  // The accept endpoint sends THREE different 409s — the figures moved while the card was open,
  // the subject is a conflict no verdict may be attached to, and the write lost a race and stored
  // nothing — and the client dropped the structured `detail`, so all three read as "The figures
  // changed while this card was open". Two of those three readings are made-up causes: a reviewer
  // sent to look for a change that never happened, or told to re-judge figures when in fact
  // nothing was ever recorded.
  //
  // The refusals are injected because the real endpoint cannot be made to send the other two from
  // these fixtures (a conflict needs two indistinguishable findings; the write conflict needs two
  // simultaneous posts). What is under test is the READING: the screen says what the server said,
  // on the card that asked, and records nothing.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const rev = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const target = rev.checks.find((c) => c.status === "open" && !!c.subject_key);
  expect(target, "the fixture must raise a judgeable finding").toBeTruthy();
  const card = cardForSubject(page, target!.subject_key as string);
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await card.click();

  // The accept POST goes to `…/review/judgements?locale=en`, and a Playwright URL glob is matched
  // against the WHOLE url, anchored at both ends (`globToRegexPattern`): the pattern here was
  // `**/review/judgements`, which compiles to `^(.*\/)review\/judgements$` and therefore matched
  // NOTHING. So none of the three refusals below was ever injected — the click reached the real
  // endpoint, the acceptance was STORED, and this test asserted the client's reading of a 409 that
  // never happened while quietly leaving a judgement behind for the next test to inherit.
  //
  // The trailing `*` compiles to `([^/]*)`, so it takes the query string and still cannot reach
  // `…/review/judgements/{subject_key}` — the DELETE `clearJudgements` uses, which must go through.
  // The method guard stays for the same belt-and-braces reason it always had.
  const JUDGEMENTS_ROUTE = "**/review/judgements*";
  const refuse = (error: string) =>
    page.route(JUDGEMENTS_ROUTE, async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({
        status: 409,
        json: { detail: { error, subject_key: target!.subject_key, count: 2 } },
      });
    });

  // A subject two findings disagree about: refused, and the sentence says the identity is shared
  // rather than that the numbers moved.
  await refuse("subject_conflict");
  await card.getByTestId("rv-reason").fill("e2e refusal reading");
  await card.getByTestId("rv-accept").click();
  await expect(card.getByTestId("rv-error")).toContainText(/shares an identity/, { timeout: 15_000 });
  await expect(card.getByTestId("rv-error")).not.toContainText(/figures changed/);
  await page.unroute(JUDGEMENTS_ROUTE);

  // A write that lost a race: nothing was stored, and it says so — not "the figures changed", of
  // figures that did not change.
  await refuse("judgement_write_conflict");
  await card.getByTestId("rv-accept").click();
  await expect(card.getByTestId("rv-error")).toContainText(/Nothing was recorded/,
                                                          { timeout: 15_000 });
  await expect(card.getByTestId("rv-error")).not.toContainText(/figures changed/);
  await page.unroute(JUDGEMENTS_ROUTE);

  // A refused acceptance recorded nothing at all — the card is still open, here and on the
  // server, so the reviewer's next attempt starts from the truth.
  await expect(card).toHaveAttribute("data-status", "open");
  const after = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(after.summary.accepted).toBe(0);
  expect(after.judgements.orphaned).toEqual([]);
});

test("an acceptance stops reading 'accepted' the moment the figures it was made against move",
     async ({ page }) => {
  // FINDING B is that a whole class of finding could never reach this state: a rulebook GUARD's
  // evidence was the constant {actual:0, expected:0, diff:0, components:{}, sign_suspect:null},
  // because a guard puts its substance in details.violations — so nine violations of a blocking
  // rule stayed "accepted" on the strength of one person having examined one of them, and dropped
  // out of the open count entirely. The digest now fingerprints the violation SET.
  //
  // That cannot be driven from this browser: none of the three e2e fixtures fails a rulebook guard
  // (sample.pdf raises one low-confidence finding; comparative.pdf and sample.xlsx raise none), so
  // making an acceptance on a guard go stale needs a filing that violates one — a fixture this
  // suite does not have. The per-guard-kind proof is in the backend suite. What only a browser can
  // prove, and what nothing proved before, is that the stale state reaches the screen at all: that
  // an acceptance is withdrawn by the figures moving, loudly, carrying the numbers AS JUDGED
  // rather than the ones now on the card.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const rev = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  // A finding whose figure can be moved needs a canonical key to edit. Stated as a failure rather
  // than skipped: a test that quietly asserts nothing is worse than one that says out loud that
  // the fixture stopped raising what it was written for.
  const target = rev.checks.find(
    (c) => c.status === "open" && !!c.subject_key && typeof c.subject.key === "string");
  expect(target, "the fixture must raise a judgeable finding over a MAPPED line, whose figure can "
                 + "then be moved").toBeTruthy();
  const key = target!.subject.key as string;

  const card = cardForSubject(page, target!.subject_key as string);
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await card.click();
  const reason = `Checked against the printed page — e2e stale ${Date.now()}`;
  await card.getByTestId("rv-reason").fill(reason);
  await card.getByTestId("rv-accept").click();
  await expect(card).toHaveAttribute("data-status", "accepted", { timeout: 20_000 });

  // Move the figure the acceptance was made against — through the PATCH the Workspace inspector
  // posts to, not through the inspector: the editor has its own test above, and what is under test
  // here is the queue's reaction to the figures changing.
  const moved = await apiSend(page, "PATCH", `/api/v1/documents/${doc}/line-items/${key}`, {
    value: 424242, formula: "", basis: "consolidated", period: "current",
    comment: "e2e: move the accepted figure",
  });
  expect(moved.ok(), `moving the accepted figure → ${moved.status()}`).toBeTruthy();

  const afterEdit = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const stale = afterEdit.checks.find((c) => c.subject_key === target!.subject_key);
  expect(stale!.status).toBe("stale");
  expect(afterEdit.summary.stale).toBe(1);
  // Stale is outstanding work, not a settled acceptance — the distinction a guard finding could
  // never reach, which is how nine violations of a blocking rule read as vouched for.
  expect(afterEdit.summary.accepted).toBe(0);
  expect(afterEdit.summary.open).toBeGreaterThanOrEqual(1);

  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const again = cardForSubject(page, target!.subject_key as string);
  await expect(again).toHaveAttribute("data-status", "stale", { timeout: 20_000 });
  await expect(again.getByTestId("rv-accepted-pill")).toHaveCount(0);
  // The loudest thing on the screen, and true this time: one acceptance really does rest on
  // figures that have changed since.
  await expect(page.getByTestId("rv-stale-strip")).toContainText("1", { timeout: 15_000 });
  await again.click();
  const block = again.getByTestId("rv-stale");
  await expect(block).toBeVisible();
  await expect(block).toContainText("admin");
  await expect(block).toContainText(reason);
  // The figures AS JUDGED — what the person was looking at when they vouched, not what the card
  // says now. Taken from the server's `accepted_rows` rather than formatted here, because the
  // browser formats no figure; and the moved value must NOT be in this block, or the record would
  // be showing the reader numbers nobody vouched for.
  const judged = stale!.judgement!.accepted_rows;
  expect(judged.length).toBeGreaterThan(0);
  for (const [, value] of judged) await expect(block).toContainText(value);
  await expect(block).not.toContainText("424242");
  // …and the quantity that moved is NAMED, so the reader is not left diffing figures by eye.
  expect(stale!.judgement!.changed.length).toBeGreaterThan(0);
  await expect(block).toContainText(stale!.judgement!.changed_label);
  // Re-judgeable: a stale card is outstanding work somebody can settle again.
  await expect(again.getByTestId("rv-accept")).toBeVisible();

  // --- leave the document as we found it ------------------------------------------------------
  // Revert the figure first: that makes the stored judgement match again (the card reads
  // "accepted"), and withdrawing then leaves the all-open queue this test started from.
  const reverted = await apiSend(page, "DELETE", `/api/v1/documents/${doc}/line-items/${key}`);
  expect(reverted.ok(), `reverting the moved figure → ${reverted.status()}`).toBeTruthy();
  await clearJudgements(page, doc);
  const final = await apiGet<JReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(final.summary.accepted).toBe(0);
  expect(final.summary.stale).toBe(0);
  expect(final.checks.length).toBe(rev.checks.length);
  expect(final.checks.find((c) => c.subject_key === target!.subject_key)!.status).toBe("open");
});

test("the export footer counts the list it heads on both paths, and the invented 148 / 12 are gone",
     async ({ page }) => {
  // FINDING C. The sample project served {"pct": 72, "line_items": 148, "in_review": 12} as
  // literals: 148 line items over 33 rows, 12 in review over 4 findings, and a 72% that was never
  // a ratio of anything. The footer printed two of them under ONE word, "flagged", which also
  // labelled the real path's rows-with-no-mapping-or-a-flag — so deleting the literals was not
  // enough by itself: one label over two quantities is how a stale 12 kept reading as "flagged".
  //
  // Checked against the API for the same dataset rather than against expected text, because the
  // claim is that the two agree. Three things: the served counts ARE the counts of the lists they
  // head, the footer prints those and nothing else, and each path names the quantity it counted.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  await setSampleLoaded(page, true);

  // --- the sample path -----------------------------------------------------------------------
  const proj = await apiGet<{ project: { progress: { line_items: number; in_review: number;
                                                     pct?: number } } }>(
    page, "/api/v1/projects/demo");
  const prog = proj.project.progress;
  // `pct` is gone rather than derived: "how far through the workflow is this project" has no
  // source in the sample at all. Absent, not zero — a 0 would have been drawn as an empty
  // progress bar and announced as "0%" over a project with mapped line items.
  expect(prog.pct).toBeUndefined();

  // Both served counts, against the lists they describe. The line items are the item rows of the
  // statements the sample serves; the backlog is the review route's own open count, so the Export
  // footer and the Review header cannot state different numbers for one seeded dataset.
  let items = 0;
  for (const s of ["balance_sheet", "profit_and_loss", "cash_flow"]) {
    const stmt = await apiGet<{ rows: { kind?: string }[] }>(
      page, `/api/v1/projects/demo/statements/${s}?basis=consolidated&locale=en`);
    items += stmt.rows.filter((r) => r.kind === "item").length;
  }
  const demoReview = await apiGet<JReview>(page, "/api/v1/projects/demo/review?locale=en");
  expect(items).toBeGreaterThan(0);
  expect(prog.line_items).toBe(items);
  expect(prog.in_review).toBe(demoReview.summary.open);

  await page.goto("/export", DCL);
  await expect(page.getByRole("heading", { name: "Export" })).toBeVisible({ timeout: 15_000 });
  const footer = page.getByTestId("e-footer-counts");
  // Wait for the footer to have RESOLVED its query, not merely to exist: it renders empty while
  // the project payload is in flight — a pending request is not an empty project — and reading it
  // then would compare the API against nothing.
  await expect(footer).not.toBeEmpty({ timeout: 20_000 });
  await expect(footer).toContainText(`${prog.line_items} line items`);
  await expect(footer).toContainText(`${prog.in_review} in review`);
  const sampleText = (await footer.innerText()).trim();
  // The literals themselves, named. The equality assertions above are the load-bearing ones;
  // these are here because these exact numbers are what shipped, over these exact lists.
  expect(sampleText).not.toMatch(/\b148\b/);
  expect(sampleText).not.toMatch(/\b12\b/);
  expect(sampleText).not.toContain("%");
  // "flagged" belongs to the OTHER path's quantity: one word over two counts is the defect.
  expect(sampleText).not.toMatch(/flagged/i);
  // …and the 72% is gone from the shell too, which rendered `pct ?? 0` beside a progress bar.
  await expect(page.getByText("72%")).toHaveCount(0);

  // The same number on the screen that owns it: the Review header's open counter, over the same
  // seeded dataset. Two screens stating different backlogs for one project is what "12 in review"
  // above 4 findings was.
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("rv-open")).toContainText(String(prog.in_review),
                                                         { timeout: 15_000 });

  // --- the real path -------------------------------------------------------------------------
  // Same footer, a different quantity, and it now says which: the rows this export would carry
  // with no canonical mapping or an extraction flag. Derived here from the run itself, so the
  // label and the number are both checked against the payload the preview above them renders.
  const doc = await extractFixture(page, "sample.pdf");
  const run = await apiGet<{ result: { rows: { canonical_key?: string | null;
                                               flags?: string[] }[] } }>(
    page, `/api/v1/documents/${doc}/run`);
  const rows = run.result.rows;
  const unmappedOrFlagged =
    rows.filter((r) => !r.canonical_key || (r.flags?.length ?? 0) > 0).length;
  expect(rows.length).toBeGreaterThan(0);

  await page.goto("/export", DCL);
  await expect(page.getByRole("heading", { name: "Export" })).toBeVisible({ timeout: 15_000 });
  await expect(footer).not.toBeEmpty({ timeout: 20_000 });
  // "LINES", not "line items" — the real path's own population. `rows` is what the run produced,
  // the subtotals the mapper promotes included, which is a WIDER set than the line items a template
  // declares and than the item rows the sample counts on the other path above (2331). One word over
  // both was the second half of this finding: the number was right and the label claimed a
  // population it had not counted. This assertion is what the rename was missing — it still demanded
  // "line items" here after the footer had been corrected, so the suite contradicted the fix.
  await expect(footer).toContainText(`${rows.length} lines`);
  await expect(footer).toContainText(`${unmappedOrFlagged} unmapped or flagged`);
  const realText = (await footer.innerText()).trim();
  // …and it must not ALSO say "line items", or the two populations have converged again under one
  // word, which is the state this test exists to keep out.
  expect(realText).not.toMatch(/line items/i);
  expect(realText).not.toMatch(/\b148\b/);
  expect(realText).not.toContain("%");
  // The sample's backlog is not this path's quantity, so its label must not appear here either.
  expect(realText).not.toMatch(/in review/i);
});

/* ===========================================================================================
 * ROUND 2. The same rule broken in five more places: a card that speaks for a SET while
 * fingerprinting one member of it, an identity a figure can move, and a stored acceptance no
 * control could take back.
 *
 * Helpers first again — appended, because the suite is serial and nothing above needs them.
 * =========================================================================================== */

/** One review check, plus the fields this pass needs on top of `JCheck`.
 *
 * Spelled out rather than left implicit, so a field the server stops sending fails on this line
 * instead of arriving as `undefined` and quietly satisfying a comparison below. `names` — the
 * extracted lines a card indicts — is new in this pass, and is what makes the header's third tile
 * derivable in ONE place: the count of lines no finding names. The presentation fields are here
 * because the two doctored payloads below must model a card the real builder would emit, headline
 * and all: a fixture that hand-builds a shape the producer never sends reports coverage that does
 * not exist, which is what round 2 found the round-1 guard test doing. */
interface NCheck extends JCheck {
  names: string[];
  icon: string;
  severity: string;
  tone: string;
  target: string;
  fix: string;
}
interface NReview {
  run_id: string;
  checks: NCheck[];
  tabs: { label: string; count: number; types: string[] | null }[];
  summary: { open: number; accepted: number; stale: number; conflict: number; passed: number };
  judgements: { orphaned: unknown[] };
}

/** The geometry the API serves for one figure. `label_bbox` is the row CAPTION's box and `bbox`
 *  the VALUE word's; the judgement anchor must be derived from the first and never the second. */
interface NBox { x0: number; y0: number; x1: number; y1: number }
interface NProv {
  source_kind?: string; page_index?: number; sheet?: string | null; cell?: string | null;
  bbox?: NBox | null; label_bbox?: NBox | null;
}
interface NRunRow {
  source_label: string; canonical_key: string | null;
  // What the extractor decided this row IS — line / subtotal / total / header / spacer. Spelled here
  // because the review header's third tile counts LINES, and the caption roles are the only rows out
  // of that population (services/review_lines.py). Optional so a payload predating the field reads
  // as "not a caption" rather than failing the cast, which is also how the server treats it.
  role?: string | null;
  values: { basis: string; period_label: string; value: string | null;
            provenance: NProv | null }[];
}
interface NRun {
  result: { rows: NRunRow[]; reconciliation?: unknown[] };
}

/** A figure as the digest sees it: "12,048" and "(1,240)" are the SCREEN's spellings of 12048 and
 *  -1240, and the evidence carries numbers. Comparing the two without this would report every
 *  formatted figure as un-fingerprinted, which is the opposite of the defect being hunted. */
function figureText(s: string): string {
  return s.trim().replace(/,/g, "").replace(/^\((.*)\)$/, "-$1");
}

/** Every figure inside one check's `evidence`, flattened — the set the digest is taken over.
 *
 * Recurses into nested objects because a set-shaped finding puts its members THERE: a
 * calculated_mismatch carries `components: {key: value}` and a note tie carries
 * `entries: {face line: "face / residual"}`. A flattener that stopped at the top level would call
 * the note_tie HIGH fixed while the card still printed rows no digest covered. Keys are not
 * collected: a canonical key is a name, not a figure. */
function evidenceFigures(ev: Record<string, unknown>): Set<string> {
  const out = new Set<string>();
  const add = (v: unknown) => {
    if (v === null || v === undefined) return;
    if (typeof v === "number") { out.add(figureText(String(v))); return; }
    if (typeof v === "string") { out.add(figureText(v)); return; }
    if (typeof v === "boolean") { out.add(String(v)); return; }
    if (Array.isArray(v)) { v.forEach(add); return; }
    Object.values(v as Record<string, unknown>).forEach(add);
  };
  Object.values(ev).forEach(add);
  return out;
}

test("every figure a card prints is fingerprinted, and nothing a figure can move is part of its identity",
     async ({ page }) => {
  // ROUND 2's RULE, held over every card this browser can raise, rather than over the five
  // instances two rounds of review happened to catch. Three things, and each of the five was one
  // of them:
  //
  //  I2. Every figure the card PRINTS is inside the evidence the digest is taken over. A
  //      reviewer's "I looked at these and they stand" is a statement about the whole card, so a
  //      printed figure outside the digest is one the acceptance keeps covering silently after it
  //      moves. That is the note_tie HIGH (documents.py:1094): reconciliation holds one untied
  //      entry per FACE LINE, the card printed the first one, and the evidence carried the first
  //      one — so a reviewer who accepted "out by 20; the note rounds" kept an 'accepted' card
  //      while two further face lines on the same note went out by 2,000,000 and 900,000,000,
  //      evidence byte-identical, both breaks dropping out of summary.open.
  //  I3. No subject value IS a figure. Evidence moving means "stale — come look again". A subject
  //      moving means "different finding", which Review.tsx captions "The findings they were
  //      recorded against were corrected, or are no longer raised." A figure that can move an
  //      identity therefore reports a still-failing finding as fixed.
  //  I3, geometrically, for the two row-shaped types the whole layer was built around: the anchor
  //      is the row LABEL's box and never the figure's (extractions.py:103). "Cash and cash
  //      equivalents" printed 1,204 gave the value box x0 ≈ 798/1000 and printed 12,048 gave 789 —
  //      right-aligned figures grow leftwards — so the subject changed with the digit count and an
  //      acceptance ORPHANED where it should have gone stale.
  //
  // A one-figure card satisfies all of this trivially, and sample.pdf raises exactly one finding,
  // so the run is first given a SET-shaped card to sweep: one edit to a component of a printed
  // total raises a calculated_mismatch that prints a row per component. Reverted at the end.
  test.setTimeout(240_000);
  // Admin: uploading needs documents:manage, editing needs extraction:edit, judging needs
  // review:resolve, and no other single role holds all three.
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);

  const runPayload = await apiGet<NRun>(page, `/api/v1/documents/${doc}/run`);
  const rows = runPayload.result.rows;
  expect(rows.length, "the fixture must extract line items to sweep").toBeGreaterThan(0);
  const before = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);

  // --- give the sweep a SET card -------------------------------------------------------------
  // A mapped row with no finding against it: moving it breaks the arithmetic of whichever
  // calculated line it feeds, which is what raises a card carrying one row per component.
  const withFinding = new Set(before.checks.map((c) => c.title));
  const editable = rows.find((r) => !!r.canonical_key && !withFinding.has(r.source_label));
  expect(editable, "the fixture must extract a mapped line whose figure can be moved").toBeTruthy();
  const editKey = editable!.canonical_key as string;
  const moved = await apiSend(page, "PATCH", `/api/v1/documents/${doc}/line-items/${editKey}`,
                             { value: 555555, formula: "", basis: "consolidated",
                               period: "current", comment: "e2e: break a printed subtotal" });
  expect(moved.ok(), `moving ${editKey} → ${moved.status()}`).toBeTruthy();

  const rev = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const setCard = rev.checks.find((c) => c.type === "calculated_mismatch");
  // Stated as a failure, not skipped: a sweep whose only subject is a single-figure card reports
  // coverage of the set case that it does not have — which is the exact criticism round 2 made of
  // the round-1 guard test.
  expect(setCard, "the edit must raise a set-shaped finding (a calculated line whose components no "
                  + "longer come to the printed figure), or this sweep covers no set at all")
    .toBeTruthy();
  const components = (setCard!.evidence.components ?? {}) as Record<string, unknown>;
  expect(Object.keys(components).length,
         "the set card must carry more than one member, or 'the whole set' is one figure")
    .toBeGreaterThan(1);
  // Every member is PRINTED, not just fingerprinted: the note_tie defect was a card that printed a
  // set and hashed one member, and this is the same shape from the other side.
  for (const value of Object.values(components)) {
    expect(setCard!.calc.map(([, v]) => figureText(v)),
           `the set card fingerprints ${value} without printing it`)
      .toContain(figureText(String(value)));
  }

  // --- I2 and I3, over every card the run serves ---------------------------------------------
  // The one exception is counted rather than waved through, and asserted to be that one exception:
  // the EXACT confidence percentage a low-confidence card prints (its Confidence row and the delta
  // that repeats it).
  //
  // ROUND 3's ITEM 3 REWROTE WHY. This block used to defend the mapping's strength and method being
  // left out of the digest altogether, as churn defence — "fingerprinting them would re-open a
  // confirmed concept because a re-run scored 0.42 instead of 0.41". Half right, wrong conclusion: a
  // low-confidence finding IS a statement about the mapping, and with only `value` fingerprinted a
  // reviewer's "41% fuzzy — checked p.42, the concept is right" kept an 'accepted' card over the same
  // label re-scored 0.02 by 'llm', digest byte-identical, changed == []. The churn worry is answered
  // by QUANTIZING, the way `_prov_anchor` answered it for geometry: the digest carries the printed
  // percentage's 10-point BAND and the method EXACTLY (a method change is not jitter). So the
  // exception below is a quantization, not an omission, and it is checked as one — the band the
  // printed figure falls in has to be in the evidence, and so has the method.
  const exceptions: string[] = [];
  for (const c of rev.checks) {
    const printed: [string, string][] = c.calc.map(([label, value]) => [label, value]);
    // The header's own figure, which the reader sees before expanding anything.
    if (/\d/.test(c.delta)) printed.push(["(the card's delta)", c.delta]);
    const fingerprinted = evidenceFigures(c.evidence);
    for (const [label, value] of printed) {
      if (!/\d/.test(value)) continue;                 // a caption or a concept key, not a figure
      if (fingerprinted.has(figureText(value))) continue;
      exceptions.push(`${c.type} · ${label} = ${value}`);
      expect(c.type, `${c.id}: prints ${value} under "${label}" and the digest does not cover it, `
                     + "so an acceptance survives it moving").toBe("low_confidence");
      expect(label === "Confidence" || label === "(the card's delta)",
             `${c.id}: the only figure allowed outside a low-confidence digest is the confidence `
             + "percentage — its Confidence row and the delta that repeats it").toBeTruthy();
      expect(value.endsWith("%"),
             `${c.id}: "${label}" is not the confidence percentage`).toBeTruthy();
      // THE QUANTIZATION, asserted where the omission used to be excused. The exact figure may sit
      // outside the digest only because the BAND it falls in is inside it: 41% and 44% are one band,
      // so jitter leaves the acceptance standing, while a collapse to 2% is four bands away and
      // withdraws it.
      const ev = c.evidence as { confidence_band?: unknown; method?: unknown };
      const band = String(ev.confidence_band ?? "");
      expect(band, `${c.id}: prints ${value} and the digest carries no confidence band, so a mapping `
                   + "that collapses keeps an acceptance nobody re-made").toMatch(/^\d+-\d+%$/);
      const [lo, hi] = band.replace("%", "").split("-").map(Number);
      const printedPct = Number(value.replace("%", ""));
      expect(printedPct >= lo && printedPct <= hi,
             `${c.id}: prints ${value} over a ${band} band — the digest is quantizing a different `
             + "figure from the one on the card").toBeTruthy();
    }
    // …and the METHOD, which is not quantized at all: 'fuzzy' and 'llm' are different kinds of
    // evidence for the same claim, so a reviewer who accepted a fuzzy alias match has not thereby
    // accepted a model's guess. It carries no digit, so the sweep above skips it — and it is checked
    // against what the card PRINTS, so a card printing no method ("—") is asked for nothing.
    if (c.type === "low_confidence") {
      const row = c.calc.find(([label]) => label === "Method");
      const printedMethod = row ? row[1] : "";
      if (printedMethod && printedMethod !== "—") {
        expect(String((c.evidence as { method?: unknown }).method ?? ""),
               `${c.id}: prints "Method ${printedMethod}" and the digest does not carry it — a `
               + "method change is not jitter, and an acceptance must not survive one")
          .toBe(printedMethod);
      }
    }
    // I3: no value the identity is built from is a figure the card prints. Whole-value equality,
    // not containment — a coordinate bucket that happens to share digits with a figure is not a
    // dependency, and a test that failed on that coincidence would be worse than none.
    for (const sv of Object.values(c.subject).map(String)) {
      for (const [label, value] of printed) {
        if (!/\d/.test(value)) continue;
        expect(figureText(sv), `${c.id}: the identity carries ${value} ("${label}"), so moving that `
                               + "figure makes this a DIFFERENT finding and the old acceptance is "
                               + "reported as corrected").not.toBe(figureText(value));
      }
    }
  }
  // Recorded so a reviewer can see WHICH figures sit outside a digest today: the confidence
  // percentage, on low-confidence cards, and nothing else.
  expect(exceptions.every((e) => e.startsWith("low_confidence")), exceptions.join(" | "))
    .toBeTruthy();

  // --- the anchor is the caption's geometry, not the figure's ---------------------------------
  let anchored = 0;
  for (const c of rev.checks) {
    const m = /^chk-(unmapped|lowconf)-(\d+)$/.exec(c.id);
    if (!m) continue;
    // The index in the id is a RENDER key that the backend documents as one; it is used here only
    // to reach the row, and the label cross-check below is what proves it reached the right one.
    const row = rows[Number(m[2])];
    expect(row, `${c.id}: names a row position this run does not have`).toBeTruthy();
    expect(c.title).toBe(row.source_label);
    const prov = row.values[0]?.provenance ?? null;
    expect(prov, `${c.id}: a native PDF row with no provenance at all`).toBeTruthy();
    const label = prov!.label_bbox ?? null;
    const box = prov!.bbox ?? null;
    // The serializer half of the fix. `_prov_dict` emitted only `bbox`, so the one box that cannot
    // move with the figure never reached the anchor — and `_prov_anchor`'s docstring claimed it
    // used the label geometry all the same.
    expect(label, "the API serves no label_bbox, so the anchor cannot be value-independent")
      .toBeTruthy();
    expect(box, "the API serves no value bbox for a native PDF row").toBeTruthy();
    // The premise of the assertion after next, asserted rather than assumed: on this filing the
    // caption and the right-aligned figure are printed in different places, so "the anchor follows
    // the label" and "the anchor follows the figure" are distinguishable outcomes.
    expect(Math.abs(label!.x0 - box!.x0) * 1000,
           "caption and figure share a left edge, so this row cannot tell the two anchors apart")
      .toBeGreaterThan(1);

    const anchor = String(c.subject.anchor ?? "");
    const page0 = prov!.page_index ?? 0;
    expect(anchor, `${c.id}: the anchor is not label geometry on page ${page0}`)
      .toMatch(new RegExp(`^p${page0}#l`));
    const nums = anchor.slice(anchor.indexOf("#l") + 2).split("/").map(Number);
    expect(nums.length, `${c.id}: ${anchor} is not four coordinates`).toBe(4);
    const want = [label!.x0, label!.y0, label!.x1, label!.y1].map((v) => v * 1000);
    // ±1 bucket, because the server rounds in Python (banker's rounding at exact halves) and this
    // does not: the claim under test is WHICH box the anchor is taken from, not how .5 is broken.
    for (let k = 0; k < 4; k++) {
      expect(Math.abs(nums[k] - want[k]),
             `${c.id}: anchor coordinate ${k} is ${nums[k]}, label_bbox says ${want[k]}`)
        .toBeLessThanOrEqual(1);
    }
    // …and demonstrably NOT the figure's box. This is the assertion that fails with the shipped
    // anchor restored: the value box's left edge is where p0#b798/… came from.
    expect(Math.abs(nums[0] - box!.x0 * 1000),
           `${c.id}: the anchor's left edge is the FIGURE's, so a re-priced line is a new identity`)
      .toBeGreaterThan(1);
    anchored++;
  }
  expect(anchored, "the fixture must raise at least one unmapped or low-confidence finding — the "
                   + "two types this anchor exists for").toBeGreaterThan(0);

  // --- the set card, on the screen ------------------------------------------------------------
  // The API half above says the payload covers the set; this says the reader sees it. FigureRows
  // renders `calc` verbatim, so every component figure is on the card or the card is summarising.
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const card = cardForSubject(page, setCard!.subject_key as string);
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await card.click();
  for (const [labelText, value] of setCard!.calc) {
    await expect(card).toContainText(value.trim() === "" ? labelText : value);
  }

  // --- leave the document as we found it ------------------------------------------------------
  const reverted = await apiSend(page, "DELETE", `/api/v1/documents/${doc}/line-items/${editKey}`);
  expect(reverted.ok(), `reverting ${editKey} → ${reverted.status()}`).toBeTruthy();
  const final = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(final.checks.length).toBe(before.checks.length);
  expect(final.summary).toEqual(before.summary);
});

test("a note that does not tie prints EVERY face line it failed to tie, and a grown break reads stale",
     async ({ page }) => {
  // THE ROUND-2 HIGH (documents.py:1094), held where a browser can hold it — and the part it
  // cannot is said out loud rather than asserted around.
  //
  // WHAT THE FIXTURES CANNOT DO. A note tie needs a note→face reconciliation entry, which needs an
  // extracted NOTE TABLE. None of the three fixtures has one: sample.pdf prints "Note 14"/"Note 15"
  // as citations in the label column and no note pages at all, and comparative.pdf and sample.xlsx
  // cite none — every run of all three serves an EMPTY reconciliation, asserted below rather than
  // assumed. So the server-side half of the fix — that a second untied face line on one note moves
  // the evidence digest, so the card reads 'stale' and not 'accepted' — cannot be driven from this
  // browser, and is proved in the backend suite instead
  // (test_review_judgement.py::test_a_further_untied_face_line_on_one_note_moves_the_digest_and_reads_stale).
  // It is in this file's left_undone, not silently downgraded here.
  //
  // WHAT ONLY A BROWSER CAN ANSWER, and what nothing answered before: whether the reader SEES the
  // whole set. The reviewers' reproduction was a card printing "Face figure 1,000 / Residual vs
  // note total 20" where face-b's 40 "appears nowhere" — and after the regression, two breaks of
  // 2,000,000 and 900,000,000 on the same note appeared nowhere either, on a card still labelled
  // accepted. So the queue read is doctored to serve one note_tie card in the shape the fixed
  // builder emits — every untied face line in `entries`, the count and the summed residual beside
  // them, `subject` naming only the NOTE — with a judgement made when the break was 20 and a
  // status of 'stale'. The card and the evidence are generated from ONE list here, exactly as
  // `_note_tie_entries` generates both from one group, so this fixture cannot print a figure its
  // own evidence lacks.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);

  // The fixture limit, asserted: no note ties are reachable, so nothing below is being served in
  // place of a real one that this suite could have driven.
  const runPayload = await apiGet<NRun>(page, `/api/v1/documents/${doc}/run`);
  expect((runPayload.result.reconciliation ?? []).length,
         "this fixture now reconciles notes to the face — a real note_tie finding is reachable and "
         + "the growth case should be driven for real instead of modelled here").toBe(0);
  const real = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(real.checks.filter((c) => c.type === "note_tie").length).toBe(0);
  const base = real.checks.find((c) => !!c.subject_key);
  expect(base, "the fixture must raise a card to model the note tie on").toBeTruthy();

  // The untied set, ONE list. Both the evidence and the printed rows are derived from it below.
  //
  // ROUND 3's ITEM 4 IS MODELLED INTO IT: the two nine-figure breaks are in OPPOSITE directions, and
  // a per-entry residual is SIGNED because the direction is the truth about that line. The summary
  // figure the card leads with is therefore the sum of their MAGNITUDES, not their signed sum —
  // `residual = raw_face - note_total` is signed while TIE_UNTIED only bounds abs(residual), so these
  // three sum to −897,999,980 signed while the note is out by 902,000,020 across the three lines —
  // and a note broken +2,000,000 on one line and −2,000,000 on another summed to exactly 0, serving
  // "does not tie" over a delta of '0'. A card whose at-a-glance figure can cancel is the defect; the
  // label says magnitudes so no reader takes the summary for a residual.
  const ENTRIES: [string, string][] = [
    ["bs_current_assets__trade_receivables", "1,000 / 20"],
    ["bs_current_assets__other_receivables", "2,400,000 / 2,000,000"],
    ["bs_current_assets__prepayments", "912,000,000 / -900,000,000"],
  ];
  const TOTAL = "902,000,020";                 // |20| + |2,000,000| + |−900,000,000|
  const SIGNED = "-897,999,980";               // what the old summary figure would have printed
  const SUBJECT = "e2e-note-tie-12-consolidated-current";
  const ACTOR = "e2e-earlier-reviewer";
  const REASON = "Out by 20; the note rounds. Immaterial.";
  const BREAK_ROW = "Total break across the untied face lines";
  // The quantities that moved, named the way `_changed_label` names them: the localized label of each
  // changed EVIDENCE KEY, comma-joined. Spelled as the producer spells it — a fixture whose wording
  // the server never sends reports coverage of a sentence nobody reads.
  const CHANGED = ["Face figure / residual vs note total", "Face lines that do not tie", BREAK_ROW]
    .join(", ");
  // What the reviewer was looking at: ONE face line, out by 20. The two nine-figure breaks came
  // later, and this block must not show them as though anyone had vouched for them.
  const AS_JUDGED: [string, string][] = [
    ["Face lines that do not tie", "1"],
    [BREAK_ROW, "20"],
    ["bs_current_assets__trade_receivables", "1,000 / 20"],
  ];

  await page.route("**/api/v1/documents/*/review*", async (route) => {
    // The queue READ only: `*` does not cross a "/", so nothing under /review/judgements/ is
    // matched and any control the screen offers would reach the real endpoint.
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const body = (await res.json()) as NReview;
    const tie: NCheck = {
      ...(base as NCheck),
      id: "chk-note-12-consolidated-current",
      type: "note_tie",
      icon: "≠",
      title: "Note does not tie to the face figure",
      where: "Note 12 · consolidated/current",
      severity: "Check failed",
      tone: "high",
      target: "note:12",
      fix: "The note's detail rows do not sum to the face figure(s) it supports. Verify the note "
           + "breakdown and each face value listed.",
      // The TOTAL BREAK — the sum of the residuals' magnitudes — never the first entry's residual
      // passed off as the note's (which is what "Residual vs note total 20" said over a second face
      // line out by 2,000,000), and never their signed sum (which two breaks in opposite directions
      // cancel to nothing under a card titled "does not tie").
      delta: TOTAL,
      // The identity names the NOTE and nothing about which face lines broke — that is the fix's
      // choice: one question per note, membership in the evidence, so a set that GROWS reads stale
      // instead of orphaning the acceptance and captioning a nine-figure break as corrected.
      subject: { k: "note_tie", note: "12", basis: "consolidated", period: "current" },
      subject_key: SUBJECT,
      // `total_break`, spelled the way the builder spells it: ONE quantity, ONE name, in the digest,
      // on the card and in the accepted-figures panel. It was `residual` over a signed sum, so the
      // fingerprint's own summary cancelled exactly as the card's did.
      evidence: { entries: Object.fromEntries(ENTRIES), entry_count: ENTRIES.length,
                  total_break: 902000020 },
      evidence_digest: "e2e-digest-note-12-three-untied-face-lines",
      calc: [["Face lines that do not tie", String(ENTRIES.length), false],
             [BREAK_ROW, TOTAL, true],
             ...ENTRIES.map(([l, v]) => [l, v, false] as [string, string, boolean])],
      status: "stale",
      conflict: false, conflict_count: 0, conflict_note: "", judgement_withheld: false,
      ambiguous: false, ambiguous_count: 0,
      names: ENTRIES.map(([k]) => k),
      judgement: { actor: ACTOR, reason: REASON, at: "2026-01-01T09:00:00",
                   changed: ["entries", "entry_count", "total_break"], changed_label: CHANGED,
                   accepted_rows: AS_JUDGED },
    };
    // Stale ranks above open, which is where the server puts it: somebody vouched for figures that
    // have since moved, which is more urgent than a finding nobody has looked at.
    body.checks = [tie, ...body.checks];
    body.summary = { ...body.summary, stale: 1, open: body.summary.open + 1 };
    body.tabs = body.tabs.map((tb) => {
      if (tb.types === null) return { ...tb, count: tb.count + 1 };   // the everything tab
      // The accounting tab, which is where a note tie is counted. This fixture's run raises no
      // accounting finding at all, so that tab's type list is empty — and leaving it that way would
      // put a card in the list that no chip counts, i.e. a chip whose number is no longer the length
      // of the list clicking it produces. That is a defect the suite asserts against elsewhere, and
      // a fixture must not introduce it to prove something else.
      //
      // Identified by ELIMINATION of the row-shaped tabs rather than by "the one that is not
      // unmapped or low_confidence": the queue gained a third row-shaped tab this round (Off
      // template), and that older test would have added note_tie to it as well — two chips selecting
      // one card, which is the very partition break the paragraph above refuses to introduce.
      if (tb.types.some((ty) => ROW_SHAPED_TYPES.has(ty))) return tb;
      return { ...tb, types: [...tb.types, "note_tie"], count: tb.count + 1 };
    });
    await route.fulfill({ response: res, json: body });
  });

  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const cards = page.getByTestId("rv-check");
  await expect(cards).toHaveCount(real.checks.length + 1, { timeout: 20_000 });
  const tie = cardForSubject(page, SUBJECT);
  // Wait for the STATE: the card renders while its query is in flight and data-status read then is
  // null. NOT 'accepted' — this is the whole point. The shipped builder kept this card accepted
  // through both regressions because its evidence never mentioned the other face lines.
  await expect(tie).toHaveAttribute("data-status", "stale", { timeout: 20_000 });
  await expect(tie.getByTestId("rv-accepted-pill")).toHaveCount(0);
  await expect(page.getByTestId("rv-stale-strip")).toContainText("1", { timeout: 15_000 });

  await tie.click();
  // EVERY face line, with its own face figure and its own residual. This is the assertion the
  // reviewers' reproduction fails: face-b's 40 "appears nowhere", and after the regression neither
  // does a 2,000,000 nor a 900,000,000 break on the same note.
  for (const [faceLine, figures] of ENTRIES) {
    await expect(tie).toContainText(faceLine);
    await expect(tie).toContainText(figures);
  }
  // …and the two summary rows the card leads with, which are what stop the FIRST entry's residual
  // being read as the note's: how many face lines failed, and the total they are out by.
  await expect(tie).toContainText(String(ENTRIES.length));
  await expect(tie).toContainText(TOTAL);
  // ITEM 4, where a reader meets it: the summary is the total BREAK, and the figure two opposite
  // residuals would have summed to is nowhere on the card. With the signed sum restored this is what
  // a note out by 2,000,000 one way and 2,000,000 the other prints under "does not tie": nothing.
  await expect(tie).not.toContainText(SIGNED);

  // The record: who vouched, for what, and the figures AS JUDGED — one face line, out by 20.
  const stale = tie.getByTestId("rv-stale");
  await expect(stale).toBeVisible();
  await expect(stale).toContainText(ACTOR);
  await expect(stale).toContainText(REASON);
  // The quantities that moved are NAMED, so the reader is not left diffing the block against the
  // card by eye.
  await expect(stale).toContainText(CHANGED);
  for (const [, value] of AS_JUDGED) await expect(stale).toContainText(value);
  // Nobody vouched for the two later breaks, so the record must not print them as though somebody
  // had. Scoped to the block: the card above legitimately shows them.
  await expect(stale).not.toContainText("2,000,000");
  await expect(stale).not.toContainText("900,000,000");
  // Outstanding work, so it is judgeable again — a reviewer can look at the grown set and settle it.
  await expect(tie.getByTestId("rv-accept")).toBeVisible();
  await expect(tie.getByTestId("rv-reason")).toBeVisible();

  // Nothing was stored: the real queue is exactly as it was, and the doctored card leaves no trace.
  await page.unroute("**/api/v1/documents/*/review*");
  const untouched = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(untouched.summary.stale).toBe(0);
  expect(untouched.summary.accepted).toBe(0);
  expect(untouched.judgements.orphaned).toEqual([]);
  expect(untouched.checks.length).toBe(real.checks.length);
  await page.reload(DCL);
  await expect(page.getByTestId("rv-check")).toHaveCount(real.checks.length, { timeout: 20_000 });
  await expect(page.getByTestId("rv-stale")).toHaveCount(0);
});

test("a WITHHELD acceptance can be withdrawn: the control is on the card that shows no verdict, and it works",
     async ({ page }) => {
  // FINDING 5. `withdraw_review_judgement` (documents.py:2054) deliberately PERMITS withdrawal on a
  // conflicted subject — its comment calls that "precisely the one a reviewer needs to be able to
  // take back" — while Review.tsx:693 gated the button on `judgeable && canResolve && accepted`,
  // and BOTH `judgeable` and `accepted` are false on a conflict card. So a real, named acceptance
  // stood with no control anywhere able to remove it: accept a lone "Others 1,234", let a second
  // "Others 5,678" appear on the same page, and both cards go conflict/judgement_withheld with the
  // row stuck in force. One run later the surviving card is served 'stale' carrying the original
  // actor, reason and figures at rank 0 — a verdict on figures that reviewer never saw, and finding
  // A's exact rendered outcome, reached because the row could never be withdrawn.
  //
  // The ACCEPTANCE and the WITHDRAWAL here are real: a real POST stores a real judgement against a
  // real finding, and a real DELETE takes it out of force. Only the queue's CLASSIFICATION is
  // doctored — the two cards the server would serve for a collision the fixtures cannot produce
  // (the source anchor keeps two printed lines apart, and no fixture prints two identically
  // captioned lines). What the server does with a conflicted subject is backend-tested; what only
  // a browser can answer is whether the reviewer is given a control at all, and whether pressing it
  // reaches the endpoint and leaves nothing behind.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const real = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const target = real.checks.find((c) => c.status === "open" && !!c.subject_key);
  expect(target, "the fixture must raise a judgeable finding to accept for real").toBeTruthy();
  const KEY = target!.subject_key as string;

  // --- a real acceptance ----------------------------------------------------------------------
  const own = cardForSubject(page, KEY);
  await expect(own).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await own.click();
  const reason = `Confirmed against the printed page — e2e withheld ${Date.now()}`;
  await own.getByTestId("rv-reason").fill(reason);
  await own.getByTestId("rv-accept").click();
  await expect(own).toHaveAttribute("data-status", "accepted", { timeout: 20_000 });
  // The control on a card showing its OWN verdict says so in the attribute, which is what makes
  // "withheld" below a discrimination rather than a constant.
  await expect(own.getByTestId("rv-withdraw")).toHaveAttribute("data-withheld", "false");

  // --- the same stored row, now served as a conflict nobody can be given credit for ------------
  const A_FIG = "1,234";
  const B_FIG = "5,678";
  const NOTE = "2 findings here share one identity but printed different figures, so the queue "
             + "cannot tell them apart. None of them can be accepted until the extraction "
             + "distinguishes them. A recorded acceptance for this identity is being withheld.";
  await page.route("**/api/v1/documents/*/review*", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const body = (await res.json()) as NReview;
    const twin = (id: string, figure: string, digest: string): NCheck => ({
      ...(target as NCheck),
      id,
      // The reviewers' own reproduction: two unmapped "Others" lines on one page, which the
      // `#nobox` sentinel — an adapter that reported a page and no geometry — cannot tell apart.
      // Modelled down to the type, severity and delta the real unmapped builder emits, because a
      // fixture whose shape the producer never sends is a test that reports coverage it lacks.
      type: "unmapped",
      icon: "?",
      title: "Others",
      severity: "Unmapped",
      tone: "low",
      target: "Others",
      delta: "—",
      subject: { k: "unmapped", label: "others", anchor: "p0#nobox" },
      subject_key: KEY,                       // the identity the stored row is pinned to
      evidence: { value: figure },
      evidence_digest: digest,                // differing digests are what make it a CONFLICT
      calc: [["Source label", "Others", false],
             ["Mapped to", "— (no confident match)", true],
             ["Value", figure, false]],
      names: [],
      status: "conflict",
      conflict: true, conflict_count: 2, conflict_note: NOTE,
      // The server serves the stored row as WITHHELD and attaches it to no card: naming a reviewer
      // over figures they may never have seen is the fabrication the conflict state exists to
      // refuse. `judgement_withheld` is the only thing on the payload that says the row exists.
      judgement_withheld: true, judgement: null,
      ambiguous: false, ambiguous_count: 0,
    });
    // Exactly the group: the real card is REPLACED by the two that share its identity, so the
    // payload says what the server would say and not "three findings on one subject".
    body.checks = [twin("chk-e2e-withheld-a", A_FIG, "e2e-withheld-digest-a"),
                   twin("chk-e2e-withheld-b", B_FIG, "e2e-withheld-digest-b"),
                   ...body.checks.filter((c) => c.subject_key !== KEY)];
    body.summary = { ...body.summary, conflict: 2, accepted: 0, stale: 0,
                     open: body.summary.open + 2 };
    // One card left, two arrived, and their type changed — so each chip is adjusted by what
    // happened to ITS list rather than by a single blanket +1. A chip whose number stops being the
    // length of the list it produces is a defect the suite asserts against above; a fixture proving
    // something else must not introduce it.
    body.tabs = body.tabs.map((tb) => {
      if (tb.types === null) return { ...tb, count: tb.count + 1 };
      let n = tb.count;
      if (tb.types.includes("unmapped")) n += 2;
      if (tb.types.includes(target!.type)) n -= 1;
      return { ...tb, count: n };
    });
    await route.fulfill({ response: res, json: body });
  });

  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const group = cardForSubject(page, KEY);
  await expect(group).toHaveCount(2, { timeout: 20_000 });
  // Reached in the ORDER THE PAYLOAD SERVES THEM, because a collision's premise is that the two
  // cards are indistinguishable: same caption, same location, and their differing figures are
  // inside the body, which only the expanded card renders (one card expands at a time). The client
  // does no sorting of its own, so this order is the server's — and each card is identified by the
  // figure it prints once opened, asserted below rather than assumed.
  const cardA = group.nth(0);
  const cardB = group.nth(1);
  await expect(cardA).toHaveAttribute("data-status", "conflict", { timeout: 15_000 });
  await expect(cardB).toHaveAttribute("data-status", "conflict", { timeout: 15_000 });

  await cardA.click();
  await expect(cardA).toContainText(A_FIG);
  await expect(cardA).not.toContainText(B_FIG);
  // No verdict and no acceptance path — that part was already right, and it is asserted here so
  // "the withdraw button exists" cannot be satisfied by a build that simply stopped withholding.
  await expect(cardA.getByTestId("rv-judgement")).toHaveCount(0);
  await expect(cardA.getByTestId("rv-judged-by")).toHaveCount(0);
  await expect(cardA.getByTestId("rv-accept")).toHaveCount(0);
  await expect(cardA.getByTestId("rv-reason")).toHaveCount(0);
  await expect(cardA.getByTestId("rv-conflict-message")).toHaveText(NOTE);
  // THE CONTROL THAT DID NOT EXIST. Gated on the one condition the DELETE has — a stored row in
  // force on this subject — and it says which of the two withdrawable shapes it stands for.
  const withdrawA = cardA.getByTestId("rv-withdraw");
  await expect(withdrawA).toHaveAttribute("data-withheld", "true", { timeout: 15_000 });
  await expect(withdrawA).toBeEnabled();
  // …and a sentence beside it, because the card displays no acceptance: without one, "Withdraw
  // acceptance" under a card showing no verdict reads as a button that does nothing, which is how
  // it came to be hidden in the first place.
  await expect(cardA.getByTestId("rv-withheld-withdrawable")).toBeVisible();

  // --- AND IT WORKS: a real DELETE, against the real endpoint --------------------------------
  await withdrawA.click();
  // The row is gone from the store. Asserted against the SERVER rather than the screen, because
  // the screen is reading a doctored queue that will keep saying "withheld" either way — this is
  // the assertion that separates a control that fires from a control that is merely rendered.
  await expect(async () => {
    const now = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
    expect(now.summary.accepted).toBe(0);
    expect(now.checks.find((c) => c.subject_key === KEY)!.status).toBe("open");
  }).toPass({ timeout: 20_000 });

  // --- the 404 the widened control makes reachable, in words ---------------------------------
  // Two cards share ONE stored row, so the second withdrawal finds nothing. The endpoint answers
  // {"error": "no_judgement"}; before this pass the screen printed the raw
  // `404 Not Found — {"detail":…}` at a reviewer. The error map is keyed on the subject, so one
  // refusal shows once per identity — correctly: it is one row and one refusal, not two that could
  // disagree.
  await cardB.click();
  await expect(cardB).toContainText(B_FIG);
  await expect(cardB.getByTestId("rv-withdraw")).toHaveAttribute("data-withheld", "true",
                                                                { timeout: 15_000 });
  await cardB.getByTestId("rv-withdraw").click();
  await expect(cardB.getByTestId("rv-error")).toContainText(/Nothing was withdrawn/,
                                                           { timeout: 15_000 });
  await expect(cardB.getByTestId("rv-error")).not.toContainText("404");

  // --- leave the queue as we found it --------------------------------------------------------
  await page.unroute("**/api/v1/documents/*/review*");
  const after = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(after.summary.accepted).toBe(0);
  expect(after.summary.conflict).toBe(0);
  expect(after.judgements.orphaned).toEqual([]);
  expect(after.checks.length).toBe(real.checks.length);
  await page.reload(DCL);
  const restored = cardForSubject(page, KEY);
  await expect(restored).toHaveAttribute("data-status", "open", { timeout: 20_000 });
});

/** Rows that are CAPTIONS — a section heading or a spacer — in either producer's vocabulary.
 *
 *  The real extraction says what a row is in `role`, the sample in its display `kind`, and one
 *  predicate over both is the point: the review header's third tile counts LINES, and the two routes
 *  were counting two different populations under that one label. Kept in step with the server's own
 *  definition, services/review_lines.py::_CAPTIONS, which is where the sentence "a subtotal and a
 *  total ARE lines" is argued. */
const CAPTION_KINDS = new Set(["header", "spacer", "section", "subhead"]);

/** Is this row one of the lines the review header's third tile counts?
 *
 *  A SUBTOTAL AND A TOTAL ARE LINES — they are exactly what the balance card names — so the only
 *  rows out of the population are the ones carrying no figure. Reads whichever of `role` / `kind`
 *  the row carries, so the sample path and the real path are asked the same question here rather
 *  than each being checked against its own arithmetic. */
function isStatementLine(row: { role?: string | null; kind?: string | null }): boolean {
  for (const spelling of [row.role, row.kind]) {
    if (spelling && CAPTION_KINDS.has(String(spelling).toLowerCase())) return false;
  }
  return true;
}

test("the third header tile counts the lines the payload says carry no finding, on both paths",
     async ({ page }) => {
  // FINDING 7, and ROUND 3's ITEM 9 — its second half, which this test was written for and did not
  // catch.
  //
  // ROUND 2's HALF. The tile was relabelled from "passed" to "lines with no finding" in all four
  // locales, over `summary.passed` — which was `len(rows) - (unmapped + low_confidence)`. So every
  // line indicted by a balance, note_tie, structural, guard, calculated_mismatch or uncomputed
  // finding counted as having none: the reviewers' 9-row run with 4 checks against 4 of those rows
  // rendered "4 open · 0 accepted · 9 lines with no finding". The number never changed in that
  // relabelling; the old word did not assert WHICH lines it counted and the new one does, and it
  // was false.
  //
  // ROUND 3's HALF, AND WHY THIS TEST USED TO PASS THROUGH IT. The two routes counted two different
  // POPULATIONS under the identical label: the real path every serialized line (subtotals and totals
  // included), the sample path only rows with `kind == "item"` — so the seeded project served 31
  // over its 33 item rows while the same statements also served 6 subtotal and 4 total rows, 8 of
  // which no finding names. The tile understated the quantity its own label names by 8. This test
  // could not catch it because it checked each path against the population THAT path had chosen:
  // it built `itemIds` from `kind === "item"` and asserted the sample tile against that, which is
  // the defect restated as the expectation. One definition is now spelled once, above, and both
  // paths are held to it — and the sample's own item-only count is asserted to be a DIFFERENT
  // number, so the two can never again be confused for one.
  //
  // Both paths are checked against the lines the payload itself names, because the claim is that
  // ONE definition is served under ONE label: `names` — the extracted lines each card indicts,
  // contributed by the builder that knows them — with the tile reading `summary.passed` straight off
  // the payload and recomputing nothing.
  test.setTimeout(240_000);
  await loginAs(page, "admin");

  // --- the sample path, first: it is the one the screen shows while no document is active ------
  await setSampleLoaded(page, true);
  const demo = await apiGet<NReview>(page, "/api/v1/projects/demo/review?locale=en");
  const demoNamed = new Set(demo.checks.flatMap((c) => c.names ?? []));
  expect(demoNamed.size, "the sample's findings must name the lines they indict").toBeGreaterThan(0);
  const itemIds = new Set<string>();                 // the sample's LINE ITEMS — a narrower set …
  const lineIds = new Set<string>();                 // … than the lines the tile's label names
  for (const s of ["balance_sheet", "profit_and_loss", "cash_flow"]) {
    const stmt = await apiGet<{ rows: { id?: string; kind?: string }[] }>(
      page, `/api/v1/projects/demo/statements/${s}?basis=consolidated&locale=en`);
    for (const r of stmt.rows) {
      if (r.kind === "item") itemIds.add(String(r.id));
      if (isStatementLine(r)) lineIds.add(String(r.id));
    }
  }
  expect(itemIds.size).toBeGreaterThan(0);
  const demoNamedItems = [...demoNamed].filter((n) => itemIds.has(n)).length;
  const demoNamedLines = [...demoNamed].filter((n) => lineIds.has(n)).length;
  // The label's own arithmetic: statement lines, less the lines a finding names.
  expect(demo.summary.passed).toBe(lineIds.size - demoNamedLines);
  // THE ASSERTION THAT WOULD HAVE CAUGHT TWO PATHS COUNTING TWO POPULATIONS. The sample's own
  // item-only count is a different number on this very data, so a route that answered with it —
  // which is what this route did, 31 against the real route's inclusive count — fails here instead
  // of agreeing with a test that had adopted its definition.
  expect(lineIds.size,
         "no sample row is a subtotal or a total, so the item-only population and the served one "
         + "cannot be told apart on this data and this test proves nothing about which is served")
    .toBeGreaterThan(itemIds.size);
  expect(demo.summary.passed,
         "the sample tile is answering with its LINE ITEMS while the real path counts every line — "
         + "one label over two populations, understated here by the sample's subtotal and total rows")
    .not.toBe(itemIds.size - demoNamedItems);
  // …and the older definition again: at least one finding names a row that is not a line item at
  // all (a subtotal, a total), so subtracting `len(checks)` removed rows from a population they
  // were never in.
  expect([...demoNamed].some((n) => !itemIds.has(n)),
         "every sample finding names a line item, so `lines - len(checks)` cannot be told apart "
         + "from the served definition here").toBeTruthy();
  expect(demo.summary.passed).not.toBe(itemIds.size - demo.checks.length);

  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const tile = page.getByTestId("rv-passed");
  // Wait for the tile to have RESOLVED its query rather than merely to exist: it renders while the
  // payload is in flight, and a pending request is not a project with no findings.
  await expect(tile).not.toBeEmpty({ timeout: 20_000 });
  await expect(tile).toContainText(String(demo.summary.passed));
  // The words the number now has to be true of. One spelling for one quantity: the payload key
  // (`passed`), the i18n key (`r.passed`) and the testid (`rv-passed`) are the same name.
  await expect(tile).toContainText("lines with no finding");

  // --- the real path --------------------------------------------------------------------------
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const runPayload = await apiGet<NRun>(page, `/api/v1/documents/${doc}/run`);
  const rows = runPayload.result.rows;
  /** The row POSITIONS some served finding names, derived the way the server derives them.
   *
   * Positions, not keys: an unmapped row has no canonical key and two rows can legitimately print
   * one caption, so crediting a finding by caption would name the wrong row. The accounting cards
   * name lines in `names`; the two row-shaped types are about the row they were built FROM, whose
   * position is in the card id (`chk-unmapped-{i}` / `chk-lowconf-{i}`) — documented as a render
   * key, and used here only for that. */
  const indicted = (rev: NReview): Set<number> => {
    const named = new Set(rev.checks.flatMap((c) => c.names ?? []));
    const hit = new Set<number>();
    rows.forEach((r, i) => {
      if ((r.canonical_key && named.has(r.canonical_key))
          || (r.source_label && named.has(r.source_label))) hit.add(i);
    });
    for (const c of rev.checks) {
      const m = /^chk-(unmapped|lowconf)-(\d+)$/.exec(c.id);
      if (m) hit.add(Number(m[2]));
    }
    return hit;
  };
  /** What the tile used to count: rows less the two row-shaped findings, which is what made the
   *  new label false. */
  const oldFormula = (rev: NReview): number =>
    rows.length - rev.checks.filter((c) => c.type === "unmapped" || c.type === "low_confidence")
                            .length;
  /** The lines this run has that no served finding names — THE SAME PREDICATE the sample path was
   *  held to above, asked of the real serializer's spelling (`role`) instead of the sample's `kind`.
   *  Counted per row rather than as one total minus another, which is also what the server does now,
   *  so a caption that somehow attracted a finding cannot push the answer below zero.
   *
   *  On these fixtures every extracted row is a figure-bearing line, so the caption exclusion makes
   *  no difference to the number HERE; what matters is that both paths are asked one question. The
   *  exclusion itself is pinned in the backend suite
   *  (test_review_checks.py::test_the_lines_with_no_finding_tile_counts_subtotals_and_totals_but_not_captions). */
  const passedLines = (rev: NReview): number => {
    const hit = indicted(rev);
    return rows.filter((r, i) => isStatementLine(r) && !hit.has(i)).length;
  };

  const before = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(before.summary.passed).toBe(passedLines(before));
  await expect(tile).toContainText(String(before.summary.passed), { timeout: 20_000 });
  await expect(tile).toContainText("lines with no finding");

  // Now give the run an ACCOUNTING finding — the class the old count ignored. One edit to a
  // component of a printed total raises a calculated_mismatch that names the total and its
  // components, so a line the tile counted as having no finding now has one.
  const editable = rows.find((r) => !!r.canonical_key
                                    && !before.checks.some((c) => c.title === r.source_label));
  expect(editable, "the fixture must extract a mapped line whose figure can be moved").toBeTruthy();
  const editKey = editable!.canonical_key as string;
  const moved = await apiSend(page, "PATCH", `/api/v1/documents/${doc}/line-items/${editKey}`,
                              { value: 555555, formula: "", basis: "consolidated",
                                period: "current", comment: "e2e: indict a line by arithmetic" });
  expect(moved.ok(), `moving ${editKey} → ${moved.status()}`).toBeTruthy();

  // The edit moves a FIGURE. It must not change the row composition, or the positions `indicted`
  // reads out of the card ids would name different lines than the ones they named a moment ago —
  // and the comparison below would be between two different populations.
  const rowsAfter = (await apiGet<NRun>(page, `/api/v1/documents/${doc}/run`)).result.rows;
  expect(rowsAfter.map((r) => r.canonical_key)).toEqual(rows.map((r) => r.canonical_key));

  const after = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const accounting = after.checks.filter((c) => (c.names ?? []).length > 0);
  expect(accounting.length, "the edit must raise a finding that NAMES the lines it indicts")
    .toBeGreaterThan(0);
  const now = indicted(after);
  expect(after.summary.passed).toBe(passedLines(after));
  // THE ASSERTION THAT FAILS WITH THE DEFECT RESTORED. The accounting finding names a row, so the
  // served count must be strictly below the old formula — which is unchanged by it, because no
  // extra row became unmapped or low-confidence.
  expect(now.size).toBeGreaterThan(indicted(before).size);
  expect(after.summary.passed,
         "a line named by an accounting finding is still being counted as having no finding — the "
         + "reviewers' '4 open · 9 lines with no finding'").toBeLessThan(oldFormula(after));

  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await expect(tile).toContainText(String(after.summary.passed), { timeout: 20_000 });
  await expect(tile).toContainText("lines with no finding");
  // The other two tiles still count CARDS, and the three are not one quantity: this is the screen
  // where a line count sits beside a card count, so the one that moved has to be the right one.
  await expect(page.getByTestId("rv-open")).toContainText(String(after.summary.open));

  // --- leave the document as we found it ------------------------------------------------------
  const reverted = await apiSend(page, "DELETE", `/api/v1/documents/${doc}/line-items/${editKey}`);
  expect(reverted.ok(), `reverting ${editKey} → ${reverted.status()}`).toBeTruthy();
  const final = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(final.summary).toEqual(before.summary);
  expect(final.checks.length).toBe(before.checks.length);
});

/* ===========================================================================================
 * ROUND 3. A card whose EXISTENCE was decided by a figure, a stored acceptance no control could
 * reach, and a bucket printed as a measurement.
 *
 * Helpers first again, appended: the suite is serial and nothing above needs them.
 * =========================================================================================== */

/** One row of the run's structural report — the rulebook's own verdicts, before the queue reads them.
 *
 * Spelled out so a field the evaluator stops emitting fails on this line instead of arriving as
 * `undefined`. `details.guard` is the predicate a GUARD row carries and is what separates a guard
 * from an arithmetic relation; `details.target` is DECLARED for a relation and DERIVED FROM THE
 * VIOLATIONS for several guard predicates, which is the whole subject of item 1. */
interface NStructuralRow {
  rule_id: string;
  kind: string;
  scope_key: string;
  status: string;
  details: {
    target?: string; components?: string[]; op?: string; severity?: string; reason?: string;
    guard?: string; guard_keys?: string[]; violations?: unknown[]; rule_text?: string;
  };
}
interface NStructuralRun { result: { structural?: NStructuralRow[] } }

/** The review payload plus the coverage band, whose failed bucket counts the failed rules a card
 *  above it already reports. One decision, two readers — see `_relation_reported_elsewhere`. */
interface NCovReview extends NReview {
  coverage: { available: boolean; failed_reported_elsewhere?: number };
}

/** Take every ORPHANED judgement out of force before asserting on this document's queue.
 *
 * `clearJudgements` only reaches rows the queue still has a card for; an orphan has no card, which
 * is the whole point of item 8 — so a run that died between an acceptance and its withdrawal hands
 * the next run an in-force row that no card-driven cleanup can see. Same discipline as
 * `resetThresholds`: establish the baseline rather than inherit it. Requires review:resolve. */
async function clearOrphans(page: Page, doc: string): Promise<void> {
  const token = await page.evaluate(() => localStorage.getItem("finex-token"));
  const rev = await apiGet<{ judgements: { orphaned: { subject_key: string }[] } }>(
    page, `/api/v1/documents/${doc}/review?locale=en`);
  for (const o of rev.judgements.orphaned) {
    const res = await page.request.delete(
      `/api/v1/documents/${doc}/review/judgements/${o.subject_key}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    expect(res.ok(), `withdrawing orphan ${o.subject_key} → ${res.status()}`).toBeTruthy();
  }
}

test("every failed rulebook rule is on the queue or owned by a card above it, and no fixture breaks a guard",
     async ({ page }) => {
  // ITEM 1 (the HIGH), as far as a browser can hold it — and the part a browser cannot hold is said
  // out loud here rather than asserted around with something weaker.
  //
  // THE DEFECT. `_structural_checks` tested `details.target in covered` BEFORE the guard branch, and
  // for sign_expectation `_guard_slot` sets `details.target = violations[0]["key"]` — the
  // alphabetically first VIOLATING key, DERIVED FROM THE FIGURES. So a run that mis-signed one more
  // line moved the guard's target onto bs_total_assets, which the balance card owns and has already
  // put in `covered`, and the entire guard card vanished from the queue: the reviewer's acceptance
  // was reported as ORPHANED under "corrected, or no longer raised", `failed_reported_elsewhere`
  // counted the guard as reported elsewhere while nothing reported it, and a rulebook rule failing on
  // two lines showed no finding anywhere. Whether a card EXISTS may not be decided by a figure.
  //
  // WHAT THIS BROWSER CANNOT DRIVE, each of the three reasons asserted below rather than assumed:
  //  * no e2e fixture VIOLATES a shipped guard. The v2 rulebook evaluates six guard rows on
  //    sample.pdf and every one comes back pass or skipped — the same on comparative.pdf and
  //    sample.xlsx — so no guard card is raised at all and there is nothing to drop;
  //  * an edit cannot turn a passing guard into a failing one either: `run.result["structural"]` is
  //    written once by the pipeline (routes/extractions.py) and NOTHING recomputes it on a line-item
  //    edit — which is exactly why a structural card carries the "inputs edited since" note
  //    (`_structural_inputs_edited`). The one lever this browser has over the figures therefore
  //    cannot move a guard's verdict;
  //  * and the COLLISION additionally needs a balance / equity / note card to own the colliding
  //    target, because `covered` is built from the accounting checks alone. No fixture extracts
  //    bs_total_equity_and_liabilities, an equity statement or a note table, so none of those cards
  //    exists here.
  // The emission fix is proved in the backend suite instead, through the real loader and evaluator on
  // the shipped rulebook (test_review_judgement.py::
  // test_a_failed_guard_is_not_dropped_when_its_violation_lands_on_a_covered_target, and
  // ::test_a_guards_figure_derived_target_suppresses_no_other_cards_finding_either for the second
  // R1 hole). It is in this file's left_undone, with the fixture that would make it drivable from
  // here, not silently downgraded into something this run can satisfy.
  //
  // WHAT THIS TEST DOES HOLD, over the payload a real run actually serves:
  //  1. the rulebook in force is really evaluating guards on this filing — without that the rest of
  //     this test would be about nothing;
  //  2. no guard row fails, and the queue agrees: there is no guard card, and no screen anywhere
  //     shows a guard finding. That is a statement about THIS fixture, and the assertion is written
  //     to fail loudly the day a fixture does break a guard, naming the work it then unblocks;
  //  3. THE CONTRACT the fix restored: every failed rule is either on the queue or suppressed by a
  //     card that owns its DECLARED target, and every rule the coverage band counts as "reported
  //     above" is one a served card really reports. A guard is never one of them.
  test.setTimeout(240_000);
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");

  const structural = (await apiGet<NStructuralRun>(page, `/api/v1/documents/${doc}/run`))
    .result.structural ?? [];
  expect(structural.length, "the run must carry a structural report at all").toBeGreaterThan(0);
  const guards = structural.filter((s) => s.kind === "guard" || !!s.details.guard);
  // (1) The rulebook's guards were EVALUATED on this filing. If this ever reads 0, the run is being
  // read against a rulebook that declares none and nothing below is a statement about guards.
  expect(guards.length,
         "the rulebook in force declares no guards for this filing, so this test asserts nothing "
         + "about guard emission — pick a rulebook whose sentences reach these concepts")
    .toBeGreaterThan(0);

  const rev = await apiGet<NCovReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const kindOf = (c: NCheck): string => String((c.subject ?? {}).k ?? "");

  // (2) The fixture limit, stated as an assertion so it cannot rot into a false claim of coverage.
  expect(guards.filter((g) => g.status === "fail").map((g) => g.rule_id),
         "A FIXTURE NOW VIOLATES A SHIPPED GUARD. Item 1's emission case is drivable from this "
         + "browser now: accept the guard card, then arrange for the guard's derived target to land "
         + "on a target the balance / equity / note card owns, and assert the card is still on the "
         + "queue and the acceptance reads stale rather than orphaned. Do that instead of deleting "
         + "this expectation.")
    .toEqual([]);
  expect(rev.checks.filter((c) => kindOf(c) === "guard").map((c) => c.id),
         "a guard card is served while no guard row failed").toEqual([]);
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  // Wait for the queue to have RESOLVED — its cards render while the payload is in flight, and an
  // empty list read then is a pending request rather than an absence.
  await expect(page.getByTestId("rv-check")).toHaveCount(rev.checks.length, { timeout: 20_000 });
  await expect(page.getByText("Rulebook guard failed")).toHaveCount(0);

  // (3) THE CONTRACT. `covered` may only ever be the targets served cards OWN, and a guard owns
  // nothing there: its target is violation-derived, so admitting it would let WHICH LINE BROKE decide
  // whether some other card exists. Built here from the served payload, exactly as
  // `_suppression_targets` builds it from the same list.
  const owned = new Set(rev.checks.filter((c) => kindOf(c) !== "guard")
                                  .map((c) => c.target).filter(Boolean));
  const failed = structural.filter((s) => s.status === "fail");
  const suppressed = failed.filter((s) => !s.details.guard && s.kind !== "guard"
                                          && owned.has(String(s.details.target ?? "")));
  // The band's number is the count of failed rules a card above it really reports — not one guard
  // more. This is the count that said 3 while two relations were actually suppressed.
  expect(rev.coverage.failed_reported_elsewhere ?? 0).toBe(suppressed.length);
  for (const s of suppressed) {
    expect(s.details.guard ?? null,
           `${s.rule_id} is a guard and is being counted as reported by another card`).toBeNull();
  }
  // …and nothing that failed is simply missing: a failed rule is on the queue under the id its
  // builder gives it, or it is one of the suppressed relations above.
  const served = new Set(rev.checks.map((c) => c.id));
  for (const s of failed) {
    if (suppressed.includes(s)) continue;
    const guard = !!s.details.guard || s.kind === "guard";
    expect(served.has(`chk-${guard ? "guard" : "structural"}-${s.rule_id}-${s.scope_key}`),
           `${s.rule_id} · ${s.scope_key} FAILS and no card on the queue reports it`).toBeTruthy();
  }
  // Recorded, because the two sweeps above are 0 against 0 on this filing: that is the FIXTURE's
  // limit, not the assertion's. Pinned so the day a rule does fail, this test stops and the author
  // reads the sweep it has just made meaningful instead of trusting a green tick.
  expect(failed.length,
         "this filing now has failing rulebook rules — the two sweeps above are no longer vacuous. "
         + "Check that the failures are the ones intended, then move this expectation to the new "
         + "count rather than deleting it.")
    .toBe(0);
});

test("an ORPHANED acceptance is still in force and can be withdrawn from the row it is shown on",
     async ({ page }) => {
  // ITEM 8. Round 2's finding 5 was closed for CONFLICT cards and left open one row along: an
  // orphaned judgement is an in-force acceptance the server will withdraw on request (DELETE
  // /documents/{id}/review/judgements/{subject_key} → 200 {'ok':true,'withdrawn':true}), it is
  // rendered on screen under "The findings they were recorded against were corrected, or are no
  // longer raised" — and NO control anywhere could remove it. The withdraw button lived only inside a
  // check card, gated on a card-level proxy for "the server holds a row", and AN ORPHAN HAS NO CARD.
  //
  // Not inert either, which is why the missing control matters and why this test proves it before
  // pressing anything: the stored row stays verdict='accepted', so the moment the same finding is
  // raised again with the same figures the card comes back status='accepted' under a verdict nobody
  // re-made. That whole sequence is driven for real here — no route mocking, no doctored payload:
  //
  //   an edit raises an accounting finding → a real acceptance is recorded through the screen →
  //   the edit is reverted, so the finding is no longer raised and the row ORPHANS →
  //   the same edit is re-applied, and the finding returns already 'accepted' (the danger) →
  //   the row is withdrawn FROM THE ORPHAN ROW, gated on the row and not on a card →
  //   the same edit is re-applied once more, and the finding now returns 'open' (the proof).
  test.setTimeout(240_000);
  // Admin: uploading needs documents:manage, editing needs extraction:edit and judging needs
  // review:resolve, and no other single role holds all three.
  await loginAs(page, "admin");
  const doc = await extractFixture(page, "sample.pdf");
  await page.goto("/review", DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  await clearJudgements(page, doc);
  // …and the orphans, which `clearJudgements` cannot see for the very reason this test exists.
  await clearOrphans(page, doc);
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });

  const base = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(base.judgements.orphaned, "the baseline must have nothing in force").toEqual([]);
  const rows = (await apiGet<NRun>(page, `/api/v1/documents/${doc}/run`)).result.rows;

  // --- a finding that can be made to go away again --------------------------------------------
  // A mapped row with no finding against it: moving its figure breaks the arithmetic of the
  // calculated line it feeds, and reverting the edit removes that finding entirely — which is what
  // orphans a judgement made on it. The FIGURE moves; the row composition does not.
  const editable = rows.find((r) => !!r.canonical_key
                                    && !base.checks.some((c) => c.title === r.source_label));
  expect(editable, "the fixture must extract a mapped line whose figure can be moved").toBeTruthy();
  const editKey = editable!.canonical_key as string;
  const EDIT = { value: 555555, formula: "", basis: "consolidated", period: "current",
                 comment: "e2e: raise an accounting finding to orphan" };
  const patch = async () => {
    const r = await apiSend(page, "PATCH", `/api/v1/documents/${doc}/line-items/${editKey}`, EDIT);
    expect(r.ok(), `moving ${editKey} → ${r.status()}`).toBeTruthy();
  };
  const revert = async () => {
    const r = await apiSend(page, "DELETE", `/api/v1/documents/${doc}/line-items/${editKey}`);
    expect(r.ok(), `reverting ${editKey} → ${r.status()}`).toBeTruthy();
  };
  await patch();

  const raised = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const target = raised.checks.find((c) => c.type === "calculated_mismatch" && !!c.subject_key);
  expect(target, "the edit must raise a judgeable accounting finding — a calculated line whose "
                 + "components no longer come to the printed figure").toBeTruthy();
  const KEY = target!.subject_key as string;
  const DIGEST = target!.evidence_digest;

  // --- a real acceptance, made through the screen ---------------------------------------------
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const card = cardForSubject(page, KEY);
  await expect(card).toHaveAttribute("data-status", "open", { timeout: 20_000 });
  await card.click();
  const REASON = `Checked against the printed page — e2e orphan ${Date.now()}`;
  await card.getByTestId("rv-reason").fill(REASON);
  await card.getByTestId("rv-accept").click();
  await expect(card).toHaveAttribute("data-status", "accepted", { timeout: 20_000 });

  // --- the finding goes away, and the acceptance is left standing with no card -----------------
  await revert();
  const orphanedNow = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(orphanedNow.checks.some((c) => c.subject_key === KEY),
         "the finding is still raised, so nothing has orphaned and this test proves nothing")
    .toBeFalsy();
  expect(orphanedNow.judgements.orphaned).toHaveLength(1);
  expect((orphanedNow.judgements.orphaned[0] as { subject_key: string }).subject_key).toBe(KEY);
  expect(orphanedNow.summary.accepted, "an orphan is counted in no queue total").toBe(0);

  // THE DANGER, before any control is pressed: the row is not inert. Raise the same finding with the
  // same figures and it comes back ACCEPTED, carrying the actor and reason of a verdict nobody
  // re-made. Asserted against the server, because the claim is about what is stored.
  await patch();
  const recurred = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const back = recurred.checks.find((c) => c.subject_key === KEY);
  expect(back, "the same edit must raise the same finding again").toBeTruthy();
  expect(back!.evidence_digest, "the figures must be the ones that were accepted, or this is a "
                                + "statement about a stale card instead").toBe(DIGEST);
  expect(back!.status).toBe("accepted");
  expect(back!.judgement?.reason).toBe(REASON);
  await revert();

  // --- THE CONTROL THAT DID NOT EXIST ---------------------------------------------------------
  await page.reload(DCL);
  await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible({ timeout: 15_000 });
  const block = page.getByTestId("rv-orphaned");
  await expect(block).toBeVisible({ timeout: 20_000 });
  // No card submits under this identity — which is precisely why a card-gated control could never
  // reach it. Asserted first, so "the button exists" cannot be satisfied by a build that started
  // serving a card for this subject again.
  await expect(cardForSubject(page, KEY)).toHaveCount(0);
  // The row is told to the reader as an acceptance that is STILL STANDING, not as history.
  await expect(page.getByTestId("rv-orphaned-inforce")).toContainText("still in force");
  await expect(page.getByTestId("rv-orphaned-inforce")).toContainText("Withdraw any that should not");

  const row = page.locator(`[data-testid="rv-orphan"][data-subject-key="${KEY}"]`);
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("admin");            // who vouched …
  await expect(row).toContainText(REASON);             // … and what they said
  const withdraw = row.getByTestId("rv-orphan-withdraw");
  // Wait for the control's STATE — it carries the identity its DELETE will name, which is what makes
  // this a control over THIS row rather than over whatever is first in the list.
  await expect(withdraw).toHaveAttribute("data-subject", KEY, { timeout: 15_000 });
  await expect(withdraw).toBeEnabled();
  await expect(row.getByTestId("rv-orphan-error")).toHaveCount(0);

  // --- AND IT WORKS ---------------------------------------------------------------------------
  await withdraw.click();
  // Gone from the screen: the row it was rendered on is no longer there, and the block with it,
  // because it held only this one.
  await expect(row).toHaveCount(0, { timeout: 20_000 });
  await expect(block).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByTestId("rv-orphan-error")).toHaveCount(0);
  // …and gone from the store, which is the assertion that separates a control that fires from a
  // control that is merely rendered.
  const cleared = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(cleared.judgements.orphaned).toEqual([]);
  expect(cleared.summary.accepted).toBe(0);

  // THE PROOF THAT THE VERDICT IS OUT OF FORCE: raise the same finding with the same figures once
  // more, and it is now OPEN — a reviewer will be asked again, rather than handed an acceptance
  // nobody re-made.
  await patch();
  const afterWithdraw = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  const reraised = afterWithdraw.checks.find((c) => c.subject_key === KEY);
  expect(reraised, "the same edit must raise the same finding again").toBeTruthy();
  expect(reraised!.evidence_digest).toBe(DIGEST);
  expect(reraised!.status,
         "the withdrawn acceptance is still attaching itself to a finding nobody has re-judged")
    .toBe("open");
  expect(reraised!.judgement).toBeNull();

  // --- leave the document as we found it ------------------------------------------------------
  await revert();
  const final = await apiGet<NReview>(page, `/api/v1/documents/${doc}/review?locale=en`);
  expect(final.judgements.orphaned).toEqual([]);
  expect(final.summary).toEqual(base.summary);
  expect(final.checks.length).toBe(base.checks.length);
});

test("a confidence badge prints the measured percentage, and a row nothing scored prints no number",
     async ({ page }) => {
  // ITEM 7. `theme.ts::confStyle` returned a HARDCODED percentage per confidence CATEGORY
  // ({high:"96%", med:"78%", low:"54%"}) and screens printed it as the measured confidence of the
  // very thing it sat above: a page the classifier scored 0.40 announced "54%", a row it never
  // scored announced "78%" (because `_conf_cat(None)` fabricated 60), and every 'high' row of the
  // Notes detail table announced "96%" whatever its real score. The derived figure existed and was
  // thrown away — documents.py did `cat, _ = _conf_cat(...)`.
  //
  // Two things have to hold, and a build could fake either one alone:
  //   * where a measurement EXISTS, the badge prints THAT number — not its band's stand-in. The
  //     payload is the authority here, so the assertion is equality with the served `conf_pct`;
  //   * where none exists, the badge prints NO number. A band is not a measurement, so it is named
  //     ("High"/"Medium"/"Low") rather than converted into a figure, and `data-measured` says which
  //     case each badge is in — without that attribute a build that simply printed a different
  //     literal would still look like a percentage.
  test.setTimeout(240_000);
  await loginAs(page, "admin");

  // --- the seeded sample: a band was recorded and nothing was ever measured -------------------
  await setSampleLoaded(page, true);
  const demoPages = await apiGet<{ pages: { no: number; conf: string; conf_pct?: number | null }[] }>(
    page, "/api/v1/projects/demo/pages?locale=en");
  expect(demoPages.pages.length).toBeGreaterThan(0);
  // The premise, asserted rather than assumed: these pages carry a band and NO measurement, so
  // "prints no number" is the honest rendering of this payload and not a bug being pinned.
  for (const p of demoPages.pages) {
    expect(p.conf, `p.${p.no} carries no band either`).toBeTruthy();
    expect(p.conf_pct ?? null, `p.${p.no} now carries a measurement — assert it is printed`).toBeNull();
  }

  await page.goto("/scope", DCL);
  const tiles = page.getByTestId("scope-conf");
  // Wait for the STATE: the grid renders as its query resolves, and a count read too early is a
  // pending request rather than a page list.
  await expect(tiles).toHaveCount(demoPages.pages.length, { timeout: 20_000 });
  for (let i = 0; i < demoPages.pages.length; i++) {
    await expect(tiles.nth(i)).toHaveAttribute("data-measured", "false");
    // THE ASSERTION THAT FAILS WITH THE SHIPPED confStyle RESTORED: no digit at all, so none of
    // 96% / 78% / 54% can be standing here as this page's score.
    await expect(tiles.nth(i)).not.toContainText(/\d/);
    // What it says instead: the band it actually knows, as a word.
    await expect(tiles.nth(i)).toHaveText(/^(High|Medium|Low)$/);
  }

  // The Notes detail table, where the same literal reached every 'high' row under the CONF. header.
  const note = await apiGet<{ rows: { label: string; conf?: string; conf_pct?: number | null }[] }>(
    page, "/api/v1/projects/demo/notes/12?locale=en");
  const scored = note.rows.filter((r) => !!r.conf);
  expect(scored.length, "the sample note must have rows carrying a band").toBeGreaterThan(0);
  for (const r of scored) {
    expect(r.conf_pct ?? null, `${r.label} now carries a measurement — assert it is printed`).toBeNull();
  }
  await page.goto("/notes", DCL);
  await page.getByText("N12", { exact: true }).click();
  const pills = page.getByTestId("note-conf");
  await expect(pills).toHaveCount(scored.length, { timeout: 20_000 });
  for (let i = 0; i < scored.length; i++) {
    await expect(pills.nth(i)).toHaveAttribute("data-measured", "false");
    await expect(pills.nth(i)).not.toContainText(/\d/);
  }
  // Named because it is the reviewers' own reproduction: "every 'high' row prints 96%".
  await expect(pills.filter({ hasText: "96%" })).toHaveCount(0);

  // --- a real run, where the classifier DID measure something ---------------------------------
  const doc = await extractFixture(page, "sample.pdf");
  const realPages = await apiGet<{ pages: { no: number; conf: string; conf_pct: number | null }[] }>(
    page, `/api/v1/documents/${doc}/pages`);
  expect(realPages.pages.length).toBeGreaterThan(0);
  const measured = realPages.pages.filter((p) => typeof p.conf_pct === "number");
  // Stated as a failure rather than skipped: with nothing measured on this run, the half of the fix
  // that prints the measurement would be uncovered while the test still passed — which is the
  // criticism round 2 made of the round-1 guard test.
  expect(measured.length,
         "no page of this run carries a measured confidence, so nothing here exercises printing one")
    .toBeGreaterThan(0);

  await page.goto("/scope", DCL);
  const realTiles = page.getByTestId("scope-conf");
  await expect(realTiles).toHaveCount(realPages.pages.length, { timeout: 20_000 });
  // Tile i is page i: the grid renders `data.pages` in the order the payload serves them and the
  // client sorts nothing. Position is used ONLY to line one list up against the other for a display
  // comparison — nothing here is an identity, and nothing a judgement is pinned to.
  // The retired literal per band, so the assertion below can say what it is discriminating against.
  const RETIRED: Record<string, string> = { high: "96%", med: "78%", low: "54%" };
  // The premise of that discrimination, asserted rather than assumed: some measured page is scored at
  // a figure that is NOT its band's old literal, so "prints the measurement" and "prints the bucket's
  // stand-in" are distinguishable outcomes on this run. Today it is p.1, in band 'high', scored 95 —
  // one point off the retired 96%, which is exactly why the discrimination is asserted and not eyed.
  expect(measured.some((p) => RETIRED[p.conf] !== `${p.conf_pct}%`),
         "every measured page is scored at exactly its band's retired literal, so this run cannot "
         + "tell the served percentage apart from the bucket it used to be printed from")
    .toBeTruthy();
  for (let i = 0; i < realPages.pages.length; i++) {
    const p = realPages.pages[i];
    const tile = realTiles.nth(i);
    if (typeof p.conf_pct === "number") {
      await expect(tile).toHaveAttribute("data-measured", "true");
      // THE MEASUREMENT, exactly as served — the client rounds and scales nothing, so there is no
      // second number that could disagree with the payload.
      await expect(tile).toHaveText(`${p.conf_pct}%`);
      // …and demonstrably not the band's stand-in, wherever the two differ — which the assertion
      // above proves they do for at least one page of this run.
      if (RETIRED[p.conf] && RETIRED[p.conf] !== `${p.conf_pct}%`) {
        await expect(tile).not.toHaveText(RETIRED[p.conf]);
      }
    } else {
      await expect(tile).toHaveAttribute("data-measured", "false");
      await expect(tile).not.toContainText(/\d/);
    }
  }
});
