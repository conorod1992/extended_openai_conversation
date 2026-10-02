"""Controlled failures run only by the manual/nightly diagnostics campaign."""

import os

import pytest
import yaml
from pytest_homeassistant_custom_component.common import MockUser

from ci.enhanced_evidence import CANARIES, fresh_privacy_canaries
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from tests_stress.conftest import record
from tests_stress.provider_fault_transport import ProviderFaultTransport, WireStep
from tests_stress.test_lifecycle_matrix import _entry
from tests_stress.test_provider_fault_matrix import _OWNER, _agent, _say
from tests_stress.test_runtime_soak import _resource_footprint


def _begin(trace, boundary):
    fresh = fresh_privacy_canaries(os.environ.get("STRESS_SEED", "unknown"))
    record(
        trace,
        "begin",
        boundary=boundary,
        api_key=fresh[0],
        authorization=fresh[1],
        diagnostic_marker="USEFUL-PRIVACY-DIAGNOSTIC",
        artifact_privacy_probes=1,
    )
    try:
        yaml.safe_load(f"api_key: [{fresh[0]}")
    except yaml.YAMLError as err:
        record(
            trace,
            "configuration_error",
            api_key=CANARIES[0],
            error=str(err),
            diagnostic_marker="USEFUL-PRIVACY-DIAGNOSTIC",
        )


def _fail(trace, boundary):
    fresh = fresh_privacy_canaries(os.environ.get("STRESS_SEED", "unknown"))
    record(
        trace,
        "before_assertion",
        private_content=CANARIES[1],
        knowledge_content=CANARIES[2],
        prompt=CANARIES[3],
        user_text=fresh[2],
        prompt_content=fresh[3],
    )
    pytest.fail(
        f"controlled {boundary} failure USEFUL-PRIVACY-DIAGNOSTIC {fresh[0]} {fresh[1]}"
    )


def test_python_failure_artifact_survives_partial_trace(stress_trace):
    _begin(stress_trace, "python")
    _fail(stress_trace, "python")


async def test_provider_failure_artifact_has_real_assist_fault_phase(
    hass, monkeypatch, stress_trace
):
    _begin(stress_trace, "provider")
    MockUser(id=_OWNER, name="Diagnostics provider owner", is_owner=True).add_to_hass(
        hass
    )
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    wire = ProviderFaultTransport([WireStep("transport", error="dns")])
    wire.install(monkeypatch, agent)
    record(stress_trace, "fault", phase="before_headers", kind="dns")
    failed = await _say(hass, agent, "Diagnose a provider DNS failure")
    assert failed.response.error_code is not None
    record(stress_trace, "assist_failure", provider_requests=len(wire.requests))
    from tests_real_ha.test_provider_wire_e2e import _install_wire

    fresh = fresh_privacy_canaries(os.environ.get("STRESS_SEED", "unknown"))
    provider_error = _install_wire(
        monkeypatch,
        agent,
        [
            (
                400,
                {
                    "error": {
                        "message": f"USEFUL-PRIVACY-DIAGNOSTIC provider rejected {fresh[0]}",
                        "type": "invalid_request_error",
                    }
                },
            )
        ],
    )
    rejected = await _say(hass, agent, "Diagnose provider error payload")
    assert (
        rejected.response.error_code is not None and len(provider_error.requests) == 1
    )
    record(
        stress_trace,
        "provider_error_payload",
        provider_requests=1,
        error=f"USEFUL-PRIVACY-DIAGNOSTIC {fresh[0]}",
    )
    _fail(stress_trace, "provider")


async def test_lifecycle_failure_artifact_has_real_entry_and_resources(
    hass, stress_trace
):
    _begin(stress_trace, "lifecycle")
    baseline = _resource_footprint(hass)
    entry = _entry(8274)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    peak = _resource_footprint(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    after = _resource_footprint(hass)
    record(
        stress_trace,
        "resource_snapshot",
        baseline_resources=baseline,
        peak_resources=peak,
        after_resources=after,
        entry_state=str(entry.state),
    )
    _fail(stress_trace, "lifecycle")


def test_resource_assertion_failure_artifact_has_baseline_and_peak(hass, stress_trace):
    _begin(stress_trace, "resource")
    baseline = _resource_footprint(hass)
    after = _resource_footprint(hass)
    record(
        stress_trace,
        "resource_snapshot",
        baseline_resources=baseline,
        peak_resources=after,
        after_resources=after,
    )
    _fail(stress_trace, "resource")
