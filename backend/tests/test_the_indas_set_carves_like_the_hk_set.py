"""The Ind AS line-item set declares the same carving as the HK set it was made from.

`carved_from_face` on a cascade rung says the figure it computes is read out of captions the face
already prints, so it is shown on its own line and not added into a total a second time
(`rollups.evaluate`). The Ind AS set was copied from the HK set the day the flag was introduced and
lost it on the three related-party rungs. That was invisible while the Indian filings' notes were
read as the group's; once they are read on the face's standalone basis, an Ind AS test filing (f2)
added its related-party receivables 46.85 / 38.20 on top of the face lines they are carved from —
total non-current assets 6,572.80 -> 6,619.65 — where the HK set leaves the totals as printed.
"""
from __future__ import annotations

import json
import pathlib

_TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "sample" / "templates"


def _rungs(name: str) -> dict[tuple[str, str], dict]:
    raw = json.loads((_TEMPLATES / name).read_text(encoding="utf-8"))
    return {(item["key"], rung.get("id")): rung for item in raw["items"]
            for rung in (item.get("cascade") or ())}


def test_every_rung_the_two_sets_share_declares_the_same_carving():
    hk, indas = _rungs("output_csv_hk_line_items.json"), _rungs("output_csv_indas_line_items.json")
    shared = sorted(set(hk) & set(indas))
    assert len(shared) >= 33, len(shared)

    differ = [(key, rung, flag) for key, rung in shared
              for flag in ("carved_from_face", "outranks_printed")
              if bool(hk[(key, rung)].get(flag)) != bool(indas[(key, rung)].get(flag))]
    assert differ == []


def test_the_related_party_rungs_are_carved():
    indas = _rungs("output_csv_indas_line_items.json")

    for key, rung in (("bs_nca__due_from_related_parties_ltp", "MAX_VALID"),
                      ("bs_ncl__due_to_related_parties_ltp", "FROM_THE_RELATED_PARTY_NOTE"),
                      ("bs_cl__due_to_related_parties_cp", "FROM_THE_RELATED_PARTY_NOTE")):
        assert indas[(key, rung)].get("carved_from_face") is True, (key, rung)
