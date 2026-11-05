"""Fixture metadata tolerates bounded TLS timeouts without changing selection."""

from io import BytesIO
import json
import ssl
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from ci import resolve_ha_test_plugin as resolver


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("failures", [0, 1, 2])
def test_metadata_recovers_from_bounded_transport_timeouts(
    monkeypatch, wrapped, failures
):
    error = TimeoutError("TLS handshake timed out")
    if wrapped:
        error = URLError(error)
    metadata = {"info": {"requires_dist": ["homeassistant==2026.11.0"]}}
    response = BytesIO(json.dumps(metadata).encode())
    opening = Mock(side_effect=[*[error] * failures, response])
    sleeping = Mock()
    monkeypatch.setattr(resolver, "urlopen", opening)
    monkeypatch.setattr(resolver, "sleep", sleeping)
    assert resolver._get_json("https://pypi.org/test/json") == metadata
    assert opening.call_count == failures + 1
    assert [call.args for call in sleeping.call_args_list] == [
        (attempt,) for attempt in range(1, failures + 1)
    ]
    assert all(call.kwargs == {"timeout": 20} for call in opening.call_args_list)
    assert response.closed


@pytest.mark.parametrize("wrapped", [False, True])
def test_metadata_timeout_exhaustion_propagates_the_original_failure(
    monkeypatch, wrapped
):
    error = TimeoutError("TLS handshake timed out")
    if wrapped:
        error = URLError(error)
    opening, sleeping = Mock(side_effect=error), Mock()
    monkeypatch.setattr(resolver, "urlopen", opening)
    monkeypatch.setattr(resolver, "sleep", sleeping)
    with pytest.raises(type(error)) as caught:
        resolver._get_json("https://pypi.org/test/json")
    assert caught.value is error
    assert opening.call_count == 3
    assert [call.args for call in sleeping.call_args_list] == [(1,), (2,)]


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://pypi.org/test/json", 404, "Not found", {}, None),
        URLError("unclassified transport failure"),
        URLError(ssl.SSLCertVerificationError("invalid certificate")),
        json.JSONDecodeError("Invalid metadata", "", 0),
    ],
)
def test_metadata_does_not_retry_other_failures(monkeypatch, error):
    opening, sleeping = Mock(side_effect=error), Mock()
    monkeypatch.setattr(resolver, "urlopen", opening)
    monkeypatch.setattr(resolver, "sleep", sleeping)
    with pytest.raises(type(error)) as caught:
        resolver._get_json("https://pypi.org/test/json")
    assert caught.value is error
    opening.assert_called_once()
    sleeping.assert_not_called()
