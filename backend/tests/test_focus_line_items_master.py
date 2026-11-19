"""THE FOCUS LINE ITEMS MASTER: seeded from config.toml, kept from the Settings screen, and the list
a focus run reads (`services.focus_master`, `api/routes/focus_line_items.py`).

What these pin: the master starts as the shipped list and is the list `stages.line_item_llm` reads;
an admin adds and removes lines and the next run sees it; a line computed from its parts goes in with
the parts its cascade reads (it is never asked itself); a key no set in force names is refused; an
emptied master is empty, not the shipped list; only an admin changes it; and it survives a restart.
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest

from app.config import get_settings
from app.services import focus_master

BASE = "/api/v1/focus-line-items"
SHIPPED = list(dict.fromkeys(tomllib.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "config.toml").read_text(encoding="utf-8")
)["extraction"]["llm_focus_keys"]))


@pytest.fixture
def master(client):
    """The admin client, with the master put back to the shipped list before and after — it is
    process-wide, so a test must not inherit another's entries or leave its own behind."""
    assert client.post(f"{BASE}/restore").status_code == 200
    yield client
    assert client.post(f"{BASE}/restore").status_code == 200


def _items(client) -> dict:
    return {i["key"]: i for i in client.get(BASE).json()["items"]}


def _empty(client) -> None:
    for item in client.get(BASE).json()["items"]:
        assert client.delete(f"{BASE}/{item['id']}").status_code == 204


def test_the_master_is_the_shipped_list_and_the_list_a_run_reads(master):
    body = master.get(BASE).json()
    assert [i["key"] for i in body["items"]] == sorted(SHIPPED)
    assert {i["source"] for i in body["items"]} == {"shipped"}
    assert body["shipped"] == SHIPPED
    # Off as shipped: the list is kept, and used once an admin switches focus-only on.
    assert body["focus_only"] is get_settings().extraction.llm_focus_only
    assert sorted(get_settings().extraction.llm_focus_keys) == sorted(SHIPPED)
    # Every entry names a line of a set in force — the shipped list has no dead key.
    assert all(i["matched"] >= 1 for i in body["items"]), [
        i["key"] for i in body["items"] if not i["matched"]]


def test_an_admin_adds_a_line_and_the_next_run_reads_it(master):
    key = "bs_ca__cash_in_hand_and_at_banks"
    assert key not in SHIPPED
    r = master.post(BASE, json={"key": key, "note": "under review"})
    assert r.status_code == 201, r.text
    assert r.json()["added"] == [key]
    assert key in get_settings().extraction.llm_focus_keys
    row = _items(master)[key]
    assert (row["note"], row["source"], row["kind"], row["pattern"]) == (
        "under review", "admin", "asked", False)
    assert row["label"] and row["sets"] == ["output_csv_hk"]

    # Adding it again keeps one entry and only updates the note.
    again = master.post(BASE, json={"key": key, "note": "still under review"})
    assert again.status_code == 201 and again.json()["added"] == []
    assert _items(master)[key]["note"] == "still under review"

    assert master.delete(f"{BASE}/{row['id']}").status_code == 204
    assert key not in get_settings().extraction.llm_focus_keys
    assert master.delete(f"{BASE}/{row['id']}").status_code == 404


def test_a_line_computed_from_its_parts_goes_in_with_them(master):
    """Sales (Revenues) is derived: its figure is its cascade's, and no request asks about it. In
    focus alone it would restrict a run to a line nothing asks — so its parts go in with it."""
    _empty(master)
    r = master.post(BASE, json={"key": "is_pl__sales_revenues"})
    assert r.status_code == 201, r.text
    added = r.json()["added"]
    assert added[0] == "is_pl__sales_revenues"
    assert {"sub__revenue_note_principal_revenue", "sub__face_principal_revenue"} <= set(added)
    items = _items(master)
    # Computed in the HK set, asked directly in the Ind AS and US GAAP sets, which hold the same
    # key: the entry says so rather than reporting one set's answer as every set's.
    assert items["is_pl__sales_revenues"]["kind"] == "mixed"
    assert items["is_pl__sales_revenues"]["sets"] == [
        "output_csv_hk", "output_csv_indas", "output_csv_usgaap"]
    assert items["sub__face_principal_revenue"]["note"] == "part of is_pl__sales_revenues"
    assert all(items[k]["kind"] == "asked" for k in added[1:])

    # …unless the admin says otherwise.
    _empty(master)
    alone = master.post(BASE, json={"key": "is_pl__sales_revenues", "with_parts": False})
    assert alone.json()["added"] == ["is_pl__sales_revenues"]


def test_a_nested_computed_part_is_expanded_to_the_lines_that_are_asked(master):
    """Other receivables (CP) reads two computed parts of its own; the parts added are the lines
    under them that a request can answer, never a computed one."""
    _empty(master)
    added = master.post(BASE, json={"key": "bs_ca__other_receivables_cp"}).json()["added"]
    assert {"sub__cp_other_receivables_net_row", "sub__cp_other_receivables_rp_note"} <= set(added)
    assert "sub__cp_other_receivables_net" not in added      # computed itself: its parts instead
    lines = {ln.key: ln for ln in focus_master.lines_in_force(_Session())}
    assert all(lines[k].kind == "asked" for k in added[1:])


class _Session:
    """A session for a direct read, closed on exit of the test process — the master's own."""

    def __new__(cls):
        from app.db.base import SessionLocal

        return SessionLocal()


def test_a_prefix_pattern_names_every_line_it_begins(master):
    r = master.post(BASE, json={"key": "bs_ca__*", "note": "the whole of current assets"})
    assert r.status_code == 201, r.text
    row = _items(master)["bs_ca__*"]
    assert row["pattern"] is True and row["matched"] > 10
    assert "bs_ca__*" in get_settings().extraction.llm_focus_keys


@pytest.mark.parametrize("key, why", [
    ("bs_ca__no_such_line", "No line item"),
    ("zz_nothing__*", "No line item"),
    ("Not A Key", "is not a line-item key"),
    ("bs_ca__**", "is not a line-item key"),
    ("*", "is not a line-item key"),
    ("", "Give a line-item key"),
])
def test_a_key_no_set_in_force_names_is_refused(master, key, why):
    r = master.post(BASE, json={"key": key})
    assert r.status_code == 422, r.text
    assert why in r.json()["detail"]
    assert sorted(_items(master)) == sorted(SHIPPED)


def test_an_emptied_master_is_empty_and_the_shipped_list_comes_back_on_restore(master):
    _empty(master)
    assert master.get(BASE).json()["items"] == []
    # Empty is an answer — no line in focus — not "use the file".
    assert get_settings().extraction.llm_focus_keys == []
    body = master.post(f"{BASE}/restore").json()
    assert [i["key"] for i in body["items"]] == sorted(SHIPPED)
    assert sorted(get_settings().extraction.llm_focus_keys) == sorted(SHIPPED)


def test_only_an_administrator_changes_the_master(anon_client, auth, master):
    assert anon_client.get(BASE).status_code == 401
    for role in ("analyst", "reviewer"):
        assert anon_client.get(BASE, headers=auth(role)).status_code == 200
        assert anon_client.post(BASE, json={"key": "bs_ca__*"},
                                headers=auth(role)).status_code == 403
        assert anon_client.post(f"{BASE}/restore", headers=auth(role)).status_code == 403
    some = next(iter(_items(master).values()))
    assert anon_client.delete(f"{BASE}/{some['id']}", headers=auth("analyst")).status_code == 403
    assert sorted(_items(master)) == sorted(SHIPPED)


def test_the_master_survives_a_restart(master):
    """The list is re-applied at startup (`settings_state.load_persisted`), and the table is not
    re-seeded: `init_db` seeds it only when it creates it."""
    from app.db.base import init_db
    from app.services.settings_state import load_persisted

    master.post(BASE, json={"key": "bs_ca__cash_in_hand_and_at_banks"})
    gone = _items(master)[SHIPPED[0]]
    assert master.delete(f"{BASE}/{gone['id']}").status_code == 204

    get_settings().extraction.llm_focus_keys = ["something else"]
    init_db()
    load_persisted()
    live = get_settings().extraction.llm_focus_keys
    assert "bs_ca__cash_in_hand_and_at_banks" in live and SHIPPED[0] not in live
    assert SHIPPED[0] not in _items(master)


def test_the_candidates_are_the_lines_a_focus_run_can_reach(master):
    """Offered: the lines a request asks about, and the computed ones whose parts it can. Not
    offered: a line the app or a person fills, nor a section's "Others" bucket."""
    candidates = {(c["set"], c["key"]): c for c in master.get(BASE).json()["candidates"]}
    hk = {k: c for (s, k), c in candidates.items() if s == "output_csv_hk"}
    assert hk["sub__revenue_note_principal_revenue"]["kind"] == "asked"
    assert hk["is_pl__sales_revenues"]["kind"] == "computed"
    assert "sub__revenue_note_principal_revenue" in hk["is_pl__sales_revenues"]["parts"]
    assert "statement_setup_controls__analyst" not in hk          # a person fills it
    assert {c["kind"] for c in candidates.values()} == {"asked", "computed"}
