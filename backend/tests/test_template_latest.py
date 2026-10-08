"""GET /templates: newest first per key, and each row says whether it is the latest."""
from __future__ import annotations

import uuid


def _publish(session, key: str, version: int, published: bool = False):
    from app.db.models import TemplateVersion

    row = TemplateVersion(template_key=key, name=f"{key} v{version}", version=version,
                          definition={"template_key": key, "name": key, "statements": []},
                          is_published=published)
    session.add(row)
    session.flush()
    return row.id


def test_the_list_is_newest_first_and_names_the_latest_version(client):
    """THE TWO DEFECTS THIS CLOSES, both caused by serving versions unordered and unlabelled.

    The route had no ``ORDER BY``, so rows came back in insertion order — oldest first — and every
    client picked a template with ``find(x => x.template_key === key)``, which is the FIRST row with
    that key. So three screens named v1 as the active template however many revisions existed, and the
    extraction view resolved a run's ``template_version_id`` the same way: a re-extraction ran against
    the OLDEST template and could not produce a revised statement order. Selecting was worse than
    ineffective — every row in the picker carried the same key, so choosing v2 stored the key already
    held and the list re-answered v1.

    Versions are inserted OUT of order here (2, then 4, then 1) so insertion order cannot be mistaken
    for the ordering under test.
    """
    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion

    key = f"tk-latest-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        ids = {2: _publish(s, key, 2), 4: _publish(s, key, 4, True), 1: _publish(s, key, 1)}
        s.commit()
    try:
        rows = [r for r in client.get("/api/v1/templates").json() if r["template_key"] == key]
        assert [r["version"] for r in rows] == [4, 2, 1], rows
        assert [r["version"] for r in rows if r["is_latest"]] == [4]
        # The flag is on the row a client would DEFAULT to, and it is the newest — not the first
        # inserted, which is v2 here.
        assert next(r["id"] for r in rows if r["is_latest"]) == ids[4]
        # Being published is a DIFFERENT question from being latest: the shipped v1 is published and
        # an edited draft is not, and a picker defaulting to "published" would name the older one.
        assert next(r["is_published"] for r in rows if r["version"] == 4) is True
        assert next(r["is_published"] for r in rows if r["version"] == 1) is False
    finally:
        with SessionLocal() as s:
            for i in ids.values():
                s.delete(s.get(TemplateVersion, i))
            s.commit()


def test_exactly_one_version_per_key_is_latest(client):
    """A flag several rows wear is not a choice. Checked across every key the server holds, so a
    second template's versions cannot both claim it."""
    rows = client.get("/api/v1/templates").json()
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r["template_key"], []).append(r)
    assert by_key, "no templates stored at all"
    for key, group in by_key.items():
        latest = [r for r in group if r["is_latest"]]
        assert len(latest) == 1, (key, [(r["version"], r["is_latest"]) for r in group])
        assert latest[0]["version"] == max(r["version"] for r in group), key


def test_the_shipped_template_is_served_latest_first(client):
    """The real one, not a fixture: whatever revisions the shipped template has accumulated, the
    first row for its key is the newest — which is what every screen now defaults to."""
    rows = [r for r in client.get("/api/v1/templates").json()
            if r["template_key"] == "hkfrs_hk_china_v1"]
    assert rows, "the shipped template is not stored"
    assert rows[0]["version"] == max(r["version"] for r in rows)
    assert rows[0]["is_latest"] is True


def test_each_row_says_whether_a_run_on_it_maps_against_a_configuration(client):
    """`configured`: a line-item set is in force for the row's key.

    Served because the Upload and extraction screens must default to a template a run can map
    against, and a reader without `config:line_items` (an analyst) cannot list configurations to
    find one. Without it the analyst's run went out naming no template, and the server laid it out
    on whichever template was stored last — ICON, on a fresh database — whatever the picker showed.
    """
    from app.db.base import SessionLocal
    from app.db.models import TemplateVersion

    bare = f"tk-bare-{uuid.uuid4().hex[:8]}"          # a template no line-item set targets
    with SessionLocal() as s:
        _publish(s, bare, 1, True)
        s.commit()
    try:
        rows = {r["template_key"]: r for r in client.get("/api/v1/templates").json()
                if r["is_latest"]}
        assert rows[bare]["configured"] is False
        for key in ("output_csv_hk_v1", "output_csv_indas_v1", "output_csv_icon_v1"):
            assert rows[key]["configured"] is True, key
    finally:
        with SessionLocal() as s:
            s.query(TemplateVersion).filter(TemplateVersion.template_key == bare).delete()
            s.commit()
