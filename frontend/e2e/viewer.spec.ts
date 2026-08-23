/** Getting around the source document: the collapsed navigation rail, and find-in-document.
 *
 *  Both are about the same thing — the screens here put a statement grid beside a page of the
 *  filing, so the menu should not hold 214px permanently, and the reader has to be able to reach a
 *  page by what it SAYS and not only by which extracted value they happened to click. */
import { test, expect, Page } from "@playwright/test";

const DCL = { waitUntil: "domcontentloaded" as const };

async function loginAs(page: Page, role: "admin" | "reviewer" | "analyst") {
  await page.goto("/", DCL);
  await page.getByRole("button", { name: new RegExp(role, "i") }).click();
  await expect(page.getByText(/FinExtract/)).toBeVisible();
}

test("the navigation rail starts collapsed, expands on demand, and remembers", async ({ page }) => {
  await loginAs(page, "analyst");
  await page.goto("/workspace", DCL);

  const rail = page.getByTestId("nav-rail");
  await expect(rail).toHaveAttribute("data-collapsed", "1");
  // Collapsed hides the LABELS and nothing else: every destination is still one click away.
  await expect(page.getByText("Review Queue")).toHaveCount(0);

  await page.getByTestId("nav-toggle").click();
  await expect(rail).toHaveAttribute("data-collapsed", "0");
  await expect(page.getByText("Review Queue").first()).toBeVisible();

  // The choice survives a reload — it is the reader's, not the screen's.
  await page.reload(DCL);
  await expect(page.getByTestId("nav-rail")).toHaveAttribute("data-collapsed", "0");

  // …and collapsing again sticks the same way.
  await page.getByTestId("nav-toggle").click();
  await page.reload(DCL);
  await expect(page.getByTestId("nav-rail")).toHaveAttribute("data-collapsed", "1");
});

test("find-in-document jumps to the page that carries the phrase", async ({ page }) => {
  test.setTimeout(180_000);
  await loginAs(page, "analyst");
  await page.goto("/upload", DCL);
  await page.setInputFiles('input[type="file"]', "e2e/fixtures/sample.pdf");
  await expect(page.getByTestId("doc-row").filter({ hasText: "sample.pdf" }))
    .toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Run integrity check/ }).click();
  await page.getByRole("button", { name: /Extract now/ }).click();
  await expect(page.getByRole("heading", { name: "Extracted data" }))
    .toBeVisible({ timeout: 60_000 });

  await page.goto("/workspace", DCL);
  const box = page.getByTestId("page-search").first();
  await expect(box).toBeVisible({ timeout: 15_000 });
  await box.fill("Trade receivables");
  await box.press("Enter");

  // A hit count, and the hit highlighted by the same overlay a picked value gets.
  await expect(page.getByTestId("page-search-count")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("page-search-count")).toContainText("1 /");
  await expect(page.getByTestId("prov-highlight")).toBeVisible({ timeout: 15_000 });

  // A phrase the filing does not contain says so, rather than leaving the last hit on screen.
  await box.fill("zzz no such phrase zzz");
  await box.press("Enter");
  await expect(page.getByText("No matches")).toBeVisible({ timeout: 20_000 });
});
