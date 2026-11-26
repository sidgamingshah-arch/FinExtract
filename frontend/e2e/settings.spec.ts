/** The Settings screen's route picker: "How filings are read" as three cards.
 *
 * What a person has to be able to do here, and so what is measured:
 *
 *  - see which route the next run uses, on every tab, in one line (the summary reads the SAVED
 *    values, never the edit in progress);
 *  - pick a card and see that route's own options and no other's, before saving;
 *  - save the route and its options in one action, and find them still chosen after a reload;
 *  - change what the whole-PDF route falls back to, from under its card;
 *  - Restore defaults back to what config.toml ships, the whole PDF to the model.
 *
 * These settings are PERSISTED, so every test starts from Restore defaults and ends there. The
 * suite's backend runs with `FINEX_EXTRACTION__LLM_MAPPING=false` (playwright.config.ts), so its
 * shipped fallback is "Rules only"; the tests say which fallback they expect rather than assume.
 */
import { expect, type Page, test } from "./fixtures";

const DCL = { waitUntil: "domcontentloaded" as const };

async function loginAs(page: Page, role: "admin" | "reviewer" | "analyst") {
  await page.goto("/", DCL);
  await page.getByRole("button", { name: new RegExp(role, "i") }).click();
  // The shell's own marker: "FinExtract" alone is on the sign-in page too, so waiting for it lets
  // the next navigation race the login and land back on sign-in.
  await expect(page.getByTestId("nav-rail")).toBeVisible({ timeout: 15_000 });
}

/** Back to the shipped route and options: the route tab's Restore defaults. */
async function restoreRoute(page: Page) {
  await page.goto("/settings?tab=route", DCL);
  const reset = page.getByTestId("ex-reset");
  await expect(reset).toBeVisible({ timeout: 15_000 });
  await reset.click();
  await expect(page.getByTestId("st-route-summary"))
    .toHaveAttribute("data-route", "whole_pdf", { timeout: 15_000 });
  await expect(page.getByTestId("ex-save")).toBeDisabled();
}

test("the route picker shows the route in use, opens the picked route's options, and saves it",
     async ({ page }) => {
  test.setTimeout(120_000);
  await loginAs(page, "admin");
  await restoreRoute(page);

  // Settings opens on How filings are read, and the summary says what the next run does.
  await page.goto("/settings", DCL);
  const summary = page.getByTestId("st-route-summary");
  await expect(summary).toHaveAttribute("data-route", "whole_pdf", { timeout: 15_000 });
  await expect(summary).toContainText("Filings are read by: Whole PDF to the model.");
  await expect(summary).toContainText("If that fails:");
  await expect(page.getByTestId("route-card-whole_pdf")).toHaveAttribute("data-on", "true");
  await expect(page.getByTestId("route-in-use-whole_pdf")).toBeVisible();
  // The card says the rules still read alongside the model; "(skip extraction)" is gone.
  await expect(page.getByTestId("route-card-whole_pdf")).toContainText("alongside");
  await expect(page.getByText("skip extraction")).toHaveCount(0);

  // Only the chosen route's own options are on the page. The whole PDF sends every page and asks
  // every line, so its own options are its request limits, folded; there is no page choice.
  await expect(page.getByTestId("st-group-request-limits")).toBeVisible();
  await expect(page.getByTestId("ex-whole_pdf_pages")).toHaveCount(0);
  await expect(page.getByTestId("route-card-whole_pdf")).toContainText("Page Scope");
  await expect(page.getByTestId("route-fallback")).toBeVisible();
  await expect(page.getByTestId("ex-llm_document_context")).toHaveCount(0);

  // Picking another card swaps the options at once, and marks the change as not saved; the
  // summary still says what is saved.
  await page.getByTestId("route-pick-questions").check();
  await expect(page.getByTestId("route-card-questions")).toHaveAttribute("data-on", "true");
  await expect(page.getByTestId("route-not-saved-questions")).toBeVisible();
  await expect(page.getByTestId("ex-llm_document_context")).toBeVisible();
  await expect(page.getByTestId("st-group-request-limits")).toHaveCount(0);
  await expect(page.getByTestId("route-fallback")).toHaveCount(0);
  await expect(page.getByTestId("st-tab-unsaved-route")).toBeVisible();
  await expect(page.getByTestId("st-route-summary-unsaved")).toBeVisible();
  await expect(summary).toHaveAttribute("data-route", "whole_pdf");

  // The route's main choice is in words; the rarely changed groups are folded until opened.
  await page.getByTestId("ex-llm_document_context-full").check();
  await expect(page.getByTestId("ex-llm_request_grouping")).toHaveCount(0);
  await page.getByTestId("st-group-how-requests-are-put-together").click();
  await expect(page.getByTestId("ex-llm_request_grouping")).toBeVisible();

  // Rules only has no options of its own.
  await page.getByTestId("route-pick-rules").check();
  await expect(page.getByTestId("route-no-options")).toBeVisible();
  await expect(page.getByTestId("ex-llm_document_context")).toHaveCount(0);

  // The keyboard moves between the cards as in any radio group.
  await page.getByTestId("route-pick-rules").focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("route-pick-questions")).toBeChecked();

  // One Save holds the route and its option together.
  await page.getByTestId("ex-save").click();
  await expect(summary).toHaveAttribute("data-route", "questions", { timeout: 15_000 });
  await expect(summary).toContainText("Rules + model questions (the whole report with every question)");
  await expect(page.getByTestId("st-tab-unsaved-route")).toHaveCount(0);

  // …and a reload reads it back from the server.
  await page.reload(DCL);
  await expect(page.getByTestId("route-card-questions"))
    .toHaveAttribute("data-on", "true", { timeout: 15_000 });
  await expect(page.getByTestId("route-in-use-questions")).toBeVisible();
  await expect(page.getByTestId("ex-llm_document_context")).toHaveAttribute("data-value", "full");

  // The summary is on every tab, and takes you back to the picker.
  await page.getByTestId("st-tab-general").click();
  await expect(summary).toContainText("Rules + model questions");
  await page.getByTestId("st-route-summary-change").click();
  await expect(page).toHaveURL(/tab=route/);

  // Restore defaults returns to the whole PDF.
  await page.getByTestId("ex-reset").click();
  await expect(summary).toHaveAttribute("data-route", "whole_pdf", { timeout: 15_000 });
  await expect(page.getByTestId("route-card-whole_pdf")).toHaveAttribute("data-on", "true");
  await expect(page.getByTestId("ex-llm_document_context")).toHaveCount(0);
});

test("the whole-PDF route's fallback is changed from under its card and saved with it",
     async ({ page }) => {
  test.setTimeout(120_000);
  await loginAs(page, "admin");
  await restoreRoute(page);
  const summary = page.getByTestId("st-route-summary");
  const fallback = page.getByTestId("route-fallback");
  // This suite's backend ships `llm_mapping` off, so the shipped fallback is rules only.
  await expect(fallback).toHaveAttribute("data-route", "rules");
  await expect(summary).toContainText("If that fails: rules only.");

  try {
    await page.getByTestId("route-fallback-change").click();
    await page.getByTestId("fallback-pick-questions").check();
    // The main route stays the whole PDF; the fallback's own options open inside its box.
    await expect(page.getByTestId("route-card-whole_pdf")).toHaveAttribute("data-on", "true");
    await expect(fallback).toHaveAttribute("data-route", "questions");
    await expect(fallback.getByTestId("ex-llm_document_context")).toBeVisible();
    await expect(page.getByTestId("route-fallback-text")).toContainText("rules + model questions");

    await page.getByTestId("ex-save").click();
    await expect(summary).toContainText("If that fails: rules + model questions", { timeout: 15_000 });
    await expect(summary).toHaveAttribute("data-route", "whole_pdf");
    await page.reload(DCL);
    await expect(page.getByTestId("route-fallback"))
      .toHaveAttribute("data-route", "questions", { timeout: 15_000 });
  } finally {
    await restoreRoute(page);
  }
  await expect(page.getByTestId("route-fallback")).toHaveAttribute("data-route", "rules");
});

test("coming back to the saved card leaves nothing unsaved that another card set on the way",
     async ({ page }) => {
  test.setTimeout(120_000);
  await loginAs(page, "admin");
  await restoreRoute(page);
  const fallback = page.getByTestId("route-fallback");
  try {
    // Saved: the whole PDF, falling back to rules + model questions.
    await page.getByTestId("route-fallback-change").click();
    await page.getByTestId("fallback-pick-questions").check();
    await page.getByTestId("ex-save").click();
    await expect(page.getByTestId("st-route-summary"))
      .toContainText("If that fails: rules + model questions", { timeout: 15_000 });
    await expect(page.getByTestId("ex-save")).toBeDisabled();

    // Rules only turns the model's questions off; back on the whole PDF, they are as saved.
    await page.getByTestId("route-pick-rules").check();
    await expect(page.getByTestId("ex-save")).toBeEnabled();
    await page.getByTestId("route-pick-whole_pdf").check();
    await expect(page.getByTestId("route-in-use-whole_pdf")).toBeVisible();
    await expect(page.getByTestId("route-not-saved-whole_pdf")).toHaveCount(0);
    await expect(fallback).toHaveAttribute("data-route", "questions");
    await expect(page.getByTestId("ex-save")).toBeDisabled();
    await expect(page.getByTestId("st-tab-unsaved-route")).toHaveCount(0);

    // A fallback the person changed under the card is theirs, and comes back with the card.
    // (The fallback box is still open from the first change.)
    const change = page.getByTestId("route-fallback-change");
    if ((await change.getAttribute("aria-expanded")) !== "true") await change.click();
    await page.getByTestId("fallback-pick-rules").check({ timeout: 10_000 });
    await expect(fallback).toHaveAttribute("data-route", "rules");
    await page.getByTestId("route-pick-questions").check();
    await page.getByTestId("route-pick-whole_pdf").check();
    await expect(page.getByTestId("route-fallback")).toHaveAttribute("data-route", "rules");
    await expect(page.getByTestId("ex-save")).toBeEnabled();
  } finally {
    await restoreRoute(page);
  }
});

test("rules only says when the extra model calls it still makes cannot reach a model",
     async ({ page }) => {
  test.setTimeout(120_000);
  await loginAs(page, "admin");
  await restoreRoute(page);
  // The session lives in localStorage, not a cookie (as e2e/scope.spec.ts reads it).
  const token = await page.evaluate(() => localStorage.getItem("finex-token"));
  const res = await page.request.get("/api/v1/settings", { headers: { Authorization: `Bearer ${token}` } });
  expect(res.ok()).toBeTruthy();
  const settings = await res.json();
  const extra = ["llm_row_suggestions", "llm_disclosures", "llm_gap_routing",
                 "llm_contingent_liabilities"].filter((k) => Boolean(settings.extraction?.[k]));
  test.skip(extra.length === 0, "no extra model call is switched on in this backend");
  const unreachable = settings.llm?.provider === "stub" || !settings.llm?.key_configured;

  await page.getByTestId("route-pick-rules").check();
  const calls = page.getByTestId("route-extra-calls");
  await expect(calls).toBeVisible();
  if (unreachable) {
    await expect(calls).toContainText(settings.llm?.provider === "stub"
      ? "are not sent either" : "will fail");
  } else {
    await expect(calls).not.toContainText("⚠");
  }
});
