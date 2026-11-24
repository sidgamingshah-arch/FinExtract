"""The Settings screen offers three ways a filing is read, as cards, instead of unrelated switches.

The routes are not settings of their own. A route is a reading of two knobs the pipeline already
decides on — `whole_pdf_to_model` (`core.pipeline.pipeline_for`) and `llm_mapping`
(`stages.line_item_llm`) — and picking a card sets those two and nothing else
(`settings_state.ROUTES`). What has to hold for the cards to be safe:

* every pair of values names exactly one route, so the screen can never show two cards chosen,
  or none, and the one it shows is the one a run takes;
* every option on the route tab belongs to exactly one route, so "only the chosen route's own
  options show under it" is decided by the descriptors, not by a list in the browser;
* the cards say what the routes do now. The whole-PDF switch was labelled "(skip extraction)";
  the rules now read the filing alongside the model, so that label was removed and is pinned
  out here.
"""
from __future__ import annotations

import itertools

import pytest

from app.config import get_settings
from app.services import settings_state as S


@pytest.fixture(autouse=True)
def _restore_settings():
    yield
    S.reset()


def _admin(client):
    tok = client.post("/api/v1/auth/login", json={"username": "admin"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _fields():
    return {f["key"]: f for f in S.extraction_config()["fields"]}


def test_every_pair_of_switch_values_names_exactly_one_route():
    """Both switches, every combination: one card is chosen, and it is the right one. The
    whole-PDF switch wins whatever `llm_mapping` says, because `llm_mapping` is then the
    fallback's choice, not the route's."""
    expected = {(False, False): "rules", (True, False): "questions",
                (False, True): "whole_pdf", (True, True): "whole_pdf"}
    for mapping, whole in itertools.product((False, True), repeat=2):
        values = {"llm_mapping": mapping, "whole_pdf_to_model": whole}
        matching = [r.id for r in S.ROUTES
                    if all(values[k] == v for k, v in r.sets)]
        assert matching == [expected[(mapping, whole)]], values
        assert S.route_of(values) == expected[(mapping, whole)]


def test_a_route_sets_only_the_two_switches_and_they_are_switches():
    fields = _fields()
    for route in S.ROUTES:
        keys = {k for k, _ in route.sets} | {k for k, _ in route.fallback}
        assert keys <= {"llm_mapping", "whole_pdf_to_model"}, route.id
        for key in keys:
            assert fields[key]["kind"] == "bool" and fields[key]["tab"] == "route", key


def test_picking_a_card_is_what_the_pipeline_then_does(client):
    """PATCH a card's values the way the screen sends them: the snapshot names that route, and
    the pipeline a run assembles is the one the card describes (the whole-PDF stage is in it on
    the whole-PDF route only)."""
    from app.core.pipeline import pipeline_for

    h = _admin(client)
    for route in S.ROUTES:
        res = client.patch("/api/v1/settings", headers=h,
                           json={"extraction": dict(route.sets)})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["extraction_route"] == route.id
        stages = [s.name for s in pipeline_for(get_settings()).stages]
        assert ("whole_pdf" in stages) == (route.id == "whole_pdf"), route.id


def test_restore_defaults_returns_to_the_route_config_toml_ships(client):
    """config.toml ships the whole-PDF route on, so Restore defaults on the screen lands there
    again, whatever card was picked and saved before."""
    h = _admin(client)
    client.patch("/api/v1/settings", headers=h,
                 json={"extraction": {"whole_pdf_to_model": False, "llm_mapping": False}})
    assert client.get("/api/v1/settings", headers=h).json()["extraction_route"] == "rules"
    body = client.patch("/api/v1/settings", headers=h, json={"reset_extraction": True}).json()
    assert body["extraction_route"] == S.route_of(body["extraction_defaults"])
    assert body["extraction_defaults"]["whole_pdf_to_model"] is True
    assert body["extraction_route"] == "whole_pdf"


def test_every_option_on_the_route_tab_belongs_to_one_route():
    """Each knob on the route tab, other than the two switches, hangs (through `shown_when`, as far
    as it goes) off one route's switch — which is how the screen puts it under that card and no
    other. The rules-only route has no switch of its own and so no options."""
    fields = _fields()
    switches = {r.switch for r in S.ROUTES if r.switch}
    assert switches == {"llm_mapping", "whole_pdf_to_model"}

    def root(key: str) -> str:
        seen = set()
        while fields[key]["shown_when"] and key not in seen:
            seen.add(key)
            key = fields[key]["shown_when"]["key"]
        return key

    for key, f in fields.items():
        if f["tab"] != "route" or key in switches:
            continue
        assert root(key) in switches, f"{key} on the route tab belongs to no route"
    # …and nothing on the other tab hangs off a route switch, or it would show in two places.
    for key, f in fields.items():
        if f["tab"] != "route":
            assert root(key) not in switches, key


def test_the_whole_pdf_card_says_what_the_route_does_now():
    """The whole-PDF route no longer skips the extraction: the rules read the note tables
    alongside the model, check its figures and fill what it leaves empty, and a failure sends the
    whole filing to the fallback. The old label said "(skip extraction)"."""
    whole = next(r for r in S.ROUTES if r.id == "whole_pdf")
    fields = _fields()
    words = " ".join([whole.summary, whole.best_for, whole.cost, *whole.limits]).lower()
    assert "skip" not in words
    assert "skip extraction" not in fields["whole_pdf_to_model"]["label"].lower()
    assert "alongside" in words and "fallback" in words
    # Its fallback is the rules route, read with the rest of the values as they are.
    assert S.route_of({"llm_mapping": True, **dict(whole.fallback)}) == "questions"
    assert S.route_of({"llm_mapping": False, **dict(whole.fallback)}) == "rules"


def test_the_rules_card_does_not_promise_no_model_at_all():
    """"Rules only" sends no line-item question, but the extra calls (row suggestions,
    disclosures, gap routing, the contingent-liabilities wording) are gated only by their own
    switches, never by `llm_mapping` — so the card has to say they still go out."""
    import inspect

    from app.stages import contingent_liabilities, disclosure_reading, gap_closing, row_suggestions

    for module in (row_suggestions, disclosure_reading, gap_closing, contingent_liabilities):
        assert "llm_mapping" not in inspect.getsource(module), module.__name__
    rules = next(r for r in S.ROUTES if r.id == "rules")
    assert any("extra model calls" in limit.lower() for limit in rules.limits)


def test_a_choice_is_named_in_words_for_every_value_or_for_none():
    """A choice knob is offered as a list of plain-English options when it carries
    `choice_info`; then every value it takes has one, in the knob's own order."""
    for f in _fields().values():
        if f["kind"] != "choice" or not f["choice_info"]:
            continue
        assert [c["value"] for c in f["choice_info"]] == f["choices"], f["key"]
        for c in f["choice_info"]:
            assert c["label"] and len(c["label"]) <= 60, (f["key"], c["value"])
            assert c["note"] and len(c["note"]) <= 240, (f["key"], c["value"])
    assert _fields()["llm_document_context"]["choice_info"], "the route's main choice is named"


def test_a_section_is_folded_or_open_as_a_whole():
    """Folding is a property of a group of settings: a section half folded would hide some of its
    knobs behind a heading that says it is open."""
    by_section: dict[tuple[str, str], set[bool]] = {}
    for f in _fields().values():
        by_section.setdefault((f["tab"], f["section"]), set()).add(f["folded"])
    assert all(len(v) == 1 for v in by_section.values()), by_section
    # The everyday groups are open; the thresholds are folded.
    assert by_section[("advanced", "Extra model calls")] == {False}
    assert by_section[("advanced", "Matching captions")] == {True}
    assert by_section[("route", "What the model reads")] == {False}


def test_the_snapshot_says_whether_the_provider_can_be_sent_a_file(client, monkeypatch):
    """Asked as the whole-PDF stage asks it, so the screen can say before a run that every filing
    would be read by the fallback."""
    h = _admin(client)
    llm = get_settings().llm
    monkeypatch.setattr(llm, "provider", "stub")
    assert client.get("/api/v1/settings", headers=h).json()["llm"]["takes_files"] is False
    monkeypatch.setattr(llm, "provider", "anthropic")
    assert client.get("/api/v1/settings", headers=h).json()["llm"]["takes_files"] is True
    monkeypatch.setattr(llm, "provider", "no_such_provider")
    assert client.get("/api/v1/settings", headers=h).json()["llm"]["takes_files"] is False
