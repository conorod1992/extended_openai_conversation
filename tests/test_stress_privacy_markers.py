"""Privacy-isolation fixtures must not accidentally become financial secrets."""

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    MemoryPrivacyRejected,
    _validate_privacy,
)
from tests_stress.test_provider_wire_privacy import _private_memory_marker


@pytest.mark.parametrize("seed", [0, 1, 2**32 - 1, 127920261106, 2**128 - 1])
def test_private_wire_markers_survive_the_real_privacy_boundary(seed):
    markers = set()
    for number in range(40):
        user = f"wire-owner-{number:02d}"
        marker = _private_memory_marker(user, seed)
        _validate_privacy(f"The calibration token for {user} is {marker}.", "explicit")
        markers.add(marker)
    assert len(markers) == 40
    assert markers.isdisjoint(
        _private_memory_marker(f"wire-owner-{number:02d}", seed + 1)
        for number in range(40)
    )


def test_former_numeric_marker_still_triggers_financial_privacy_protection():
    # This exact supported replay seed failed before the provider-wire journey
    # began. Do not make it pass by weakening the production privacy guard.
    with pytest.raises(MemoryPrivacyRejected, match="financial credential"):
        _validate_privacy(
            "The calibration token for wire-owner-09 is "
            "PRIVATE-MEMORY-wire-owner-09-127920261106.",
            "explicit",
        )
