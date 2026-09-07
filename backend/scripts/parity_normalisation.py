"""Does normalisation still fold every caption the same way? Snapshot, then compare.

WHY THIS EXISTS SEPARATELY FROM ``parity_line_items.py``. That script holds the ported matcher to
the incumbent, and it is blind to exactly the change this one is built for. Both engines import
one normalisation — `line_item_matching` says so at the top of its own module docstring
("NORMALISATION IS IMPORTED, NEVER REIMPLEMENTED") — so a change to `normalize_label` moves BOTH
answers together and the parity run still reports 11433/11433. Agreement between two engines
reading the same broken normaliser is not evidence of anything.

AND THE BLAST RADIUS WAS MEASURED, which is why a harness comes before the migration. Feeding
every rulebook caption back in the shape a filing prints it — ``<alias> ("<initials>")`` — with
`mapping._ABBREV_GLOSS` disabled changed 1,050 of 1,993 resolutions: 976 fell to unmatched and 74
landed on a DIFFERENT concept. The 74 are the dangerous half. An unmatched row is swept into its
section's residual and itemised under its own label, so the statement still ties and a reviewer can
see the caption; a row on the wrong specific line also ties and looks finished. Migrating the
caption vocabulary out of code with no pin on the fold is unfalsifiable, and the 74 are what
"unfalsifiable" costs.

WHAT IS PINNED, per caption: `normalize_label(caption)`, `label_segments(caption)`, and
`normalize_label` of each segment. The third is not redundant. The matcher's exact tier consumes
per-segment normalisations, not the whole-string one (`mapping.py:1727`,
`line_item_matching.py:443-446`), and the residue class that motivated `_BRACKETED_NUMBER` is
visible ONLY there: "… (note 32) 年內計入遞延稅項（附註32）" splits by script, the Chinese citation's
附註 leaves with the Han segment, and the Latin segment keeps a bare "(32)" that the citation
pattern no longer recognises. A snapshot of whole-string output alone would have let that fix
regress silently. `to_simplified` is inside `normalize_label`, so the Han fold table is pinned here
too — a change to `services.han` surfaces as a diff on Traditional captions.

THE CORPUS is the rulebook's own 1,993 distinct caption strings (label + aliases + every
`aliases_i18n` list over 462 concepts) PLUS printed variants of each. The clean form is what the
rulebook stores; the dirty form is what a filing prints, and every pattern in `normalize_label`
exists because of a dirty form. Snapshotting only clean captions would pin the identity transform
and none of the strippers, so each caption is also emitted wearing a gloss, a note citation in four
shapes, a trailing note marker, a bracketed number, a CAS enumerator, a CAS component marker, a CAS
sign-convention note, an orphaned wrap fragment, a Traditional respelling, and a bilingual pairing.

THE CAS AND GLOSS VARIANTS ARE BUILT ON LATIN CAPTIONS TOO, deliberately, even though no HKEX
filing prints 其中： in front of an English caption. Half of what these patterns are worth is what
they REFUSE to touch, and those refusals are written down as such: `_CAS_ORPHAN_HEAD` requires a
Han character so "b) Trade receivables" survives, `_CAS_LINE_PREFIX` will not eat 七、70 because a
note reference must stay unmatchable. A refusal that is never exercised is not pinned, so the
corpus asks for it.

A CORPUS CHANGE FAILS TOO. An added or removed caption is reported as ADDED/REMOVED and exits
non-zero, the same as a changed fold. It is usually benign — someone gave a concept another alias
— but a corpus that quietly shrinks weakens every claim this file makes, so the shrink has to be
acknowledged with `--update` rather than absorbed.

Run from ``backend``:
    ../.venv/Scripts/python.exe scripts/parity_normalisation.py            # compare
    ../.venv/Scripts/python.exe scripts/parity_normalisation.py --update   # re-baseline
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys
import warnings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

from app.schemas.loader import load_ontology  # noqa: E402
from app.services import han as han_mod  # noqa: E402
from app.services.han import has_han  # noqa: E402
from app.services.mapping import label_segments, normalize_label  # noqa: E402

TEMPLATES = pathlib.Path("app/sample/templates")
ONTOLOGY = TEMPLATES / "output_csv_hk_ontology.json"
BASELINE = pathlib.Path("scripts/_normalisation_baseline.json")

# The fillers the printed variants carry. Real values from real filings, not placeholders: 32 is
# note 32 because that is the note the deferred-tax caption in `_NOTE_CITATION`'s comment cites,
# 16(a) because that is the shape `_TRAILING_NUMERIC_NOTE` was written for, and the sign-convention
# tail is the one 四创电子 prints on twenty-three separate captions.
NOTE_NO = "32"
SUB_NOTE = "16(b)"
TRAILING_NOTE = "16(a)"
COMPARATIVE_YEAR = "2022"
SIGN_NOTE = "（亏损以“－”号填列）"
ORPHAN_FRAGMENT = "填列） "


# THE FOLD RUNS BACKWARDS TO MAKE THE HK FORM, because the rulebook does not contain it. Measured
# on the shipped rulebook: of its 478 Han captions, exactly ONE (不會重新分類至損益的項目) is
# Traditional. Every other one is already Simplified, so a corpus of clean rulebook captions puts
# essentially no load on `to_simplified` — and `to_simplified` is the entire reason a Hong Kong
# filing, which prints 銷售成本 and 收益, can reach a Simplified alias at all. Inverting the table
# gives the printed HK spelling of every Han caption and puts the fold under the load it exists for.
#
# WHAT THIS CANNOT DISCOVER, stated because it is easy to over-read: the inverse is built FROM
# `han._T2S`, so it only produces Traditional characters that table already knows. A character
# missing from the table stays missing in both directions and this variant is silent about it. It
# detects an entry being LOST or changed, never one that was never there.
#
# The inverse is many-to-one in reverse — 322 pairs collapse to 319 distinct Simplified characters,
# so three of them have two Traditional spellings. First-in-table wins, which is arbitrary but
# fixed, and a stable arbitrary choice is all a snapshot needs.
_S2T: dict[str, str] = {}
for _t, _s in han_mod._T2S.items():
    _S2T.setdefault(_s, _t)


def _to_traditional(text: str) -> str:
    return "".join(_S2T.get(c, c) for c in text)


def _initials(caption: str) -> str:
    """The abbreviation a filing would coin for this caption, as a filing coins them.

    'PRC corporate income tax' -> 'CIT': initials of the words that carry meaning, which is what
    makes the gloss variant the same shape as the measured 1,050-resolution probe rather than a
    synthetic string. Han captions have no word boundaries to take initials from, so they get the
    contraction a Chinese filing actually prints instead — the leading characters in corner
    brackets, 中國企業所得稅（「企業所得稅」）-style.
    """
    words = [w for w in "".join(c if c.isalnum() else " " for c in caption).split()
             if len(w) > 2 and w.lower() not in {"and", "the", "for", "from", "net", "other"}]
    letters = "".join(w[0] for w in words[:4]).upper()
    return letters or "ABC"


def _variants(caption: str, partner: str | None) -> list[tuple[str, str]]:
    """``(kind, printed_form)`` for one rulebook caption.

    ``partner`` is the same concept's caption in the OTHER script, when it has one, so the
    bilingual variants are a real pairing off one concept rather than two unrelated strings glued
    together. `label_segments` is only interesting on a caption whose two halves genuinely name the
    same thing; a fabricated pairing would pin the split but not the fact that either half alone is
    an exact alias, which is what the exact tier relies on.
    """
    han = has_han(caption)
    out: list[tuple[str, str]] = []

    # _ABBREV_GLOSS — the filing naming a short form for the term it has just written out. This is
    # the variant whose removal moved 1,050 resolutions.
    if han:
        out.append(("abbrev_gloss", f"{caption}（「{caption[:3]}」）"))
    else:
        out.append(("abbrev_gloss", f'{caption} ("{_initials(caption)}")'))

    # _NOTE_CITATION, all four alternatives it carries. The bracketed form is the common one; the
    # bare CJK marker is unbracketed because the rulebook names it that way; the leading form is
    # delimited by its colon; the stump is what row reconstruction hands over when a bilingual row
    # is truncated mid-citation, and it was a real filing's output, not a hypothetical.
    out.append(("note_citation", f"{caption}（附註{NOTE_NO}）" if han
                else f"{caption} (note {NOTE_NO})"))
    out.append(("note_citation_bare", f"{caption}附註{NOTE_NO}" if han
                else f"Note {NOTE_NO}: {caption}"))
    out.append(("note_citation_sub", f"{caption}（附註{SUB_NOTE}）" if han
                else f"{caption} (note {SUB_NOTE})"))
    out.append(("note_citation_truncated", f"{caption}（附註" if han
                else f"{caption} (note"))

    # _TRAILING_NUMERIC_NOTE and _BRACKETED_NUMBER — a note pointer with the word "note" left off,
    # and a comparative-year marker. Both are pointers rather than names.
    out.append(("trailing_numeric_note", f"{caption} {TRAILING_NOTE}"))
    out.append(("bracketed_number", f"{caption} ({COMPARATIVE_YEAR})"))

    # THE CAS SHAPES GO ON HAN CAPTIONS, because a mainland face is printed in Han and putting
    # 其中： in front of "Trade receivables" is a string no filing produces. It is not only
    # unrealistic, it is expensive: a Han marker on a Latin caption makes every one of these
    # bilingual, so `label_segments` splits it three ways and the snapshot stores four copies of
    # the caption instead of one. Measured, that choice alone was 3.4MB of a 6.9MB baseline.
    if han:
        # The Hong Kong spelling of a Simplified rulebook caption, and the same caption wearing
        # the two markers a Traditional filing prints (減：, not 减：) — which is what makes
        # `normalize_label`'s "fold to Simplified BEFORE the CAS rules" ordering load-bearing.
        out.append(("traditional", _to_traditional(caption)))
        out.append(("traditional_marker", f"減：{_to_traditional(caption)}"))
        out.append(("cas_line_prefix", f"三、{caption}"))
        out.append(("cas_line_prefix_arabic", f"1、{caption}"))
        out.append(("cas_component_marker", f"其中：{caption}"))
        out.append(("cas_sign_note", f"{caption}{SIGN_NOTE}"))
        out.append(("cas_orphan_head", f"{ORPHAN_FRAGMENT}{caption}"))
        # The pathological stack the bounded loop in `normalize_label` exists for: an orphaned
        # fragment, then an enumerator, then a component marker, with a sign note behind. All
        # three CAS patterns on one wrapped line, and two separate `_CAS_LINE_PREFIX` passes
        # needed to clear it — which is what the `range(3)` loop is bounding.
        out.append(("cas_stacked", f"{ORPHAN_FRAGMENT}三、其中：{caption}{SIGN_NOTE}"))
    else:
        # THE ENGLISH PATH'S REFUSAL, one probe per Latin caption instead of the six CAS variants
        # the Han branch gets. Every CAS rule declares that it leaves an English caption alone —
        # `_CAS_ORPHAN_HEAD` requires a Han character so "b) Trade receivables" survives,
        # `_CAS_LINE_PREFIX` is anchored and enumerator-shaped — and a refusal nothing exercises
        # is not pinned. Stacking all three patterns onto one probe costs a single entry and still
        # fails the moment any of them starts firing on Latin text, because the fold of this
        # string would change.
        out.append(("cas_refusal_latin", f"{ORPHAN_FRAGMENT}三、其中：{caption}{SIGN_NOTE}"))

    if partner:
        latin, hanhalf = (partner, caption) if han else (caption, partner)
        out.append(("bilingual", f"{latin} {hanhalf}"))
        # THE HIGHEST-VALUE ROW IN THE CORPUS. This is the caption that motivated
        # `_BRACKETED_NUMBER`: the citation is printed in both halves, the split by script strands
        # 附註 in the Han segment and leaves the Latin segment holding a bare "(32)" that
        # `_NOTE_CITATION` cannot see. Nothing else in the corpus reaches that state.
        out.append(("bilingual_note",
                    f"{latin} (note {NOTE_NO}) {hanhalf}（附註{NOTE_NO}）"))
    return out


def build_corpus() -> tuple[dict[str, str], collections.Counter, int]:
    """Every caption the rulebook knows, plus its printed variants. Returns caption -> kind.

    First producer wins on a collision, and the iteration is ordered (base captions before
    variants, concepts in file order), so the same rulebook always yields the same corpus and a
    diff never moves because a dict reordered.
    """
    ont = load_ontology(json.loads(ONTOLOGY.read_text(encoding="utf-8")), resolve=True)

    per_concept: list[list[str]] = []
    for m in ont.mappings:
        names = [m.label, *m.aliases]
        for locale_aliases in (m.aliases_i18n or {}).values():
            names.extend(locale_aliases)
        seen: dict[str, None] = {}
        for n in names:
            if n and n.strip():
                seen.setdefault(n, None)
        per_concept.append(list(seen))

    kind_of: dict[str, str] = {}
    for captions in per_concept:
        for c in captions:
            kind_of.setdefault(c, "base")
    for captions in per_concept:
        han_here = next((c for c in captions if has_han(c)), None)
        latin_here = next((c for c in captions if not has_han(c)), None)
        for c in captions:
            partner = latin_here if has_han(c) else han_here
            for kind, printed in _variants(c, partner):
                kind_of.setdefault(printed, kind)

    return kind_of, collections.Counter(kind_of.values()), len(ont.mappings)


def snapshot(kind_of: dict[str, str]) -> dict:
    """caption -> the three recorded values, omitting the two that are usually implied.

    ``segments`` and ``segment_norms`` are written only when they differ from ``[caption]`` and
    ``[normalized]``, which is the monolingual case and most of the corpus. That is not a size
    trick for its own sake: it keeps the snapshot's diff readable, because a bilingual caption's
    split is then the only thing on the line that can move.
    """
    entries: dict[str, dict] = {}
    for caption, kind in kind_of.items():
        norm = normalize_label(caption)
        segs = label_segments(caption)
        seg_norms = [n for n in (normalize_label(s) for s in segs) if n]
        rec: dict = {"kind": kind, "normalized": norm}
        if segs != [caption]:
            rec["segments"] = segs
        if seg_norms != ([norm] if norm else []):
            rec["segment_norms"] = seg_norms
        entries[caption] = rec
    return entries


def write_baseline(entries: dict[str, dict], concepts: int) -> None:
    """One entry per line, valid JSON, ``ensure_ascii=False``.

    Hand-assembled rather than `json.dump(indent=…)` because indent explodes every `segments` list
    over four lines and a 22,000-entry snapshot becomes unreviewable. One line per caption means a
    normalisation change shows up in `git diff` as the captions it moved, which is the whole
    reporting value of keeping the file in the tree.
    """
    meta = {
        "source": str(ONTOLOGY).replace("\\", "/"),
        "ontology_sha256": hashlib.sha256(ONTOLOGY.read_bytes()).hexdigest()[:16],
        "concepts": concepts,
        "captions": len(entries),
        "pins": ["normalize_label", "label_segments", "normalize_label(segment)"],
        # WHICH HAN CONVERTER PRODUCED THIS, because `to_simplified` has two implementations and
        # picks between them on an OPTIONAL dependency: OpenCC when it imports (full Unihan), the
        # 322-pair built-in table when it does not. `pip install opencc` therefore changes the fold
        # of Han captions without a line of this repo changing, and every one of those would be
        # reported as a normalisation difference. Recorded so that failure is diagnosable in one
        # line instead of being investigated as a code regression.
        "han_converter": "opencc" if han_mod._CC is not None else "builtin_table",
    }
    lines = ["{", '  "meta": ' + json.dumps(meta, ensure_ascii=False, sort_keys=True) + ",",
             '  "entries": {']
    items = list(entries.items())
    for i, (caption, rec) in enumerate(items):
        tail = "" if i == len(items) - 1 else ","
        lines.append("    " + json.dumps(caption, ensure_ascii=False) + ": "
                     + json.dumps(rec, ensure_ascii=False, sort_keys=True) + tail)
    lines += ["  }", "}", ""]
    BASELINE.write_text("\n".join(lines), encoding="utf-8")


def _fmt(rec: dict) -> str:
    parts = [repr(rec.get("normalized", ""))]
    if "segments" in rec:
        parts.append(f"segments={rec['segments']!r}")
    if "segment_norms" in rec:
        parts.append(f"segment_norms={rec['segment_norms']!r}")
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline. Required to overwrite an existing one: a harness "
                         "that re-baselines itself on a difference proves nothing.")
    ap.add_argument("--show", type=int, default=20, metavar="N",
                    help="how many differences to print in full (default 20)")
    args = ap.parse_args(argv)

    kind_of, by_kind, concepts = build_corpus()
    entries = snapshot(kind_of)
    print(f"  ontology concepts   : {concepts}")
    print(f"  base captions       : {by_kind['base']}")
    print(f"  printed variants    : {len(entries) - by_kind['base']} "
          f"across {len(by_kind) - 1} kinds")
    print(f"  corpus size         : {len(entries)}")

    if not BASELINE.exists():
        write_baseline(entries, concepts)
        print(f"\n  CREATED {BASELINE} with {len(entries)} entries.")
        print("  Nothing to compare against yet — run again to check for drift.")
        return 0

    old = json.loads(BASELINE.read_text(encoding="utf-8"))
    old_entries: dict[str, dict] = old["entries"]
    print(f"  baseline            : {BASELINE} ({len(old_entries)} entries)")

    now_conv = "opencc" if han_mod._CC is not None else "builtin_table"
    was_conv = old.get("meta", {}).get("han_converter")
    if was_conv and was_conv != now_conv:
        # Said before the counts, because it explains them: this is an environment difference, not
        # a code change, and diagnosing it as a regression would waste the whole investigation.
        print(f"\n  !! HAN CONVERTER CHANGED: baseline used {was_conv}, this run uses {now_conv}.")
        print("     `to_simplified` has two implementations and picks on whether OpenCC imports,")
        print("     so every Han difference below may be that and not a change to this repo.")
    if (was_sha := old.get("meta", {}).get("ontology_sha256")) and \
            was_sha != hashlib.sha256(ONTOLOGY.read_bytes()).hexdigest()[:16]:
        # The rulebook moved under the harness. ADDED/REMOVED below is then expected rather than
        # alarming; CHANGED still is not, because an edit to one concept's aliases cannot alter how
        # a different caption folds.
        print(f"\n  note: rulebook changed since the baseline ({was_sha} -> "
              f"{hashlib.sha256(ONTOLOGY.read_bytes()).hexdigest()[:16]}).")

    identical, changed = 0, []
    for caption, rec in entries.items():
        prev = old_entries.get(caption)
        if prev is None:
            continue
        # `kind` is provenance, not a normalisation result: a caption reached by two variant
        # builders keeps whichever named it first, and a reordering there is not a fold change.
        if {k: v for k, v in rec.items() if k != "kind"} == \
           {k: v for k, v in prev.items() if k != "kind"}:
            identical += 1
        else:
            changed.append((caption, prev, rec))
    added = [c for c in entries if c not in old_entries]
    removed = [c for c in old_entries if c not in entries]

    print(f"\n  IDENTICAL  {identical}/{len(entries)}")
    print(f"  CHANGED    {len(changed)}")
    print(f"  ADDED      {len(added)}   (in the rulebook now, not in the baseline)")
    print(f"  REMOVED    {len(removed)}   (in the baseline, gone from the rulebook)")

    if changed:
        # Grouped by variant kind first, because that is the diagnosis: 1,050 rows all of kind
        # `abbrev_gloss` says one pattern stopped firing, while the same count spread evenly over
        # every kind says the punctuation fold or the Han table moved underneath all of them.
        print("\n  changed by variant kind:")
        for kind, n in collections.Counter(k for _, _, r in changed
                                           for k in [r["kind"]]).most_common():
            print(f"    {kind:26} {n}")
        print(f"\n  first {min(args.show, len(changed))} of {len(changed)}:")
        for caption, prev, rec in changed[:args.show]:
            print(f"    {caption!r}   [{rec['kind']}]")
            print(f"        was: {_fmt(prev)}")
            print(f"        now: {_fmt(rec)}")
    for label, group in (("ADDED", added), ("REMOVED", removed)):
        if group:
            print(f"\n  first {min(args.show, len(group))} {label} of {len(group)}:")
            for caption in group[:args.show]:
                print(f"    {caption!r}")

    differing = len(changed) + len(added) + len(removed)
    if args.update:
        write_baseline(entries, concepts)
        # Exit 0 because re-baselining was asked for explicitly — but the differences are printed
        # above regardless, so an accidental `--update` still leaves what it accepted on the record.
        print(f"\n  UPDATED {BASELINE} — {differing} difference(s) accepted on request.")
        return 0

    if differing:
        # THE TWO FAILURES ARE NOT THE SAME FAILURE and the verdict must not blur them, because a
        # harness that cries "normalisation moved" over an added alias gets ignored, and then it is
        # not a harness. CHANGED means a caption folds to something else than it did — the caption
        # may now resolve to another concept or to none. ADDED/REMOVED means the corpus moved
        # underneath: the fold is intact for everything both runs share, and what needs explaining
        # is the rulebook edit.
        if changed:
            print(f"\n  VERDICT: NORMALISATION MOVED — {len(changed)} caption(s) fold differently. "
                  f"Each is a\n           caption that may now resolve to another concept or to "
                  f"none, and the wrong-concept\n           half of that does not show up as a "
                  f"broken subtotal. Re-run parity_line_items.py.")
        else:
            print(f"\n  VERDICT: the fold is UNCHANGED on all {identical} shared captions, but the "
                  f"corpus moved —\n           {len(added)} added, {len(removed)} removed. That is "
                  f"a rulebook edit, not a normalisation\n           change. Confirm it was "
                  f"intended.")
        print(f"           Then `--update` to accept: {differing} difference(s) would be recorded.")
        return 1
    print(f"\n  VERDICT: normalisation is unchanged over all {len(entries)} captions and variants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
