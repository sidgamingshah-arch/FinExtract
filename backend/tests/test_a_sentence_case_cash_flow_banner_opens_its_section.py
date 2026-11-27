"""A CASH-FLOW SECTION BANNER PRINTED IN SENTENCE CASE OPENS ITS SECTION.

NEW FILE -> backend/tests/test_a_sentence_case_cash_flow_banner_opens_its_section.py

Vivo Energy Mauritius 2025 heads the sections of its statement of cash flows "Cash flows from
operating activities", "Cash flows from investing activities" and "Cash flows from financing
activities" (printed page 69). Only the CAPS form was a banner: in sentence case the lead-in "Cash
flows from" was a word the vocabulary left over, so the line was exhausted by nothing and was no
banner at all. "Cash flows from investing activities" was glued onto "Proceeds from disposal of
property, plant and equipment", the investing section never opened, and "Interest received" 7,065 /
11,701 was read under the operating section — both sections failed their rollups by twice that
figure, and net cash from operating activities showed 526,769 / 855,510 against 512,639 / 832,108.

`mapping.section_of_banner_only` now takes the lead-in as part of the banner. The TOTAL, "Net cash
flows from investing activities", still leaves "net" over and stays a caption. The real page is
pinned in `test_vivo_energy_mauritius_statements_read_as_printed`.
"""
from __future__ import annotations

import pytest

from app.services.mapping import section_of_banner_only

OPERATING = "cash_flow_from_operating_activities"
INVESTING = "cash_flow_from_investing_activities"
FINANCING = "cash_flow_from_financing_activities"


@pytest.mark.parametrize("banner, section", [
    ("Cash flows from operating activities", OPERATING),
    ("Cash flows from investing activities", INVESTING),
    ("Cash flows from financing activities", FINANCING),
    ("Cash flows from/(used in) financing activities", FINANCING),
    ("Cash flow used in investing activities", INVESTING),
    ("CASH FLOWS FROM OPERATING ACTIVITIES", OPERATING),
])
def test_the_lead_in_is_part_of_the_banner(banner, section):
    assert section_of_banner_only(banner) == section


@pytest.mark.parametrize("caption", [
    "Net cash flows from investing activities",
    "Net cash generated from operating activities",
    "Net cash used in financing activities",
    "Cash flows from investing activities Proceeds from disposal of property, plant and equipment",
])
def test_a_total_or_a_caption_glued_to_the_banner_is_not_a_banner(caption):
    assert section_of_banner_only(caption) is None
