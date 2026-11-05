"""CI source download failures must not masquerade as valid runtime metadata."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from urllib.error import HTTPError, URLError

import pytest
import yaml

from ci import install_ha_test_plugin as installer


@pytest.mark.parametrize("status", [429, 503, "dns"])
def test_source_download_retries_same_pin_then_extracts(tmp_path, monkeypatch, status):
    sha = "a" * 40
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        member = tarfile.TarInfo(f"core-{sha}/identity.txt")
        member.size = len(sha)
        archive.addfile(member, io.BytesIO(sha.encode()))
    calls, sleeps = [], []

    def download(url, *, timeout):
        calls.append((url, timeout))
        if len(calls) < 3:
            if status == "dns":
                raise URLError("No address associated with hostname")
            raise HTTPError(url, status, "transient", {}, io.BytesIO())
        return io.BytesIO(payload.getvalue())

    monkeypatch.setattr(installer, "urlopen", download)
    monkeypatch.setattr(installer, "sleep", sleeps.append)
    destination = tmp_path / "core"
    installer.extract_source("home-assistant/core", sha, destination)
    assert (destination / "identity.txt").read_text(encoding="utf-8") == sha
    assert (
        calls
        == [(f"https://codeload.github.com/home-assistant/core/tar.gz/{sha}", 120)] * 3
    )
    assert sleeps == [5, 10]


@pytest.mark.parametrize("status,attempts", [(404, 1), (429, 4), ("dns", 4)])
def test_source_download_failure_is_bounded_and_does_not_extract(
    tmp_path, monkeypatch, status, attempts
):
    calls, sleeps = [], []

    def download(url, *, timeout):
        calls.append(url)
        if status == "dns":
            raise URLError("No address associated with hostname")
        raise HTTPError(url, status, "unavailable", {}, io.BytesIO())

    monkeypatch.setattr(installer, "urlopen", download)
    monkeypatch.setattr(installer, "sleep", sleeps.append)
    destination = tmp_path / "core"
    with pytest.raises(URLError) as error:
        installer.extract_source("home-assistant/core", "b" * 40, destination)
    if isinstance(error.value, HTTPError):
        assert error.value.code == status
    else:
        assert status == "dns"
    assert len(calls) == attempts
    assert sleeps == [5, 10, 20][: attempts - 1]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("python_reply", ["failed", "empty", "healthy"])
def test_prepare_step_never_publishes_an_empty_python_version(tmp_path, python_reply):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    step = next(
        step
        for step in workflow["jobs"]["prepare"]["steps"]
        if step.get("id") == "get-versions"
    )
    script = step["run"].replace("${{ env.HA_CORE_REPO }}", "home-assistant/core")
    manifest = json.dumps(
        {
            "requirements": [
                f"{package}==1.2.3"
                for package in (
                    "openai",
                    "hassil",
                    "home-assistant-intents",
                    "xmltodict",
                    "beautifulsoup4",
                    "lxml",
                )
            ]
        }
    )
    python_result = {
        "failed": "return 22",
        "empty": "return 0",
        "healthy": "printf '3.14.10\\n'",
    }[python_reply]
    # Use Git Bash explicitly on Windows; never invoke Windows' WSL bash.
    bash = (
        str(
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Git/bin/bash.exe"
        )
        if os.name == "nt"
        else shutil.which("bash")
    )
    assert bash and Path(bash).is_file()
    stubs = (
        "python3() { printf '" + "a" * 40 + "'; }\n"
        'curl() { case "${@: -1}" in */.python-version) ' + python_result + ";; "
        "*) printf '%s' '" + manifest + "';; esac; }\n"
    )
    output = tmp_path / "outputs"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", stubs + script],
        env={
            **os.environ,
            "REQUESTED_HA_DEV_SHA": "a" * 40,
            "GITHUB_OUTPUT": output.as_posix(),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert output.exists(), (result.returncode, result.stdout, result.stderr)
    outputs = output.read_text(encoding="utf-8")
    if python_reply == "healthy":
        assert result.returncode == 0, result.stderr
        assert "python-version=3.14.10\n" in outputs
        assert "lxml-version=1.2.3\n" in outputs
    else:
        assert result.returncode != 0
        assert "python-version=" not in outputs
        assert "openai-version=" not in outputs


@pytest.mark.parametrize(
    "filename,job_name,next_step",
    [
        ("upgrade-acceptance.yml", "upgrade", "Verify prebuilt browser environment"),
        (
            "openai-sdk-compatibility.yml",
            "sdk-contract",
            "Select SDK version for early warning",
        ),
    ],
)
@pytest.mark.parametrize("reconciliation_succeeds", [True, False])
def test_stable_matrix_resolves_runtime_before_selecting_its_subject(
    tmp_path, filename, job_name, next_step, reconciliation_succeeds
):
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github/workflows" / filename).read_text(encoding="utf-8")
    )
    steps = workflow["jobs"][job_name]["steps"]
    step = next(
        step
        for step in steps
        if step.get("name") == "Reconcile current stable test requirements"
    )
    assert steps.index(step) < next(
        index
        for index, candidate in enumerate(steps)
        if candidate.get("name") == next_step
    )
    bash = (
        str(
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "Git/bin/bash.exe"
        )
        if os.name == "nt"
        else shutil.which("bash")
    )
    assert bash and Path(bash).is_file()
    # Execute the production step, keeping package/network operations at their
    # boundary. An unchanged requirements hash must not bypass HA resolution.
    stubs = r"""
python() {
  if [[ "$1" == -c && "$2" == *https://pypi.org/pypi/homeassistant/json* ]]; then
    printf '2026.11.0\n'
  elif [[ "$1" == ci/resolve_ha_test_plugin.py && "$2" == 2026.11.0 ]]; then
    printf 'compatible-fixtures\n'
  else
    return 91
  fi
}
bash() {
  [[ "$1" == ci/reconcile_stable_environment.sh ]] || return 92
  [[ "$EOAI_EXPECTED_HA_VERSION" == 2026.11.0 ]] || return 93
  [[ "$EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION" == compatible-fixtures ]] || return 94
  printf 'reconciled\n' >> "$RECONCILIATION_RECEIPT"
  return "$RECONCILIATION_RESULT"
}
"""
    output, receipt = tmp_path / "outputs", tmp_path / "receipt"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-c", stubs + step["run"]],
        env={
            **os.environ,
            "GITHUB_ENV": output.as_posix(),
            "RECONCILIATION_RECEIPT": receipt.as_posix(),
            "RECONCILIATION_RESULT": "0" if reconciliation_succeeds else "42",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert receipt.read_text(encoding="utf-8") == "reconciled\n"
    if reconciliation_succeeds:
        assert result.returncode == 0, result.stderr
        assert (
            output.read_text(encoding="utf-8") == "EOAI_EXPECTED_HA_VERSION=2026.11.0\n"
        )
    else:
        assert result.returncode == 42
        assert not output.exists()
