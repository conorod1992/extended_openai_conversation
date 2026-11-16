"""Transient evidence-download failures retry without accepting unavailable proof."""

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from ci import nightly_programme, release_certification


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_github_read_recovers(monkeypatch, status):
    attempts, delays = [], []

    def read(request, timeout):
        attempts.append(request)
        assert request.get_header("Authorization") == "Bearer fixture-token"
        if len(attempts) == 1:
            raise HTTPError(request.full_url, status, "temporary", {"Retry-After": "7"}, BytesIO())
        return BytesIO(b'{"id": 12}')

    monkeypatch.setattr(release_certification, "urlopen", read)
    monkeypatch.setattr(release_certification.time, "sleep", delays.append)
    actions = release_certification.GitHubActions("owner/repo", "fixture-token")
    assert actions.get("actions/runs/12") == {"id": 12}
    assert len(attempts) == 2 and delays == [7]


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_rejected_or_exhausted_reads_still_fail(monkeypatch, status):
    attempts, delays = [], []

    def read(request, timeout):
        attempts.append(request)
        raise HTTPError(request.full_url, status, "unavailable", {}, BytesIO())

    monkeypatch.setattr(release_certification, "urlopen", read)
    monkeypatch.setattr(release_certification.time, "sleep", delays.append)
    with pytest.raises(HTTPError) as error:
        release_certification.GitHubActions("owner/repo", "fixture-token").get("actions/runs/12")
    assert error.value.code == status
    assert len(attempts) == (5 if status == 503 else 1)
    assert delays == ([5, 10, 20, 30] if status == 503 else [])


def test_programme_prints_its_failure_reason(monkeypatch, tmp_path, capsys):
    output = tmp_path / "programme.json"
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setattr("sys.argv", ["programme", "--candidate", "a" * 40, "--ha-version", "2026.11.1", "--output", str(output)])

    def unavailable(*args, **kwargs):
        raise OSError("HTTP Error 503: Egress is over the account limit.")

    monkeypatch.setattr(nightly_programme, "collect", unavailable)
    assert nightly_programme.main() == 1
    assert json.loads(output.read_text())["passed"] is False
    assert "503: Egress is over the account limit" in capsys.readouterr().out
