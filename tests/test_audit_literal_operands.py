"""Bounded literal operands must not hide executable/file operands."""

from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)


@pytest.mark.parametrize(
    "command",
    [
        "awk '/error/ {print}' log.txt",
        "sed -n '/start/,/end/p' f",
        "grep -E '^/api' access.log",
        "echo 'see /help'",
    ],
)
def test_proven_literal_operands(command: str, tmp_path: Path):
    BashFunction()._guard_command(command, tmp_path, True)


@pytest.mark.parametrize(
    "command",
    [
        "awk '/error/ {print}' /outside/log.txt",
        "awk -f /outside/program.awk log.txt",
        "awk '/error/ {print > \"/outside/result\"}' log.txt",
        "awk 'BEGIN {system(\"cat /outside/secret\")}' log.txt",
        "sed -n '/start/,/end/p' /outside/f",
        "sed -f /outside/program.sed f",
        "sed -n '/start/w /outside/result' f",
        "sed -n '/start/e cat /outside/secret' f",
        "grep -E '^/api' /outside/access.log",
        "grep -f /outside/pattern access.log",
        "grep -Ef/outside/pattern access.log",
        "sed -f/outside/program.sed f",
        "awk -f/outside/program.awk log.txt",
        "grep --unknown '^/api' access.log",
        "echo 'see /help' > /outside/result",
        "echo 'see /help'; cat /outside/secret",
        'echo "see $(cat /outside/secret)"',
        "printf '%s' '/outside/secret'",
    ],
)
def test_neighbouring_paths_remain_checked(command: str, tmp_path: Path):
    with pytest.raises(ValueError, match="path guard"):
        BashFunction()._guard_command(command, tmp_path, True)
