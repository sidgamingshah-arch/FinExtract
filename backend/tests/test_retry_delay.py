"""A rate-limited provider tells you how long to wait. Waiting less is the same as not retrying.

THE DEFECT THIS FIXES, measured on a real filing. Every mapping call came back 429 with the wait
in the body — "Rate limit reached … on tokens per minute (TPM): Limit 70000, Used 64775, Requested
15931. Please try again in 9.176571428s" and, on the largest batch, 43.5 seconds. The adapter
backed off `min(2 ** attempt, 8)` over three attempts, so it gave up after about three seconds of
waiting, the whole run fell back to `strategy: "deterministic"` with `llm_calls: 0`, and a
full-capability extraction silently became the weaker one.

The provider had said exactly what to do. `Retry-After` is the standard place for it (RFC 9110)
and the body carried it too; the adapter read neither.

BLIND BACKOFF IS STILL RIGHT FOR THE OTHER RETRYABLE STATUSES. A 500 or a transport blip carries
no advice and exponential backoff is the correct policy — so the fallback is kept, not replaced.
"""
from __future__ import annotations

from app.adapters.openai_llm import _MAX_RETRY_WAIT, _retry_delay


class _Resp:
    """The two things `_retry_delay` reads off a response."""

    def __init__(self, headers: dict | None = None, text: str = ""):
        self.headers = headers or {}
        self.text = text


# ── the provider's own advice wins ───────────────────────────────────────────────────────────────

def test_a_retry_after_header_is_honoured():
    assert _retry_delay(_Resp({"retry-after": "12"}), attempt=0) == 12.0


def test_the_header_is_read_case_insensitively():
    """httpx normalises header case, but a stub or another client may not."""
    assert _retry_delay(_Resp({"Retry-After": "7"}), attempt=0) == 7.0


def test_the_seconds_named_in_the_body_are_used_when_there_is_no_header():
    """The exact message a token-per-minute limit returns."""
    body = ('{"error":{"message":"Rate limit reached for model `groq/compound` … on tokens per '
            'minute (TPM): Limit 70000, Used 64775, Requested 15931. Please try again in '
            '43.541142857s. Need more tokens? Upgrade to Dev Tier today"}}')

    got = _retry_delay(_Resp({}, body), attempt=0)

    assert 43.5 < got <= 45.0, "the wait the provider asked for was not used"


def test_the_body_wait_is_overshot_slightly_so_the_window_has_rolled():
    """Asking again at exactly the boundary races the provider's own clock."""
    got = _retry_delay(_Resp({}, "Please try again in 10s"), attempt=0)

    assert got > 10.0


def test_the_wait_is_capped_so_a_stage_cannot_block_indefinitely():
    """Past the ceiling, failing over to the deterministic path beats stalling the run."""
    assert _retry_delay(_Resp({"retry-after": "900"}), attempt=0) == _MAX_RETRY_WAIT
    assert _retry_delay(_Resp({}, "try again in 600s"), attempt=0) == _MAX_RETRY_WAIT


def test_a_negative_or_zero_header_does_not_produce_a_negative_sleep():
    assert _retry_delay(_Resp({"retry-after": "-5"}), attempt=0) == 0.0


# ── and blind backoff survives where there is no advice ──────────────────────────────────────────

def test_no_advice_falls_back_to_exponential_backoff():
    """A 500 or a transport blip carries no hint, and backoff is the right policy for it."""
    assert _retry_delay(_Resp({}, "internal server error"), attempt=0) == 1.0
    assert _retry_delay(_Resp({}, "internal server error"), attempt=2) == 4.0


def test_the_backoff_is_still_capped_at_eight_seconds():
    assert _retry_delay(_Resp({}, ""), attempt=10) == 8.0


def test_a_date_form_retry_after_falls_through_rather_than_raising():
    """RFC 9110 allows an HTTP-date. Unparseable as seconds — and a crash inside the retry path
    would turn a transient rate limit into a failed extraction."""
    got = _retry_delay(_Resp({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), attempt=1)

    assert got == 2.0


def test_a_missing_body_is_handled():
    class _NoText:
        headers: dict = {}
        text = None

    assert _retry_delay(_NoText(), attempt=0) == 1.0


# ── the status set this policy applies to ────────────────────────────────────────────────────────

def test_429_is_in_the_retryable_set():
    from app.adapters.openai_llm import _RETRYABLE_STATUS

    assert 429 in _RETRYABLE_STATUS
    # The others carry no advice and are the reason the backoff fallback stays.
    assert {500, 502, 503, 504} <= _RETRYABLE_STATUS
