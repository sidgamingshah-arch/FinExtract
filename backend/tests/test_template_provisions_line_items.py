"""A template provisions its own line items, and a template line's item cannot be deleted.

THE RULE, as it was given: "as soon as someone uploads a new template, all the line items from that
template should automatically flow into the line items, then the user can add more to it but not
delete any of the items present in the template."

WHAT WAS MISSING. `POST /templates` published a `TemplateVersion` and nothing else. A newly uploaded
template therefore arrived with NO configuration at all: every line served `mapped: false`, and an
author had to write four hundred-odd items by hand before the template could map anything. The
shipped pair was only ever in the right shape because a build script had produced both halves
together — nothing in the running app could reproduce it.

WHY THE PROTECTION IS SERVER-SIDE. A template line's item exists because the deliverable has a
column for that figure. Deleting it leaves the output with a line nothing can fill, and the loss
shows up as a blank cell rather than as an error. Hiding the button leaves the same delete one API
call away, so the refusal is tested here at the endpoint rather than asserted about the UI.
"""
from __future__ import annotations

import copy

import pytest

API = "/api/v1"

# Two figure-bearing lines under one section header, which is the smallest template that exercises
# the whole rule: a header must NOT get an item, and both lines must.
TEMPLATE = {
    "template_key": "prov_tpl",
    "name": "Provisioning probe",
    "statements": [{
        "type": "balance_sheet",
        "sections": [{
            "node_id": "bs_ca", "canonical_key": "bs_ca", "label": "Current Assets",
            "role": "header",
            "children": [
                {"node_id": "bs_ca__cash", "canonical_key": "bs_ca__cash", "label": "Cash",
                 "role": "line"},
                {"node_id": "bs_ca__stock", "canonical_key": "bs_ca__stock", "label": "Inventory",
                 "role": "line"},
            ],
        }],
    }],
}


def _publish_template(client, definition: dict) -> dict:
    r = client.post(f"{API}/templates", json={"definition": definition})
    assert r.status_code == 201, r.text
    return r.json()


def _set_in_force(client, template_key: str) -> dict:
    """The configuration a run against this template would read, with its full definition."""
    versions = client.get(f"{API}/line-items/versions").json()
    mine = [v for v in (versions if isinstance(versions, list) else versions.get("versions", []))
            if v.get("target_template_key") == template_key]
    assert mine, f"no line-item version targets {template_key}"
    latest = max(mine, key=lambda v: v["version"])
    r = client.get(f"{API}/line-items/versions/{latest['id']}")
    assert r.status_code == 200, r.text
    return r.json()


# ── provisioning ────────────────────────────────────────────────────────────────────────────────

def test_uploading_a_template_provisions_an_item_for_every_line(client):
    """The rule's first half. Two lines in, two items out."""
    published = _publish_template(client, TEMPLATE)

    assert published.get("line_item_version"), (
        "the upload reported no provisioned configuration, so nothing flowed into line items")
    got = _set_in_force(client, "prov_tpl")
    keys = {i["key"] for i in got["definition"]["items"]}
    assert keys == {"bs_ca__cash", "bs_ca__stock"}, keys


def test_the_section_header_gets_no_item(client):
    """A header names a section, not a figure.

    Provisioning one would create a row that can never hold a value and — under this rule — could
    never be deleted either. Measured on the shipped template: exactly the 18 `header` nodes of its
    480 keyed nodes have no item, and all 462 line/subtotal/total nodes have one.
    """
    _publish_template(client, TEMPLATE)
    got = _set_in_force(client, "prov_tpl")

    keys = {i["key"] for i in got["definition"]["items"]}
    assert "bs_ca" not in keys, "the section header was provisioned as a line item"
    # …and it becomes a section DEFAULT instead, which is what gives the items their gate.
    assert "bs_ca" in got["definition"]["section_defaults"]


def test_every_provisioned_item_inherits_a_section_that_exists(client):
    """The one configuration failure that is silent rather than loud.

    A dangling `inherits` is not a load error — the item validates and simply carries none of its
    section's statement or scope, so nothing can ever place it. Measured on the shipped set: 462 of
    462 items inherit their nearest header's canonical_key, and the 18 section_defaults keys are
    exactly those headers.
    """
    _publish_template(client, TEMPLATE)
    definition = _set_in_force(client, "prov_tpl")["definition"]

    sections = definition["section_defaults"]
    for item in definition["items"]:
        assert item.get("inherits"), f"{item['key']} inherits nothing, so it has no section gate"
        assert item["inherits"] in sections, f"{item['key']} inherits {item['inherits']!r}, absent"


def test_a_provisioned_item_is_in_the_template_namespace(client):
    """`namespace` is what the delete refusal keys on, so provisioning has to set it."""
    _publish_template(client, TEMPLATE)
    definition = _set_in_force(client, "prov_tpl")["definition"]

    assert {i["namespace"] for i in definition["items"]} == {"template"}


# ── adding ──────────────────────────────────────────────────────────────────────────────────────

def test_an_author_can_add_an_item_beyond_the_template(client):
    """The rule's second half: add, freely."""
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")

    r = client.post(f"{API}/line-items/versions/{version['id']}/items",
                    json={"key": "bs_ca__my_own", "label": "Something I track",
                          "inherits": "bs_ca"})
    assert r.status_code == 201, r.text

    after = _set_in_force(client, "prov_tpl")["definition"]
    mine = next(i for i in after["items"] if i["key"] == "bs_ca__my_own")
    assert mine["namespace"] == "internal", "an added item must be the author's, not the template's"


def test_an_added_item_is_not_in_the_output_until_somebody_says_so(client):
    """A new line is not part of the deliverable on creation.

    Defaulting `in_output` true would silently widen the delivered output on every addition — the
    template's own lines are the ones that are in it, and provisioning sets that for those.
    """
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")
    client.post(f"{API}/line-items/versions/{version['id']}/items", json={"key": "bs_ca__extra"})

    after = _set_in_force(client, "prov_tpl")["definition"]
    added = next(i for i in after["items"] if i["key"] == "bs_ca__extra")
    assert added["in_output"] is False
    # …whereas the template's own lines are.
    assert all(i["in_output"] for i in after["items"] if i["namespace"] == "template")


def test_adding_a_key_the_template_already_declares_is_refused(client):
    """That item exists; the author means to edit it. Creating a second one would give the set two
    entries for one output column."""
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")

    r = client.post(f"{API}/line-items/versions/{version['id']}/items",
                    json={"key": "bs_ca__cash"})

    assert r.status_code == 422, r.text
    assert "already exists" in r.text


# ── deleting ────────────────────────────────────────────────────────────────────────────────────

def test_deleting_a_template_line_is_refused(client):
    """The rule's third half, and the reason it is enforced here rather than in the UI."""
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")

    r = client.delete(f"{API}/line-items/versions/{version['id']}/items/bs_ca__cash")

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "template_item_protected"
    # And it really is still there.
    after = _set_in_force(client, "prov_tpl")["definition"]
    assert any(i["key"] == "bs_ca__cash" for i in after["items"])


def test_an_author_can_delete_their_own_item(client):
    """Protection is about the template, not about deletion being disallowed."""
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")
    client.post(f"{API}/line-items/versions/{version['id']}/items", json={"key": "bs_ca__mine"})
    version = _set_in_force(client, "prov_tpl")

    r = client.delete(f"{API}/line-items/versions/{version['id']}/items/bs_ca__mine")
    assert r.status_code == 200, r.text

    after = _set_in_force(client, "prov_tpl")["definition"]
    assert not any(i["key"] == "bs_ca__mine" for i in after["items"])


def test_deleting_something_that_is_not_there_is_a_404(client):
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")

    r = client.delete(f"{API}/line-items/versions/{version['id']}/items/no_such_key")
    assert r.status_code == 404, r.text


# ── re-upload ───────────────────────────────────────────────────────────────────────────────────

def test_a_reupload_keeps_everything_already_authored(client):
    """A template revision must not discard configuration. The aliases and criteria on an item are
    the expensive part."""
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")
    r = client.patch(f"{API}/line-items/versions/{version['id']}/items",
                     json={"key": "bs_ca__cash", "aliases": ["Cash at bank", "Cash on hand"]})
    assert r.status_code == 200, r.text

    _publish_template(client, TEMPLATE)                      # same template, published again

    after = _set_in_force(client, "prov_tpl")["definition"]
    cash = next(i for i in after["items"] if i["key"] == "bs_ca__cash")
    assert cash["aliases"] == ["Cash at bank", "Cash on hand"], (
        "re-uploading the template discarded authored aliases")


def test_a_line_the_new_template_drops_is_kept_and_demoted(client):
    """The user's decision, in their words: "keep it as internal".

    The authored work survives a template revision, AND the item becomes deletable — protection
    follows the template, so a line the template no longer demands is no longer protected. The
    author can then discard it deliberately, which is different from the system doing it for them.
    """
    _publish_template(client, TEMPLATE)
    version = _set_in_force(client, "prov_tpl")
    client.patch(f"{API}/line-items/versions/{version['id']}/items",
                 json={"key": "bs_ca__stock", "aliases": ["Inventories", "存货"]})

    shrunk = copy.deepcopy(TEMPLATE)
    kids = shrunk["statements"][0]["sections"][0]["children"]
    shrunk["statements"][0]["sections"][0]["children"] = [
        c for c in kids if c["canonical_key"] != "bs_ca__stock"]
    _publish_template(client, shrunk)

    after = _set_in_force(client, "prov_tpl")
    stock = next((i for i in after["definition"]["items"] if i["key"] == "bs_ca__stock"), None)
    assert stock is not None, "the dropped line's configuration was discarded"
    assert stock["namespace"] == "internal", "it should be demoted, not left protected"
    assert stock["aliases"] == ["Inventories", "存货"], "its authored aliases were lost"

    # …and being internal, it can now be deleted.
    r = client.delete(f"{API}/line-items/versions/{after['id']}/items/bs_ca__stock")
    assert r.status_code == 200, r.text


def test_a_line_the_new_template_adds_is_provisioned(client):
    """The other direction: a revision that grows the template grows the configuration."""
    _publish_template(client, TEMPLATE)

    grown = copy.deepcopy(TEMPLATE)
    grown["statements"][0]["sections"][0]["children"].append(
        {"node_id": "bs_ca__prepaid", "canonical_key": "bs_ca__prepaid", "label": "Prepayments",
         "role": "line"})
    _publish_template(client, grown)

    after = _set_in_force(client, "prov_tpl")["definition"]
    prepaid = next((i for i in after["items"] if i["key"] == "bs_ca__prepaid"), None)
    assert prepaid is not None, "the new template line was not provisioned"
    assert prepaid["namespace"] == "template"
    assert prepaid["inherits"] == "bs_ca"


# ── the shipped pair is the reference ───────────────────────────────────────────────────────────

def test_provisioning_reproduces_the_shipped_configuration_exactly():
    """The strongest available check that this is the SAME rule the shipped pair was built by.

    Run over the shipped template with the shipped set as the existing configuration, provisioning
    must be lossless and idempotent: same 476 items, the same 462/14 namespace split, and not one
    authored field altered. If this rule were even slightly different from the one the build script
    used, this is where it would show.
    """
    import json
    import pathlib

    from app.services.provision_line_items import protected_keys, provision

    d = pathlib.Path(__file__).resolve().parent.parent / "app" / "sample" / "templates"
    template = json.loads((d / "output_csv_hk_v1_template.json").read_text(encoding="utf-8"))
    shipped = json.loads((d / "output_csv_hk_line_items.json").read_text(encoding="utf-8"))

    merged = provision(template, shipped)

    before = {i["key"]: i for i in shipped["items"]}
    after = {i["key"]: i for i in merged["items"]}
    assert set(after) == set(before), "provisioning added or lost an item"
    # 527, not 543: the live configuration was exported into the shipped seed — see
    # `test_retired_derivations.test_the_shipped_set_is_the_configuration_in_force`.
    # 529 since the two 营业外 face parts split the template's single net non-operating column.
    # 531 since the two direct-method TAX face parts split the template's single Income Taxes Paid(Direct) column, as the two 营业外 parts before them split Other Non-Operating Inc(Exp)
    # 533 since Find 3 split into a gross half and an allowance half: the 关联方应收应付款项 note prints 账面余额 and 坏账准备 and no net column, so the 淨金額 the spec asks for is computed.
    assert len(after) == 538, len(after)   # 538 since the loss allowance split into its two readings — the 坏账准备 COLUMN of a measure grid and an allowance printed as its own ROW — which is what lets CP_P2 subtract it   # 536 since the other-receivables line gained the interest-and-dividends-receivable component its own definition names: a filing that prints 应收利息/应收股利 as siblings of 其他应收款 has not put them inside it   # 535 since the other-receivables NET split into the two ways a note prints one — the 账面价值 COLUMN of a gross/allowance/net grid, and a row whose own reported amount is the net; one part holds one `measure`, and 000709 needs both readings

    namespaces: dict[str, int] = {}
    for item in merged["items"]:
        namespaces[item["namespace"]] = namespaces.get(item["namespace"], 0) + 1
    # 65 internal: `sub__fa_cp_intermediate_residual` is off-template like every other part,
    # and provisioning must leave it alone rather than treat it as a template row to fill. 65 and
    # not 81 because sixteen parts were retired — see the census in `test_retired_derivations`.
    # 67 internal since the two 营业外 face parts were added — CAS prints non-operating income
    # and expense as two rows and the template holds one net column, so the halves are
    # parts. The template split is unchanged at 462, which is the property this pins.
    # 69 internal, not 67: the two direct-method TAX parts are `internal` like every other part —
    # `in_output: false`, so neither claims a template column.
    # 71 internal: Find 3's gross and allowance halves are `internal` like every other part —
    # `in_output: false`, so neither claims a template column.
    # 73 internal: the other-receivables net's two readings are `internal` like every other
    # part — `in_output: false`, so neither claims a template column, and the template split
    # is still 462.
    assert namespaces == {"template": 462, "internal": 76}, namespaces
    assert len(protected_keys(merged)) == 462

    for key, item in before.items():
        assert item == after[key], f"provisioning altered authored fields on {key}"
