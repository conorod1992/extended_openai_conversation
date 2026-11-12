"""Bounded literal operands must not hide executable/file operands."""

from pathlib import Path
import re

import pytest

from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
    _literal_text_operands,
    _shell_command_tokens,
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


@pytest.mark.parametrize("separator", [";", "|", "&&", "||", "\n"])
@pytest.mark.parametrize(
    ("command", "expected_indices"),
    [
        ("echo 'see /help'", {4}),
        ("echo 'see /help' 'read /docs' log.txt", {4, 5}),
        ("awk '/error/ {print}' log.txt", {4}),
        ("sed -n '/start/,/end/p' f", {5}),
        ("grep -E '^/api' access.log", {5}),
    ],
)
def test_literal_exemptions_stay_in_their_command_segment(
    tmp_path: Path, separator: str, command: str, expected_indices: set[int]
):
    prefix = f"printf x{separator} {command}{separator} cat "
    tokens = _shell_command_tokens(prefix + "/etc/passwd")
    assert _literal_text_operands(tokens) == expected_indices
    # Harmless earlier literal text must neither block the local-file control
    # nor hide the later external filename behind an unrelated rejection.
    BashFunction()._guard_command(prefix + "log.txt", tmp_path, True)
    with pytest.raises(ValueError, match=re.escape("path '/etc/passwd'")):
        BashFunction()._guard_command(prefix + "/etc/passwd", tmp_path, True)


@pytest.mark.parametrize("separator", [";", "|", "&&", "||", "\n"])
def test_later_external_path_is_rejected_when_prose_path_is_the_workspace(separator):
    # The original shifted exemption skipped /etc/passwd. Using /help as the
    # workspace prevents the unexempted prose from masking that real bypass.
    # The guard only resolves these paths; it does not read or execute anything.
    command = f"printf x{separator} echo 'see /help'{separator} cat /etc/passwd"
    with pytest.raises(ValueError, match=re.escape("path '/etc/passwd'")):
        BashFunction()._guard_command(command, Path("/help"), True)


@pytest.mark.parametrize(
    ("command", "expected_indices"),
    [
        ("printf x; echo 'see /help' > log.txt; cat /etc/passwd", {4}),
        ("printf x; echo 'see /help' > /etc/passwd", {4}),
        ("printf x; echo 'see /help' | cat > /etc/passwd", {4}),
        ("printf x; echo 'see /help' 'read /docs' /etc/passwd", {4, 5}),
        ("echo 'see /help'; printf x; echo 'read /docs'; cat /etc/passwd", {1, 7}),
        ("printf x; echo 'see /help'; echo 'read /docs' > /etc/passwd", {4, 7}),
    ],
)
def test_redirections_and_other_operands_never_receive_literal_exemptions(
    tmp_path: Path, command: str, expected_indices: set[int]
):
    assert _literal_text_operands(_shell_command_tokens(command)) == expected_indices
    with pytest.raises(ValueError, match=re.escape("path '/etc/passwd'")):
        BashFunction()._guard_command(command, tmp_path, True)
    BashFunction()._guard_command(
        command.replace("/etc/passwd", "log.txt"), tmp_path, True
    )
