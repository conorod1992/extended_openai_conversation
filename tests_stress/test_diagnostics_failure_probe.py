"""Controlled failures run only by the manual/nightly diagnostics campaign."""

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from ci.enhanced_evidence import CANARIES
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from tests_stress.conftest import record
from tests_stress.provider_fault_transport import ProviderFaultTransport, WireStep
from tests_stress.test_lifecycle_matrix import _entry
from tests_stress.test_provider_fault_matrix import _OWNER, _agent, _say
from tests_stress.test_runtime_soak import _resource_footprint


def _begin(trace, boundary):
    record(trace, "begin", boundary=boundary, api_key=CANARIES[0])


def _fail(trace, boundary):
    record(
        trace,
        "before_assertion",
        private_content=CANARIES[1],
        knowledge_content=CANARIES[2],
        prompt=CANARIES[3],
    )
    pytest.fail(f"controlled {boundary} failure {CANARIES[0]}")


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
