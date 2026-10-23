"""A request's text must not depend on the process's string-hash seed.

THE DEFECT. `ContextPool._score` summed IDF weights over SETS — the tokens a probe and a heading
share, the probe's own tokens, the heading's tokens. A set of strings iterates in the order of the
strings' hashes, and `PYTHONHASHSEED` changes those from one process to the next (it is random by
default). A float sum taken in another order can differ in its last bit, so two notes the probe
scores equally in exact arithmetic came out a bit apart, and the sort that is meant to break ties by
document order broke them by that bit instead. Measured: 000883's (and 000685's) depreciation part
`sub__expense_notes_depreciation` named its notes 七、63 / 七、64 / 七、65 in one capture and
七、64 / 七、63 / 七、65 in another of the same code and configuration, which changes the request id
and strands the answer recorded for it.

THE TEST RUNS IN CHILD PROCESSES because the hash seed is fixed for the life of a process: the only
way to see two orders is two interpreters. Seed 0 reorders the pair below at b1e309a.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Two notes whose headings score EQUALLY against the probe in exact arithmetic: each word of the
# first has a twin in the second printed in exactly as many headings, so every IDF weight of one is
# a weight of the other. Filler headings set those frequencies to 1..4, which gives the sums enough
# distinct terms for their order to matter.
_TIE = r"""
import json
from types import SimpleNamespace as NS
from app.services import line_item_notes as lin
L = "bcdefghijklmnopq"
A = ["alpha" + L[i] * 2 for i in range(9)]
B = ["bravo" + L[i] * 2 for i in range(9)]
notes = [NS(note_number="1", title=" ".join(A), items=()),
         NS(note_number="2", title=" ".join(B), items=())]
n = 3
for i in range(9):
    for _ in range(i % 4):
        notes.append(NS(note_number=str(n), title=A[i] + " " + B[i] + " filler" + L[n % 16] * 3,
                        items=()))
        n += 1
pool = lin.header_pool(notes)
item = NS(key="k", label="", note_source=NS(note_terms=[" ".join(A + B)]), aliases=(),
          aliases_i18n={})
hits = lin.notes_for_line_item(item, pool, min_score=0.0, cap=50)
probe = set(A + B)
print(json.dumps({"order": [h.note for h in hits],
                  "scores": [repr(pool._score(u, probe)) for u in pool.units]}))
"""

SEEDS = ("0", "1", "2", "3", "7", "42")


def _run(code: str, seed: str) -> dict:
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(BACKEND)}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def tie_runs() -> dict[str, dict]:
    return {seed: _run(_TIE, seed) for seed in SEEDS}


def test_scores_are_the_same_bits_under_every_hash_seed(tie_runs):
    first = tie_runs[SEEDS[0]]["scores"]
    for seed, run in tie_runs.items():
        assert run["scores"] == first, f"PYTHONHASHSEED={seed} scored differently"


def test_equal_scores_are_equal_bits(tie_runs):
    # The two notes score the same in exact arithmetic, so they must score the same float — or
    # the tie-break by document order never gets to run.
    for seed, run in tie_runs.items():
        assert run["scores"][0] == run["scores"][1], f"PYTHONHASHSEED={seed}"


def test_a_tie_keeps_document_order_under_every_hash_seed(tie_runs):
    for seed, run in tie_runs.items():
        assert run["order"][:2] == ["1", "2"], f"PYTHONHASHSEED={seed}: {run['order'][:2]}"


# `content_terms` ranks a line's row terms longest first; terms of ONE length came out in the order
# of the set they were collected into. Only the length is read today, but the list is an order a
# caller can see, and it must not be the hash seed's.
_TERMS = r"""
import json
from types import SimpleNamespace as NS
from app.services import line_item_notes as lin
terms = ["deltaa", "alphaa", "charli", "bravoo", "echooo", "foxtro", "golfff", "hotell", "折旧", "摊销"]
item = NS(note_source=NS(row_terms=terms, row_caption_any=[]))
print(json.dumps({"order": lin.content_terms(item)}, ensure_ascii=False))
"""


def test_row_terms_of_one_length_are_in_a_fixed_order_under_every_hash_seed():
    runs = {seed: _run(_TERMS, seed)["order"] for seed in SEEDS}
    want = sorted(runs[SEEDS[0]], key=lambda t: (-len(t), t))
    for seed, order in runs.items():
        assert order == want, f"PYTHONHASHSEED={seed}: {order}"
