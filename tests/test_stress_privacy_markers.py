"""Synthetic provider-wire canaries remain unique and storable."""

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from tests_stress.test_provider_wire_privacy import _private_memory_marker
from tests.test_memory import FakeStorage


@pytest.mark.parametrize("seed", [0, 1, 2**32 - 1, 127920261106, 2**128 - 1])
async def test_private_wire_markers_survive_storage(seed):
    manager = PersistentMemory(FakeStorage())
    await manager.async_initialize()
    markers = set()
    for number in range(40):
        user = f"wire-owner-{number:02d}"
        marker = _private_memory_marker(user, seed)
        result = await manager.async_add(user, f"The calibration token for {user} is {marker}.", "general", "explicit")
        assert result["status"] == "created"
        markers.add(marker)
    assert len(markers) == 40
    assert markers.isdisjoint(
        _private_memory_marker(f"wire-owner-{number:02d}", seed + 1)
        for number in range(40)
    )


async def test_numeric_wire_marker_is_also_storable():
    manager = PersistentMemory(FakeStorage())
    await manager.async_initialize()
    result = await manager.async_add(
        "wire-owner-09", "The calibration token for wire-owner-09 is PRIVATE-MEMORY-wire-owner-09-127920261106.",
        "general", "explicit",
    )
    assert result["status"] == "created"
