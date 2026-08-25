import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

// Frontend smoke regression. Boots the FastAPI backend + Vite dev server (which proxies
// /api → backend) and drives the real UI in the preinstalled Chromium. One worker and no parallel
// files, because the "load sample" flow toggles process-wide backend state — that pair is what
// makes the suite safe against the single shared backend. It is NOT the same thing as
// `describe.configure({ mode: "serial" })`, which smoke.spec.ts used to apply to all 46 of its
// tests: that adds nothing to the safety and costs you every result after the first failure. See
// the comment where it was removed.

// THE SUITE'S OWN DATABASE AND OBJECT STORE — not the developer's.
//
// This config used to set only FINEX_EXTRACTION__LLM_MAPPING on the backend, so persistence fell
// through to the default `database_url = "sqlite:///./finex.db"`, resolved against `cwd: "../backend"`.
// The suite therefore ran against `backend/finex.db`, the file a dev server holds, and the damage was
// not theorised but found: that database held 18 ontology versions, 16 of them published by this
// suite, whose only difference from the seed was probe values this file's own tests type in ("E2E
// alias <epoch>", "E2E includes <epoch>", "E2E netting <epoch>"). Two separate faults in one:
//
//   * it MUTATED a developer's workspace — silently, on every run;
//   * and the suite's starting state drifted every time it ran, which is a first-order determinism
//     problem. "The rulebook in force" and "how many versions the index lists" are assertions in
//     here, and both were being answered by the residue of previous runs rather than by the seed.
//
// Absolute paths, derived from THIS FILE rather than from the shell's cwd: the backend runs with
// `cwd: "../backend"` and the runner may be invoked from anywhere, so a relative FINEX_DATABASE_URL
// would name a different file depending on who typed the command. Same env vars and same shape as
// backend/tests/conftest.py, which has always isolated the pytest suite this way.
const HERE = dirname(fileURLToPath(import.meta.url));
const SCRATCH = join(HERE, "e2e", ".scratch");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  // ONE STATED BUDGET FOR A WEB-FIRST ASSERTION. There was no `expect` block at all, so
  // `toBeVisible`/`toHaveURL` fell to Playwright's 5s default while this file's heavier assertions
  // passed 15s/60s explicitly — two budgets, one of them never written down. With every navigation
  // costing 12.9s on a font request to an unreachable host (see e2e/fixtures.ts), a 5s assertion
  // was a coin flip, and raising it would have been the wrong fix: a suite that passes by waiting
  // longer cannot tell you the product got slower.
  //
  // The dependency is gone, so navigations are sub-second and this number is slack rather than the
  // thing under test. 10s is deliberately not generous: it is long enough to absorb a cold vite
  // transform on the first hit of a route, and short enough that a real regression in how long a
  // screen takes to settle still fails here instead of being absorbed.
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    headless: true,
    navigationTimeout: 20_000,
    // Kept on for diagnosis: the suite failed at a DIFFERENT test on two consecutive runs of one
    // tree, both times waiting for a screen to settle, and the page snapshot alone did not say
    // why. A trace carries the network timeline and the console, which is what distinguishes "the
    // server was slow" from "the dev server reloaded the page under us".
    trace: "retain-on-failure",
    launchOptions: { executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" },
  },
  webServer: [
    {
      // The store is emptied by the very process that then serves out of it, so every run starts from
      // the seed and from nothing else. Deliberately not in `globalSetup` (Playwright starts
      // webServers before global setup, because a webServer IS a plugin) and deliberately not at this
      // file's module scope (the config is re-imported in every worker, i.e. after the server is up —
      // a wipe there deletes the database out from under the run). reset-scratch.mjs says both.
      //
      // NO PATH IS INTERPOLATED INTO THIS STRING. The command goes through `/bin/sh -c`, where `$`, a
      // backtick and a backslash are still live inside double quotes — so a checkout under such a
      // path would reach the script mangled, trip its own refusal, and stop uvicorn from ever
      // starting behind the `&&`. All Playwright then reports is "Timed out waiting 120000ms from
      // config.webServer", which points at the backend rather than at the quoting. The script's
      // location is relative to the `cwd` below (the same frontend/backend adjacency that `cwd`
      // already assumes) and the directory it clears arrives in `env`, which is passed to the
      // process rather than through the shell.
      command: "node ../frontend/e2e/reset-scratch.mjs"
             + " && exec python -m uvicorn app.main:app --port 8000",
      cwd: "../backend",
      url: "http://127.0.0.1:8000/health",
      // NEVER adopt a server that is already listening. It reads as a convenience, but it makes
      // the suite's result depend on machine state: a server left running from an earlier run
      // serves the code IT was started with, so the suite silently tests something other than the
      // working tree. That is not hypothetical — it produced a 500 in the extraction view that
      // could not be reproduced in-process, over HTTP, or against a fresh server, and it is why
      // two agents reported 19 passed and 1 failed for the same commit: they were talking to
      // different servers. Starting our own costs a few seconds; if the port is occupied,
      // Playwright now fails loudly instead of testing the wrong thing.
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        // Read by reset-scratch.mjs, not by the app: the directory it empties. Deliberately not
        // FINEX_-prefixed — the app's settings all carry that prefix, and this is not one of them.
        E2E_SCRATCH_DIR: SCRATCH,
        // See the SCRATCH comment above: the suite's own store, so `backend/finex.db` is never
        // opened. `sqlite:///` + an absolute path is four slashes in total, as in conftest.py.
        FINEX_DATABASE_URL: `sqlite:///${join(SCRATCH, "e2e.db")}`,
        FINEX_OBJECT_STORE_ROOT: join(SCRATCH, "objects"),
        // Force deterministic mapping so the (network-blocked) LLM isn't attempted during
        // e2e — keeps extraction fast and offline; alias-tier mapping still populates.
        FINEX_EXTRACTION__LLM_MAPPING: "false",
      },
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      // Reuse is fine HERE, and the asymmetry with the backend above is the point: vite reads the
      // source from disk on request and hot-replaces it, so an already-running dev server is
      // serving the working tree. Uvicorn is not — it imports the Python once at start-up and
      // holds it — which is why only that one refuses to be adopted.
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
