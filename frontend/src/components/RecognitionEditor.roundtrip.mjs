// THE INVARIANT THE COLLAPSED RECOGNITION CONTROL RESTS ON, checked against every shipped line.
//
// The control reads `aliases`, `regex_hints` and `keyword_hints` into one list and writes them
// back. If that round trip is not exact, opening a line and saving it silently rewrites its
// recognition — and the three fields are NOT interchangeable at match time (`aliases` match the
// caption folded through `normalize_label`, `regex_hints` match the raw caption AND the folded
// one), so a value that moves field changes what it matches.
//
// Run with:  node src/components/RecognitionEditor.roundtrip.mjs
//
// There is no vitest setup in `frontend/`, which is why this is a script and why the pure helpers
// (`rowsOf`, `fieldsOf`, `modeOf`, `rawFor`) are exported separately from the component: a check
// that had to render React could not run here at all.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const seedPath = resolve(here, "../../../backend/app/sample/templates/output_csv_hk_line_items.json");

// The helpers are TypeScript; strip the types the same way a bundler would rather than pulling in
// a toolchain. Only the four pure functions are needed and none of them uses a type at runtime.
const src = readFileSync(resolve(here, "RecognitionEditor.tsx"), "utf8");
const pure = src
  .slice(src.indexOf("const MODE_LABEL"), src.indexOf("export function RecognitionEditor"))
  .replace(/^export /gm, "")
  .replace(/: Record<[^>]+>/g, "")
  .replace(/: RecognitionField\b/g, "")
  .replace(/: RecognitionMode\b/g, "")
  .replace(/: RecognitionRow\[\]/g, "")
  .replace(/: string\[\]/g, "")
  .replace(/: string \| null/g, "")
  .replace(/: string\b/g, "")
  .replace(/\): \{ mode[^}]+\}/g, ")")
  .replace(/\): Record<[^>]+>/g, ")");

const mod = await import(
  "data:text/javascript," + encodeURIComponent(pure + "\nexport { rowsOf, fieldsOf, modeOf };"));
const { rowsOf, fieldsOf, modeOf } = mod;

const seed = JSON.parse(readFileSync(seedPath, "utf8"));
const items = seed.items;

let lines = 0, values = 0, broken = [], modes = {};
for (const item of items) {
  const aliases = item.aliases ?? [];
  const regex = item.regex_hints ?? [];
  const keywords = item.keyword_hints ?? [];
  if (!aliases.length && !regex.length && !keywords.length) continue;
  lines++;
  values += aliases.length + regex.length + keywords.length;

  const rows = rowsOf(aliases, regex, keywords);
  for (const r of rows) modes[r.mode] = (modes[r.mode] ?? 0) + 1;

  const back = fieldsOf(rows);
  const same = JSON.stringify(back.aliases) === JSON.stringify(aliases)
    && JSON.stringify(back.regex_hints) === JSON.stringify(regex)
    && JSON.stringify(back.keyword_hints) === JSON.stringify(keywords);
  if (!same) broken.push({ key: item.key, aliases, regex, keywords, back });
}

console.log(`${lines} lines carry recognition, ${values} values in total`);
console.log("modes the reader assigned:", JSON.stringify(modes));
console.log(`round trips that changed a field: ${broken.length}`);
for (const b of broken.slice(0, 5)) console.log("  ", JSON.stringify(b).slice(0, 300));

// And the mode reader must never call a real pattern a literal — the text it shows would then be a
// paraphrase an author could edit into something that means something else.
let paraphrased = 0;
for (const item of items) {
  for (const raw of item.regex_hints ?? []) {
    const { mode, text } = modeOf("regex_hints", raw);
    if (mode !== "pattern" && text === raw && /[\\[\](){}|*+?]/.test(raw)) paraphrased++;
  }
}
console.log(`patterns shown as literal but left unchanged: ${paraphrased}`);
process.exit(broken.length === 0 && paraphrased === 0 ? 0 : 1);
