"""Real subprocess failures use the existing Bash execution contract."""

from contextlib import nullcontext
import os
import shlex
import subprocess
import sys

import pytest

from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from custom_components.extended_openai_conversation_responses.functions import (
    BashFunction,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("failure", ["timeout", "launch"])
@pytest.mark.parametrize("propagate", [False, True])
async def test_real_bash_failure_propagates_only_when_requested(
    hass, tmp_path, failure, propagate
):
    arguments = [sys.executable, "-c", "import time; time.sleep(2)"]
    command = (
        subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    )
    function = BashFunction()
    config = function.validate_schema(
        {
            "type": "bash",
            "command": command,
            "allow_unsafe_shell": True,
            "restrict_to_workspace": False,
            "cwd": str(
                tmp_path if failure == "timeout" else tmp_path / "missing-directory"
            ),
        }
    )
    with propagate_function_execution_errors() if propagate else nullcontext():
        if propagate:
            with pytest.raises(HomeAssistantError) as raised:
                await function.execute(hass, config, {"timeout": 0.2}, None, [])
            message = str(raised.value)
            if failure == "launch":
                assert isinstance(raised.value.__cause__, OSError)
        else:
            result = await function.execute(hass, config, {"timeout": 0.2}, None, [])
            assert set(result) == {"error"}
            message = result["error"]
    if failure == "timeout":
        assert message == "Command timed out after 0.2 seconds"
    else:
        assert not (tmp_path / "missing-directory").exists()
        assert message and "Command timed out" not in message
