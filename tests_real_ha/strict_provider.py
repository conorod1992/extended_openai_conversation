"""Strict transport beneath the real SDK; record swallowed violations too."""

import json

from tests.strict_provider_contract import validate_request
from tests_real_ha.test_provider_wire_e2e import _raw_client, _ScriptedWire


class StrictProvider(_ScriptedWire):
    """Validate serialized production requests before returning scripted replies."""

    def __init__(self, replies, **contract):
        super().__init__(replies)
        self.contract = contract
        self.violations = []

    async def send(self, request, *args, **kwargs):
        try:
            validate_request(
                request.url.path, json.loads(request.content), **self.contract
            )
        except AssertionError as err:
            self.violations.append(str(err))
            raise
        return await super().send(request, *args, **kwargs)

    def assert_complete(self, count):
        assert not self.violations, self.violations
        assert len(self.requests) == count


def install_strict_provider(monkeypatch, agent, replies, **contract):
    wire = StrictProvider(replies, **contract)
    monkeypatch.setattr(_raw_client(agent)._client, "send", wire.send)
    return wire
