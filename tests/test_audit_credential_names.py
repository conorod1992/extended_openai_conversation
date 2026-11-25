"""Historical redacted artifacts keep their original credential policy."""

import json

import pytest

from tests.test_secret_restore_context import _rest_tool

NAMES = [
    "Ocp-Apim-Subscription-Key",
    "X-Auth-Key",
    "x-functions-key",
    "access_key",
    "private_key",
    "app_key",
    "license_key",
    "ACCESS-KEY",
    "privateKey",
    "App.Key",
    "LicenseKey",
]
SECRET = "fabricated-audit-credential-42"


@pytest.mark.parametrize("location", ["params", "payload", "json_payload"])
def test_historical_redaction_does_not_apply_newer_public_classifications(location):
    from custom_components.extended_openai_conversation_responses.secret_redaction import (
        redact_secrets,
    )

    tool = _rest_tool()
    request = tool["function"]
    request["headers"] = {}
    field = "params" if location == "params" else "payload"
    request[field] = (
        json.dumps({"auth": {"aPpId": SECRET}})
        if location == "json_payload"
        else {"aPpId": SECRET}
    )
    for host in ("example.invalid", "api.openweathermap.org"):
        request["resource"] = f"https://{host}/data/2.5/weather"
        assert redact_secrets({"functions": [tool]}) == {"functions": [tool]}
    for name in NAMES:
        assert redact_secrets({name: SECRET}) == {name: SECRET}
