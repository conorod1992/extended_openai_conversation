"""Literal echo prose does not relax executable/path defensive checks."""

import pytest

from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)


@pytest.mark.parametrize(
    "command",
    [
        'echo "Use /help 0|7"',
        "echo 'Use /help 0|7'",
        'echo "Use /help (0|7), please!"',
        'echo "Use /help version 2"',
        'echo "Use /help"',
        'echo -n "Use /help 0|7"',
        'echo "Use /help 0|7" | cat',
        'echo "Use /help 0|7"; echo done',
    ],
)
def test_quoted_echo_prose_is_literal_data(tmp_path, command):
    BashFunction()._guard_command(command, tmp_path, True)


@pytest.mark.parametrize(
    "command",
    [
        'echo "/outside"',
        'echo "Use /outside/file 0|7"',
        'echo "Use /help 0|7 /outside"',
        'echo "Use /help 0|7" > /outside',
        'echo "Use /help 0|7" | cat /outside',
        'echo "Use /help 0|7"; cd ..',
        'echo "Use /help 0|7" && pushd ..',
        'echo "Use /help 0|7"; popd',
        'echo "Use /help 0|7" $(cat /outside)',
        'echo "Use /help 0|7 $(cat /outside)"',
        'echo "Use /help 0|7 `cat /outside`"',
        'echo "Use /help 0|7 $HOME"',
        'echo "Use /help 0|7 ../private"',
        'echo "Use /help 0|7 ..\\private"',
        "echo Use /help 0|7",
        'command echo "Use /help 0|7"',
        'X=1 echo "Use /help 0|7"',
        'printf "%s" "Use /help 0|7"',
        'cat "Use /help 0|7"',
    ],
)
def test_echo_exception_cannot_hide_paths_or_shell_effects(tmp_path, command):
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command(command, tmp_path, True)


def test_literal_exception_keeps_deny_patterns_and_allowlist(tmp_path):
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command('echo "Use /help rm -rf /"', tmp_path, True)
    with pytest.raises(ValueError, match="allowlist"):
        BashFunction()._guard_command('echo "Use /help 0|7"', tmp_path, True, ["^cat "])
