"""Service casing is equivalent for preparation, replay and recovery policy."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.execution_arguments import (
    execution_identity,
    prepare_service_arguments,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    action_requires_available_entities,
)


@pytest.mark.parametrize("name", ["execute_service_single", "execute_service"])
@pytest.mark.parametrize(
    "domain,service", [("LIGHT", "turn_on"), ("light", "TURN_ON"), ("LiGhT", "TuRn_On")]
)
def test_prepared_service_and_replay_identity_use_ha_canonical_names(
    name, domain, service
):
    raw = {
        "domain": domain,
        "service": service,
        "service_data": {"entity_id": "light.kitchen"},
    }
    saved = deepcopy(raw)
    canonical = {**raw, "domain": "light", "service": "turn_on"}
    assert prepare_service_arguments(raw) == prepare_service_arguments(canonical)
    if name == "execute_service":
        raw, canonical = {"list": [raw]}, {"list": [canonical]}
    assert execution_identity(
        {"type": "native", "name": name}, raw
    ) == execution_identity({"type": "native", "name": name}, canonical)
    assert (raw["list"][0] if name == "execute_service" else raw) == saved


@pytest.mark.parametrize(
    "domain,service,expected",
    [
        ("HOMEASSISTANT", "UPDATE_ENTITY", False),
        ("HomeAssistant", "Reload_Config_Entry", False),
        ("LIGHT", "TURN_ON", True),
        (None, None, True),
    ],
)
def test_recovery_availability_classification_is_case_insensitive(
    domain, service, expected
):
    assert action_requires_available_entities(domain, service) is expected
