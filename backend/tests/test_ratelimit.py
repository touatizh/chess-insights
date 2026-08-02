"""Unit tests for the per-IP rate limiter (§5)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    ratelimit.reset_limits()
    yield
    ratelimit.reset_limits()


def test_new_job_allows_two_then_blocks() -> None:
    ip = "1.2.3.4"
    assert ratelimit.allow_new_job(ip) is True
    assert ratelimit.allow_new_job(ip) is True
    assert ratelimit.allow_new_job(ip) is False  # 3rd within the hour


def test_freshness_allows_ten_then_blocks() -> None:
    ip = "5.6.7.8"
    for _ in range(10):
        assert ratelimit.allow_freshness(ip) is True
    assert ratelimit.allow_freshness(ip) is False  # 11th within the hour


def test_limits_are_per_ip() -> None:
    assert ratelimit.allow_new_job("10.0.0.1") is True
    assert ratelimit.allow_new_job("10.0.0.1") is True
    assert ratelimit.allow_new_job("10.0.0.1") is False
    # A different IP has its own budget.
    assert ratelimit.allow_new_job("10.0.0.2") is True


def test_new_job_and_freshness_are_independent() -> None:
    ip = "9.9.9.9"
    # Exhaust new-job budget.
    assert ratelimit.allow_new_job(ip) is True
    assert ratelimit.allow_new_job(ip) is True
    assert ratelimit.allow_new_job(ip) is False
    # Freshness budget is untouched.
    assert ratelimit.allow_freshness(ip) is True
