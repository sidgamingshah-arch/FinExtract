"""THE FOCUS LINE ITEMS MASTER, PER TEMPLATE: seeded from config.toml, kept from the Settings screen,
and the lists a focus run reads (`services.focus_master`, `api/routes/focus_line_items.py`).

What these pin: each template's list starts as its shipped list and is the list a run of that
template reads, and no other template's; an admin adds and removes lines on one template and the
next run of it sees it, while a template sharing the key does not; a line computed from its parts
goes in with the parts its cascade reads (it is never asked itself); a key the template's set does
not name is refused; an emptied list is empty, not the shipped list; only an admin changes it; it
survives a restart; and a database that kept the single list has it carried over, each entry under
every template whose set names it.
"""
from __future__ import annotations

import pathlib
import tomllib
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import focus_master, line_item_requests

BASE = "/api/v1/focus-line-items"
HK, ICON, INDAS, USGAAP = ("output_csv_hk_v1", "output_csv_icon_v1", "output_csv_indas_v1",
                           "output_csv_usgaap_v1")
SHIPPED = {t: list(dict.fromkeys(keys)) for t, keys in tomllib.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8")
)["extraction"]["llm_focus_keys"].items()}


def _restore_all(client) -> None:
    for template in SHIPPED:
        assert client.post(f"{BASE}/restore", params={"template": template}).status_code == 200


@pytest.fixture
def master(client):
    """The admin client, with every template's list put back to the shipped one before and after —
    the master is process-wide, so a test must not inherit another's entries or leave its own."""
    _restore_all(client)
    yield client
    _restore_all(client)


def _items(client, template=HK) -> dict:
    return {i["key"]: i for i in client.get(BASE, params={"template": template}).json()["items"]}


def _empty(client, template=HK) -> None:
    for item in client.get(BASE, params={"template": template}).json()["items"]:
        assert client.delete(f"{BASE}/{item['id']}").status_code == 204


def _live(template) -> list[str]:
    return list(get_settings().extraction.llm_focus_keys.get(template, []))


def test_each_template_has_its_shipped_list_and_it_is_the_list_its_runs_read(master):
    assert set(SHIPPED) == {HK, ICON, INDAS, USGAAP}
    body = master.get(BASE).json()
    assert [t["key"] for t in body["templates"]] == sorted(SHIPPED)
    for t in body["templates"]:
        assert t["entries"] == t["shipped"] == len(SHIPPED[t["key"]]), t
        assert t["name"] and t["set"]
    for template, keys in SHIPPED.items():
        got = master.get(BASE, params={"template": template}).json()
        assert got["template"] == template
        assert [i["key"] for i in got["items"]] == sorted(keys)
        assert {i["source"] for i in got["items"]} == {"shipped"}
        assert got["shipped"] == keys
        # Every entry names a line of the template's own set — no shipped list has a dead key.
        assert all(i["matched"] >= 1 for i in got["items"]), [
            i["key"] for i in got["items"] if not i["matched"]]
        assert sorted(_live(template)) == sorted(keys)
    # Off as shipped: the lists are kept, and used once an admin switches focus-only on.
    assert body["focus_only"] is get_settings().extraction.llm_focus_only


def test_a_line_put_in_focus_for_one_template_is_not_in_focus_for_another(master):
    """Inventories is a line of the HK, Ind AS and US GAAP sets alike. With one list for every
    template, putting it in focus for HK put it in focus for all three."""
    key = "bs_ca__inventories"
    assert key not in SHIPPED[HK] and key not in SHIPPED[INDAS]
    r = master.post(BASE, json={"template": HK, "key": key, "note": "under review"})
    assert r.status_code == 201, r.text
    assert r.json()["added"] == [key] and r.json()["template"] == HK
    assert key in _live(HK)
    assert key not in _live(INDAS) and key not in _live(USGAAP)
    assert key not in _items(master, INDAS)

    # The same key on another template is an entry of its own, with its own note.
    r = master.post(BASE, json={"template": INDAS, "key": key, "note": "Ind AS review"})
    assert r.status_code == 201, r.text
    assert _items(master, HK)[key]["note"] == "under review"
    assert _items(master, INDAS)[key]["note"] == "Ind AS review"

    # Removing it from one leaves the other's.
    assert master.delete(f"{BASE}/{_items(master, HK)[key]['id']}").status_code == 204
    assert key not in _live(HK) and key in _live(INDAS)


def test_an_admin_adds_a_line_and_the_next_run_reads_it(master):
    key = "bs_ca__cash_in_hand_and_at_banks"
    assert key not in SHIPPED[HK]
    r = master.post(BASE, json={"template": HK, "key": key, "note": "under review"})
    assert r.status_code == 201, r.text
    assert r.json()["added"] == [key]
    assert key in _live(HK)
    row = _items(master)[key]
    assert (row["note"], row["source"], row["kind"], row["pattern"]) == (
        "under review", "admin", "asked", False)
    assert row["label"] and row["sets"] == ["output_csv_hk"] and row["template"] == HK

    # Adding it again keeps one entry and only updates the note.
    again = master.post(BASE, json={"template": HK, "key": key, "note": "still under review"})
    assert again.status_code == 201 and again.json()["added"] == []
    assert _items(master)[key]["note"] == "still under review"

    assert master.delete(f"{BASE}/{row['id']}").status_code == 204
    assert key not in _live(HK)
    assert master.delete(f"{BASE}/{row['id']}").status_code == 404


def test_a_line_computed_from_its_parts_goes_in_with_them(master):
    """Sales (Revenues) is derived in the HK set: its figure is its cascade's, and no request asks
    about it. In focus alone it would restrict a run to a line nothing asks — so its parts go in
    with it. The Ind AS set asks about the same key directly, and gets it alone."""
    _empty(master)
    r = master.post(BASE, json={"template": HK, "key": "is_pl__sales_revenues"})
    assert r.status_code == 201, r.text
    added = r.json()["added"]
    assert added[0] == "is_pl__sales_revenues"
    assert {"sub__revenue_note_principal_revenue", "sub__face_principal_revenue"} <= set(added)
    items = _items(master)
    assert items["is_pl__sales_revenues"]["kind"] == "computed"
    assert items["sub__face_principal_revenue"]["note"] == "part of is_pl__sales_revenues"
    assert all(items[k]["kind"] == "asked" for k in added[1:])

    _empty(master, INDAS)
    r = master.post(BASE, json={"template": INDAS, "key": "is_pl__sales_revenues"})
    assert r.json()["added"] == ["is_pl__sales_revenues"]
    assert _items(master, INDAS)["is_pl__sales_revenues"]["kind"] == "asked"

    # …unless the admin says otherwise.
    _empty(master)
    alone = master.post(BASE, json={"template": HK, "key": "is_pl__sales_revenues",
                                    "with_parts": False})
    assert alone.json()["added"] == ["is_pl__sales_revenues"]


def test_a_nested_computed_part_is_expanded_to_the_lines_that_are_asked(master):
    """Other receivables (CP) reads two computed parts of its own; the parts added are the lines
    under them that a request can answer, never a computed one."""
    _empty(master)
    added = master.post(BASE, json={"template": HK,
                                    "key": "bs_ca__other_receivables_cp"}).json()["added"]
    assert {"sub__cp_other_receivables_net_row", "sub__cp_other_receivables_rp_note"} <= set(added)
    assert "sub__cp_other_receivables_net" not in added      # computed itself: its parts instead
    from app.db.base import SessionLocal

    with SessionLocal() as s:
        lines = {ln.key: ln for ln in focus_master.lines_in_force(s, HK)}
    assert all(lines[k].kind == "asked" for k in added[1:])


def test_a_prefix_pattern_names_every_line_of_its_template_it_begins(master):
    r = master.post(BASE, json={"template": HK, "key": "bs_ca__*",
                                "note": "the whole of current assets"})
    assert r.status_code == 201, r.text
    row = _items(master)["bs_ca__*"]
    assert row["pattern"] is True and row["matched"] > 10 and row["sets"] == ["output_csv_hk"]
    assert "bs_ca__*" in _live(HK) and "bs_ca__*" not in _live(INDAS)


@pytest.mark.parametrize("template, key, why", [
    (HK, "bs_ca__no_such_line", "No line item"),
    (HK, "zz_nothing__*", "No line item"),
    # A line of ANOTHER template's set is not one of this template's.
    (HK, "bs_icon__*", "No line item of this template"),
    (ICON, "bs_ca__inventories", "No line item of this template"),
    (HK, "Not A Key", "is not a line-item key"),
    (HK, "bs_ca__**", "is not a line-item key"),
    (HK, "*", "is not a line-item key"),
    (HK, "", "Give a line-item key"),
    ("no_such_template", "bs_ca__inventories", "is not a template"),
])
def test_a_key_the_templates_set_does_not_name_is_refused(master, template, key, why):
    r = master.post(BASE, json={"template": template, "key": key})
    assert r.status_code == 422, r.text
    assert why in r.json()["detail"]
    assert sorted(_items(master)) == sorted(SHIPPED[HK])


def test_an_unknown_template_is_not_found_on_read_or_restore(master):
    assert master.get(BASE, params={"template": "no_such_template"}).status_code == 404
    assert master.post(f"{BASE}/restore",
                       params={"template": "no_such_template"}).status_code == 404


def test_an_emptied_list_is_empty_and_its_shipped_list_comes_back_on_restore(master):
    _empty(master)
    assert master.get(BASE, params={"template": HK}).json()["items"] == []
    # Empty is an answer — no line in focus — not "use the file". Other templates are untouched.
    assert _live(HK) == []
    assert sorted(_live(ICON)) == sorted(SHIPPED[ICON])
    body = master.post(f"{BASE}/restore", params={"template": HK}).json()
    assert [i["key"] for i in body["items"]] == sorted(SHIPPED[HK])
    assert sorted(_live(HK)) == sorted(SHIPPED[HK])


def test_only_an_administrator_changes_the_master(anon_client, auth, master):
    assert anon_client.get(BASE).status_code == 401
    for role in ("analyst", "reviewer"):
        assert anon_client.get(BASE, headers=auth(role)).status_code == 200
        assert anon_client.post(BASE, json={"template": HK, "key": "bs_ca__*"},
                                headers=auth(role)).status_code == 403
        assert anon_client.post(f"{BASE}/restore", params={"template": HK},
                                headers=auth(role)).status_code == 403
    some = next(iter(_items(master).values()))
    assert anon_client.delete(f"{BASE}/{some['id']}", headers=auth("analyst")).status_code == 403
    assert sorted(_items(master)) == sorted(SHIPPED[HK])


def test_the_master_survives_a_restart(master):
    """The lists are re-applied at startup (`settings_state.load_persisted`), and the table is not
    re-seeded: `init_db` seeds it only when it creates it."""
    from app.db.base import init_db
    from app.services.settings_state import load_persisted

    master.post(BASE, json={"template": HK, "key": "bs_ca__cash_in_hand_and_at_banks"})
    gone = _items(master)[SHIPPED[HK][0]]
    assert master.delete(f"{BASE}/{gone['id']}").status_code == 204

    get_settings().extraction.llm_focus_keys = {HK: ["something else"]}
    init_db()
    load_persisted()
    live = _live(HK)
    assert "bs_ca__cash_in_hand_and_at_banks" in live and SHIPPED[HK][0] not in live
    assert SHIPPED[HK][0] not in _items(master)
    assert sorted(_live(ICON)) == sorted(SHIPPED[ICON])


def test_the_candidates_are_the_templates_own_lines_a_focus_run_can_reach(master):
    """Offered: the lines of the template's set a request asks about, and the computed ones whose
    parts it can. Not offered: a line the app or a person fills, nor another template's line."""
    body = master.get(BASE, params={"template": HK}).json()
    hk = {c["key"]: c for c in body["candidates"]}
    assert {c["set"] for c in body["candidates"]} == {"output_csv_hk"}
    assert {c["template"] for c in body["candidates"]} == {HK}
    assert hk["sub__revenue_note_principal_revenue"]["kind"] == "asked"
    assert hk["is_pl__sales_revenues"]["kind"] == "computed"
    assert "sub__revenue_note_principal_revenue" in hk["is_pl__sales_revenues"]["parts"]
    assert "statement_setup_controls__analyst" not in hk          # a person fills it
    assert {c["kind"] for c in body["candidates"]} == {"asked", "computed"}
    icon = master.get(BASE, params={"template": ICON}).json()["candidates"]
    assert icon and {c["set"] for c in icon} == {"output_csv_icon"}


def test_a_run_reads_its_own_templates_list():
    hk_set = SimpleNamespace(target_template_key=HK, items=[])
    indas_set = SimpleNamespace(target_template_key=INDAS, items=[])
    lists = {HK: ["bs_ca__inventories"], INDAS: ["bs_nca__goodwill"]}
    assert line_item_requests.focus_list(lists, hk_set) == ["bs_ca__inventories"]
    assert line_item_requests.focus_list(lists, indas_set) == ["bs_nca__goodwill"]
    # A template with no list has no line in focus.
    assert line_item_requests.focus_list(lists, SimpleNamespace(target_template_key=ICON)) == []
    assert line_item_requests.focus_keys(lists, hk_set) == {"bs_ca__inventories"}
    # Written as one list, it serves every template (a test or an environment pinning one).
    assert line_item_requests.focus_list(["k"], indas_set) == ["k"]


def test_a_database_that_kept_the_single_list_has_it_carried_over_per_template(master):
    """Each entry of the single list goes under every template whose set names it — the runs that
    read it until now — with its note; one naming no line of any set is dropped; the single list's
    table goes."""
    from sqlalchemy import inspect, text

    from app.db.base import SessionLocal, engine, init_db

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE focus_line_items_by_template"))
        conn.execute(text(
            'CREATE TABLE focus_line_items (id VARCHAR(36) PRIMARY KEY, "key" VARCHAR(160) UNIQUE, '
            "note VARCHAR(256), source VARCHAR(16), created_at DATETIME, updated_at DATETIME)"))
        for i, (key, note, source) in enumerate([
                ("bs_ca__inventories", "credit review", "admin"),
                ("bs_icon__*", "", "shipped"),
                ("zz_retired__line", "", "admin")]):
            conn.execute(text('INSERT INTO focus_line_items (id, "key", note, source) '
                              "VALUES (:i, :k, :n, :s)"), {"i": str(i), "k": key, "n": note,
                                                           "s": source})
    init_db()
    assert "focus_line_items" not in inspect(engine).get_table_names()
    with SessionLocal() as s:
        lists = focus_master.lists(s)
    assert lists == {HK: ["bs_ca__inventories"], INDAS: ["bs_ca__inventories"],
                     USGAAP: ["bs_ca__inventories"], ICON: ["bs_icon__*"]}
    assert _items(master, INDAS)["bs_ca__inventories"]["note"] == "credit review"
    assert _items(master, ICON)["bs_icon__*"]["source"] == "shipped"
