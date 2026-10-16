/** Page Scope by section: a page is added to a statement and taken out with one click, a section
 *  is switched off and its pages typed, and the next run reads the change.
 *
 *  The fixture is the HK running-header report from backend/tests/fixtures/generate.py
 *  (make_hk_running_header_report_pdf): sheets 1–3 front matter, 4 the income statement, 5 the
 *  balance sheet, 6 the cash flow, 7–8 notes, 9 a five-year summary. */
// `test` comes from ./fixtures, not from @playwright/test: it carries an auto fixture that
// ABORTS any request leaving this machine. See e2e/fixtures.ts.
import { chooseTemplate, expect, type Page, test } from "./fixtures";

const DCL = { waitUntil: "domcontentloaded" as const };

async function loginAs(page: Page, role: "admin" | "reviewer" | "analyst") {
  await page.goto("/", DCL);
  await page.getByRole("button", { name: new RegExp(role, "i") }).click();
  await expect(page.getByText(/FinExtract/)).toBeVisible();
}

/** GET an API path as the signed-in user (the session lives in localStorage, not a cookie). */
async function apiGet<T>(page: Page, path: string): Promise<T> {
  const token = await page.evaluate(() => localStorage.getItem("finex-token"));
  const res = await page.request.get(path, { headers: { Authorization: `Bearer ${token}` } });
  expect(res.ok(), `${path} → ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

const at = (page: Page, testid: string, attr: string, value: string | number) =>
  page.locator(`[data-testid="${testid}"][data-${attr}="${value}"]`);

test("page scope: a section's pages change with one click, a section switches off, and the next "
     + "run reads the change", async ({ page }) => {
  test.setTimeout(240_000);
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  await chooseTemplate(page);
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/scope-report.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "scope-report.pdf" }).first())
    .toBeVisible({ timeout: 15_000 });
  await expect.poll(() => page.evaluate(() => localStorage.getItem("finex-active-doc")),
                    { timeout: 15_000 }).toBeTruthy();
  const doc = await page.evaluate(() => localStorage.getItem("finex-active-doc"));

  await page.goto("/scope", DCL);
  // The five sections, always; the cover is read for nothing.
  await expect(page.getByTestId("scope-section")).toHaveCount(5, { timeout: 15_000 });
  await expect(at(page, "scope-page-for", "page", 2)).toHaveText("not read");

  // Choosing a section lights its pages and puts one button under every page.
  await at(page, "scope-choose", "section", "profit_and_loss").click();
  await expect(page.getByTestId("scope-hint")).toBeVisible();
  await expect(at(page, "scope-tile", "page", 4)).toHaveAttribute("data-lit", "true");

  // One click adds the highlights page to the income statement; a second takes it out again.
  const pick2 = at(page, "scope-pick", "page", 2);
  await expect(pick2).toHaveAttribute("data-action", "add");
  await pick2.click();
  await expect(at(page, "scope-page-for", "page", 2)).toContainText("Profit & loss");
  await expect(pick2).toHaveAttribute("data-action", "remove");
  await pick2.click();
  await expect(at(page, "scope-page-for", "page", 2)).toHaveText("not read");
  // A page read for another statement is moved, not added beside it.
  await expect(at(page, "scope-pick", "page", 5)).toHaveAttribute("data-action", "move");

  // The Use switch: the notes keep their pages and are not read.
  const notesUse = at(page, "scope-use", "section", "notes");
  await notesUse.click();
  await expect(notesUse).toHaveAttribute("aria-checked", "false");
  await expect(at(page, "scope-page-for", "page", 7)).toHaveText("not read");
  // Typing a section's pages switches it back on.
  await at(page, "scope-pages", "section", "notes").click();
  const typed = at(page, "scope-pages-input", "section", "notes");
  await typed.fill("7-8");
  await typed.press("Enter");
  await expect(notesUse).toHaveAttribute("aria-checked", "true");

  // Nothing was extracted by any of that: the footer goes to the Extraction screen, which does.
  // (Its heading is the entity the run names, so the run is read off the API rather than the title.)
  const runStatus = async () =>
    (await apiGet<{ status: string }>(page, `/api/v1/documents/${doc}/run-status`)).status;
  expect(await runStatus()).toBe("none");
  await page.getByTestId("scope-extract").click();
  await expect(page).toHaveURL(/\/extraction/);
  await expect.poll(runStatus, { timeout: 90_000 }).toBe("succeeded");

  // A change after the run is said on the screen, and survives the reload that brought us back.
  await page.goto("/scope", DCL);
  await expect(page.getByTestId("scope-section")).toHaveCount(5, { timeout: 15_000 });
  await at(page, "scope-use", "section", "notes").click();
  await expect(page.getByTestId("scope-changed")).toBeVisible();
  await page.reload(DCL);
  await expect(page.getByTestId("scope-changed")).toBeVisible({ timeout: 15_000 });

  // On a document already extracted the button asks first; Extract again starts the new run.
  await page.getByTestId("scope-extract").click();
  await expect(page.getByTestId("scope-extract-again")).toContainText("The pages to read have changed");
  await page.getByTestId("scope-again-extract").click();
  await expect(page).toHaveURL(/\/extraction/);
  await expect.poll(async () => (await apiGet<{ last_run: { reads_current_pages: boolean } | null }>(
    page, `/api/v1/documents/${doc}/pages`)).last_run?.reads_current_pages,
                    { timeout: 90_000 }).toBe(true);
  // ONE new run, not two: the Extraction screen adopted the run Extract again started.
  await expect.poll(runStatus, { timeout: 90_000 }).toBe("succeeded");
  const runs = await apiGet<{ runs: unknown[] }>(page, `/api/v1/documents/${doc}/runs`);
  expect(runs.runs).toHaveLength(2);
});
