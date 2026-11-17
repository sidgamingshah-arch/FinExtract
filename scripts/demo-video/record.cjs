// FinExtract demo: a live run on Apple's FY2025 Form 10-K with the US GAAP template, driven with a
// visible cursor, ending on the configuration slide. Writes the browser video and captions.json
// (cue times, sync marks, and the waits to cut). Run it through record-demo.sh.
let chromium;
try { ({ chromium } = require("playwright")); } catch { ({ chromium } = require("@playwright/test")); }
const fs = require("fs");
const path = require("path");

const [, , base, token, filing, outDir, slidePath] = process.argv;
const W = 1600, H = 900;

const CURSOR = () => {
  const add = () => {
    if (document.getElementById("__demo_cursor") || !document.body) return;
    const c = document.createElement("div");
    c.id = "__demo_cursor";
    c.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.5 15 L11.5 22 L14.5 20.7 L11.6 13.9 L18 13.9 Z"
      fill="#111827" stroke="#ffffff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, { position: "fixed", left: (window.__cx || 800) + "px", top: (window.__cy || 450) + "px",
      zIndex: 2147483647, pointerEvents: "none", transform: "translate(-3px,-2px)" });
    document.body.appendChild(c);
    const st = document.createElement("style");
    st.textContent = `@keyframes __rip{from{transform:translate(-50%,-50%) scale(.2);opacity:.9}
      to{transform:translate(-50%,-50%) scale(1.6);opacity:0}}`;
    document.head.appendChild(st);
  };
  document.addEventListener("mousemove", (e) => {
    window.__cx = e.clientX; window.__cy = e.clientY; add();
    const c = document.getElementById("__demo_cursor");
    if (c) { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }
  }, true);
  document.addEventListener("mousedown", (e) => {
    const r = document.createElement("div");
    Object.assign(r.style, { position: "fixed", left: e.clientX + "px", top: e.clientY + "px", width: "44px",
      height: "44px", borderRadius: "50%", border: "3px solid #4f46e5", background: "rgba(79,70,229,.18)",
      zIndex: 2147483646, pointerEvents: "none", animation: "__rip .55s ease-out forwards" });
    document.body.appendChild(r);
    setTimeout(() => r.remove(), 650);
  }, true);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add);
  else add();
};

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H },
                                         recordVideo: { dir: outDir, size: { width: W, height: H } } });
  await ctx.addInitScript(([t]) => { localStorage.setItem("finex-token", t); }, [token]);
  await ctx.addInitScript(CURSOR);
  const page = await ctx.newPage();
  const t0 = Date.now();
  const now = () => (Date.now() - t0) / 1000;
  const cues = [];
  const marks = {};
  let open = null;
  // Sync marks: the browser video's clock drifts from this script's when the machine is busy, so
  // every caption and cut point also flips a small marker in the bottom-left corner (red/blue in
  // turn). The edit finds each flip in the video, pins the times to it, then crops the strip off.
  const anchors = [];
  let nsync = 0;
  const sync = async () => {
    const k = nsync++;
    anchors.push(now());
    await page.evaluate((k) => {
      let m = document.getElementById("__sync");
      if (!m) {
        m = document.createElement("div"); m.id = "__sync";
        Object.assign(m.style, { position: "fixed", left: "0", bottom: "0", width: "32px", height: "6px",
          zIndex: 2147483647, pointerEvents: "none" });
        document.body.appendChild(m);
      }
      m.style.background = k % 2 ? "#0000ff" : "#ff0000";
    }, k).catch(() => {});
    await page.waitForTimeout(160);
  };
  // A caption shown from now until the next one.
  const caption = async (text) => { await sync(); if (open) { open.end = anchors[anchors.length - 1]; cues.push(open); } open = { start: anchors[anchors.length - 1], text }; };
  const close = async () => { await sync(); if (open) { open.end = anchors[anchors.length - 1]; cues.push(open); open = null; } };
  const wait = (ms) => page.waitForTimeout(ms);
  const pos = { x: 800, y: 450 };
  const moveTo = async (x, y, steps = 30) => { await page.mouse.move(x, y, { steps }); pos.x = x; pos.y = y; };
  const clickOn = async (loc, { pause = 350 } = {}) => {
    const el = loc.first();
    await el.waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
    await el.scrollIntoViewIfNeeded().catch(() => {});
    const b = await el.boundingBox();
    if (!b) return false;
    await moveTo(b.x + b.width / 2, b.y + b.height / 2);
    await wait(pause);
    await page.mouse.down(); await wait(90); await page.mouse.up();
    return true;
  };
  const hover = async (loc) => {
    const b = await loc.first().boundingBox().catch(() => null);
    if (b) await moveTo(b.x + b.width / 2, b.y + b.height / 2, 22);
  };
  // A wait on the page loading, not on the product: cut from the edit when it runs long.
  const cuts = [];
  const loaded = async (loc, ms = 60000) => {
    await sync();
    const a = anchors[anchors.length - 1];
    await loc.waitFor({ timeout: ms }).catch(() => {});
    await sync();
    const b = anchors[anchors.length - 1];
    if (b - a > 1.5) cuts.push([a + 0.3, b - 0.3]);
  };
  const nav = (title) => clickOn(page.locator(`[data-testid="nav-rail"] [title="${title}"]`));
  const card = async (title, sub) => {
    await page.setContent(`<html><body style="margin:0;height:100vh;display:flex;flex-direction:column;
      align-items:center;justify-content:center;background:#111827;font-family:Inter,Arial,sans-serif">
      <div style="color:#fff;font-size:68px;font-weight:700;letter-spacing:-1px">${title}</div>
      <div style="color:#9ca3af;font-size:28px;margin-top:18px">${sub}</div></body></html>`);
  };

  // 1. Title
  await card("FinExtract", "From annual report to a checked financial spread");
  await caption("FinExtract turns a company's annual report into a checked financial spread. Here is one run, end to end.");
  await wait(5000);

  // 2. Choose the template — every configured template is offered
  await page.goto(base + "/upload", { waitUntil: "domcontentloaded" });
  await wait(1500);
  await moveTo(700, 420, 10);
  await caption("First, choose the output template: the layout the spread is delivered in.");
  await wait(1200);
  await clickOn(page.getByRole("button", { name: /choose another|change|choose/i }));
  await wait(1200);
  await caption("Several templates are configured: HKFRS for Hong Kong, US GAAP for 10-K filers, Ind AS Schedule III, and the ICON spread.");
  const opts = page.getByTestId("tpl-option");
  const n = await opts.count();
  for (let i = 0; i < Math.min(n, 6); i++) { await hover(opts.nth(i)); await wait(700); }
  await wait(800);
  await caption("This filing is a US Form 10-K, so: the US GAAP template.");
  await clickOn(opts.filter({ hasText: /US GAAP/i }));
  await wait(2200);

  // 3. Upload the filing
  await caption("Next, the filing itself: Apple's Form 10-K for fiscal 2025, 80 pages.");
  const browse = page.getByText(/^browse$/i);
  const chooser = page.waitForEvent("filechooser", { timeout: 4000 }).catch(() => null);
  await clickOn(browse);
  const fc = await chooser;
  if (fc) await fc.setFiles(filing); else await page.setInputFiles('input[type="file"]', filing);
  await wait(1500);
  await loaded(page.getByTestId("doc-row").first(), 120000);
  await wait(2000);
  await caption("Uploaded. One click starts the extraction.");
  await wait(800);
  await clickOn(page.getByRole("button", { name: /extract directly/i }));

  // 4. The run (most of the wait is cut in the edit)
  await wait(1500);
  await caption("The run reads every page: it finds the statements and the notes, rebuilds each table from where its words are printed, and maps every row to a template line.");
  await moveTo(1250, 300, 20);
  await wait(7500);
  await close();
  marks.cut_from = anchors[anchors.length - 1];
  // The results page's heading is the company's name, so wait for the run's own heading to go.
  await page.getByText("Extracting…").first().waitFor({ timeout: 30000 }).catch(() => {});
  await page.getByText("Extracting…").first().waitFor({ state: "hidden", timeout: 900000 });
  await page.locator("h1").first().waitFor({ timeout: 60000 });
  await sync();
  marks.cut_to = anchors[anchors.length - 1] - 1.0;
  cuts.push([marks.cut_from, marks.cut_to]);
  await wait(1200);
  await caption("Done. Every extracted row, with its page, the line it was mapped to, and how confident that mapping is.");
  await page.mouse.wheel(0, 450);
  await wait(6000);

  // 5. The Workspace
  await caption("The Workspace lays the spread beside the filing itself.");
  await nav("Workspace");
  await wait(3500);
  const fig = page.locator("span", { hasText: /^\(?\d{1,3}(,\d{2,3})+\)?$/ });
  await caption("Click any figure, and the filing opens at the exact spot it was printed, with its line, its note and its confidence.");
  await clickOn(fig.nth(2));
  await wait(3500);
  await clickOn(fig.nth(6));
  await wait(3500);
  const chip = page.locator('[role="link"]').filter({ hasText: /^\s*\d{1,2}\s*$/ });
  if (await chip.count()) {
    await caption("Apple prints no note numbers beside its lines, so FinExtract finds the note behind each one. A click opens it.");
    await clickOn(chip.first());
    await loaded(page.getByTestId("note-table").first(), 45000);
    await wait(3000);
  }

  // 6. Notes
  await nav("All Notes");
  await loaded(page.getByText("N13", { exact: true }).first());
  await clickOn(page.getByText("N13", { exact: true }));
  await loaded(page.getByTestId("note-table").first());
  await caption("Notes are shown table by table, with every column the filing prints: here Apple's segment note, region by region.");
  await wait(1200);
  for (const i of [0, 1, 2, 3, 4, 5, 6]) { await hover(page.getByTestId("note-col").nth(i)); await wait(700); }
  await wait(1500);

  // 7. Review
  await caption("Anything that needs a decision goes to the review queue. Here no total fails its check; what is left are rows that reached no line, or were swept into an Others line.");
  await nav("Review Queue");
  await loaded(page.locator("text=/Rows swept into an Others line/").first());
  await wait(1500);
  const finding = page.locator("text=/Rows swept into an Others line/").first();
  await clickOn(finding);
  await wait(4500);

  // 8. Export
  await caption("When the review is done, export the spread to Excel or JSON, each figure carrying its source.");
  await nav("Export");
  await wait(2500);
  const exp = page.getByRole("button", { name: /excel|xlsx|download|export/i });
  await hover(exp);
  await wait(3500);

  // 9. What makes it possible: the options a template line is configured with
  await page.goto(require("url").pathToFileURL(path.resolve(slidePath)).href);
  await moveTo(560, 640, 10);
  await caption("What makes it possible is configuration, not code. Each template line says where its figure is read from: the face, a note, a sentence of prose, or anywhere.");
  await wait(1800);
  for (const r of ["face", "notes", "prose", "anywhere"]) {
    await clickOn(page.locator(`[data-r="${r}"]`), { pause: 250 });
    await wait(2300);
  }
  await caption("And how it is recognised, which rows and columns of a note to take, how it is built from its parts, and the checks it must pass.");
  const groups = page.locator(".grp");
  for (let i = 0; i < 7; i++) { await hover(groups.nth(i).locator(".chip").first()); await wait(1050); }
  await caption("The same options drive every template. Change one on the Line Items screen, and the next run reads it.");
  const tpls = page.locator(".tpl");
  for (let i = 0; i < 5; i++) { await hover(tpls.nth(i)); await wait(800); }
  await wait(1200);

  await card("FinExtract", "Every figure traceable to the page it came from");
  await caption("Every figure traceable to the page it came from.");
  await wait(4000);
  await close();

  const video = page.video();
  await ctx.close();
  const vpath = await video.path();
  fs.writeFileSync(path.join(outDir, "captions.json"), JSON.stringify({ video: vpath, cues, anchors, marks: { ...marks, cuts } }, null, 1));
  console.log(vpath, cues.length, JSON.stringify(marks));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
