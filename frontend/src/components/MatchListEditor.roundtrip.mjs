// THE INVARIANT EVERY USE OF `MatchListEditor` RESTS ON, checked against every shipped line.
//
// The control reads several fields into one list and writes them back. If that round trip is not
// exact, opening a line and saving it silently rewrites its configuration — and the paired fields
// are NOT interchangeable:
//
//   `aliases` match a caption folded through `normalize_label`; `regex_hints` match the raw
//   caption AND the folded one; `keyword_hints` require every word to be present.
//   A `*_any` / `*_none` regex MATCHES; a `*_terms` entry SCORES, so an unanticipated phrasing
//   still ranks instead of matching nothing.
//
// So a value that moved field on its own would change what it does.
//
// Run with:  node src/components/MatchListEditor.roundtrip.mjs
//
// A script rather than a test because there is no vitest setup in `frontend/` — which is also why
// the pure helpers are exported separately from the component: a check that had to render React
// could not run here at all.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const seedPath = resolve(here, "../../../backend/app/sample/templates/output_csv_hk_line_items.json");

// The helpers are TypeScript; strip the annotations the way a bundler would rather than pulling in
// a toolchain. None of the four pure functions uses a type at runtime.
const src = readFileSync(resolve(here, "MatchListEditor.tsx"), "utf8");
const pure = src
  .slice(src.indexOf("const META ="), src.indexOf("export function MatchListEditor"))
  .replace(/^export /gm, "")
  .replace(/: MatchRow\[\]/g, "")
  .replace(/: Record<string, string\[\]>/g, "")
  .replace(/: string\[\],?\s*\n?\s*map: ModeMap/g, ", map")
  .replace(/: ModeMap/g, "")
  .replace(/: string\[\]/g, "")
  .replace(/: string \| null/g, "")
  .replace(/: MatchMode/g, "")
  .replace(/: string\b/g, "")
  .replace(/\): \{ mode[^}]+\}/g, ")");

const mod = await import(
  "data:text/javascript," + encodeURIComponent(pure + "\nexport { rowsOf, fieldsOf, modeOf };"));
const { rowsOf, fieldsOf, modeOf } = mod;

// The four controls, exactly as `screens/LineItems.tsx` configures them.
const CONTROLS = [
  {
    name: "captions that claim this line",
    order: ["aliases", "regex_hints", "keyword_hints"],
    map: { exact: "aliases", exact_loose: "regex_hints", starts: "regex_hints",
           ends: "regex_hints", contains: "keyword_hints", pattern: "regex_hints" },
    read: (i) => ({ aliases: i.aliases ?? [], regex_hints: i.regex_hints ?? [],
                    keyword_hints: i.keyword_hints ?? [] }),
  },
  {
    name: "which note holds this line",
    order: ["note_source.note_title_any", "note_source.note_terms"],
    map: { exact_loose: "note_source.note_title_any", starts: "note_source.note_title_any",
           ends: "note_source.note_title_any", scored: "note_source.note_terms",
           pattern: "note_source.note_title_any" },
    read: (i) => ({ "note_source.note_title_any": i.note_source?.note_title_any ?? [],
                    "note_source.note_terms": i.note_source?.note_terms ?? [] }),
  },
  {
    name: "which rows inside it count",
    order: ["note_source.row_caption_any", "note_source.row_terms"],
    map: { exact_loose: "note_source.row_caption_any", starts: "note_source.row_caption_any",
           ends: "note_source.row_caption_any", scored: "note_source.row_terms",
           pattern: "note_source.row_caption_any" },
    read: (i) => ({ "note_source.row_caption_any": i.note_source?.row_caption_any ?? [],
                    "note_source.row_terms": i.note_source?.row_terms ?? [] }),
  },
  {
    name: "which rows must be excluded",
    order: ["note_source.row_caption_none", "note_source.row_terms_none"],
    map: { exact_loose: "note_source.row_caption_none", starts: "note_source.row_caption_none",
           ends: "note_source.row_caption_none", scored: "note_source.row_terms_none",
           pattern: "note_source.row_caption_none" },
    read: (i) => ({ "note_source.row_caption_none": i.note_source?.row_caption_none ?? [],
                    "note_source.row_terms_none": i.note_source?.row_terms_none ?? [] }),
  },
];

const items = JSON.parse(readFileSync(seedPath, "utf8")).items;
let failures = 0;

for (const c of CONTROLS) {
  let lines = 0, values = 0, broken = [];
  const modes = {};
  for (const item of items) {
    const before = c.read(item);
    const n = c.order.reduce((t, f) => t + (before[f]?.length ?? 0), 0);
    if (!n) continue;
    lines++;
    values += n;

    const rows = rowsOf(before, c.order, c.map);
    for (const r of rows) modes[r.mode] = (modes[r.mode] ?? 0) + 1;

    const after = fieldsOf(rows, c.order);
    for (const f of c.order) {
      if (JSON.stringify(after[f]) !== JSON.stringify(before[f] ?? [])) {
        broken.push({ key: item.key, field: f, before: before[f], after: after[f] });
      }
    }
  }
  console.log(`\n${c.name}`);
  console.log(`  ${lines} lines, ${values} values`);
  console.log(`  modes assigned: ${JSON.stringify(modes)}`);
  console.log(`  round trips that changed a field: ${broken.length}`);
  for (const b of broken.slice(0, 3)) console.log("    ", JSON.stringify(b).slice(0, 240));
  failures += broken.length;
}

// A pattern must never be shown as a readable literal while its stored value keeps regex power —
// the text an author reads would then be a paraphrase they could edit into something else.
let paraphrased = 0;
for (const item of items) {
  const patterns = [
    ["regex_hints", item.regex_hints ?? []],
    ["note_source.note_title_any", item.note_source?.note_title_any ?? []],
    ["note_source.row_caption_any", item.note_source?.row_caption_any ?? []],
    ["note_source.row_caption_none", item.note_source?.row_caption_none ?? []],
  ];
  for (const [field, values] of patterns) {
    for (const raw of values) {
      const { mode, text } = modeOf(field, raw, {});
      if (mode !== "pattern" && text === raw && /[\\[\](){}|*+?]/.test(raw)) paraphrased++;
    }
  }
}
console.log(`\npatterns shown as a literal while left unchanged: ${paraphrased}`);
process.exit(failures === 0 && paraphrased === 0 ? 0 : 1);
