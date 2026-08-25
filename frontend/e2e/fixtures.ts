/** The suite's `test`, which refuses to let the product reach off this machine.
 *
 *  WHY THIS EXISTS, and it is not a policy preference. The app used to pull IBM Plex from
 *  fonts.googleapis.com through a render-blocking `<link>` in `<head>`, ahead of the module script.
 *  A pending stylesheet blocks execution of the script after it, and DOMContentLoaded waits for
 *  that deferred module — so every `page.goto` waited for a host outside the machine before the app
 *  booted at all. Measured from a retained trace of a failing run: 12.9s per navigation, three
 *  navigations, each one immediately preceded by ERR_CONNECTION_RESET on the fonts URL, while the
 *  slowest of the 191 in-app requests in the same test was 191ms.
 *
 *  That is a determinism defect rather than a slowness one, because the 12.9s belonged to the
 *  SANDBOX's handling of an unreachable host, not to the product: the identical request from a
 *  container with working egress returns in 0.05s. A suite whose page loads are set by someone
 *  else's network cannot tell you whether the product got slower — and it did not merely make the
 *  suite slow, it decided WHICH test failed. A test doing four navigations sat at 52s against a
 *  60s timeout, so the outcome depended on where the clock ran out. Two runs of one unchanged tree
 *  failed at two different tests, which is how a timing problem came to be reported as a mystery.
 *
 *  The font dependency is gone (vendored under src/assets/fonts — see the comment at the top of
 *  src/index.css). This fixture is what stops the next one arriving unnoticed: an external request
 *  now FAILS IN ZERO MILLISECONDS instead of stalling for however long the machine takes to give
 *  up, so a reintroduced CDN link surfaces as a loud, immediate, reproducible failure at the
 *  request itself rather than as a flake somewhere downstream. Aborting rather than fulfilling is
 *  deliberate: a stub would let the product keep depending on the host and hide it from us.
 *
 *  Everything the product legitimately needs is same-origin — the Vite dev server on :5173 proxies
 *  /api to the backend on :8000 — so there is nothing to allow-list beyond localhost. Non-HTTP
 *  schemes pass through untouched: `data:` and `blob:` URLs are how the app hands the browser a
 *  generated PDF page or an exported workbook, and they never leave the tab.
 */
import { test as base, expect, type Page } from "@playwright/test";

/** Hosts the product may talk to. Anything else is a bug in the product, not in the suite. */
const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

export const test = base.extend<{ blockedExternalRequests: string[] }>({
  // An array the test can assert on, and — more usefully — one this fixture can fail the test
  // with, so a blocked request is never merely quiet. Declared `auto` so every test gets the
  // guard without opting in; a guard you have to remember is a guard that lapses.
  blockedExternalRequests: [
    async ({ context }, use, testInfo) => {
      const blocked: string[] = [];
      await context.route("**/*", (route) => {
        const raw = route.request().url();
        let host: string;
        try {
          const u = new URL(raw);
          if (u.protocol !== "http:" && u.protocol !== "https:") return route.continue();
          host = u.hostname;
        } catch {
          // An unparseable URL is not something to silently drop on the floor.
          return route.continue();
        }
        if (LOCAL.has(host)) return route.continue();
        blocked.push(raw);
        return route.abort("blockedbyclient");
      });

      await use(blocked);

      // Reported as a failure, not a console line. A page that still reaches a CDN is the exact
      // defect this file was written for, and a passing test that quietly logged it would be how
      // the defect came back. Skipped when the test already failed, so the real failure is not
      // buried under a second one.
      if (blocked.length && testInfo.status === testInfo.expectedStatus) {
        const unique = [...new Set(blocked)];
        throw new Error(
          `The page requested ${unique.length} off-machine URL(s). Everything the product needs `
            + `is same-origin; an external dependency here puts this suite's timing in someone `
            + `else's network. Vendor the asset instead:\n  ${unique.join("\n  ")}`,
        );
      }
    },
    { auto: true },
  ],
});

export { expect, type Page };
