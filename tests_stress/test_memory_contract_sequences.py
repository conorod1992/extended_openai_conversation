"""Longer reproducible contract sequences against HA's actual atomic Store."""

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
)
from tests.memory_sequence_contract import run_memory_sequence
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
async def test_seeded_memory_contract_against_real_storage(
    hass, stress_seed, stress_scale, stress_trace
):
    await run_memory_sequence(
        lambda: HomeAssistantMemoryStorage(hass, "sequence", "agent"),
        seed=stress_seed,
        steps=300 * stress_scale,
        trace=stress_trace,
    )
