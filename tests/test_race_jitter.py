"""Ensure race scheduling perturbations remain reproducible and opt-in."""

import asyncio

import pytest

from tests_stress.race_jitter import race_yield


@pytest.mark.asyncio
async def test_jitter_is_disabled_for_normal_campaigns(monkeypatch):
    monkeypatch.delenv("EOAI_RACE_JITTER", raising=False)
    trace = []
    await race_yield(100, "boundary", trace)
    assert trace == []


@pytest.mark.asyncio
async def test_jitter_is_repeatable_and_distinguishes_synchronization_points(monkeypatch):
    monkeypatch.setenv("EOAI_RACE_JITTER", "1")
    first, second, other = [], [], []
    await race_yield(7319, "provider-release", first)
    await race_yield(7319, "provider-release", second)
    await race_yield(7319, "unload", other)
    assert first == second
    assert len(first) == len(other) == 1
    assert first[0]["seed"] == 7319
    assert first[0]["delays_ms"]
    assert all(0 <= value <= 15 for value in first[0]["delays_ms"])
    assert first[0]["point"] != other[0]["point"]
