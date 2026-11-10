"""The Bash format guard distinguishes shell control syntax from literal text."""

from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)


@pytest.mark.parametrize(
    "command",
    [
        "echo ready;\nformat C:",
        "echo ready\n\nformat C:",
        "echo ready &&\nformat C:",
        "echo ready |\nformat C:",
        "echo ready && \\\nformat C:",
        "echo ready; \\\n format C:",
        "\\\nformat C:",
        "for\\\nmat C:",
        '"for\\\nmat" C:',
        "echo ready # comment\\\nformat C:",
        "if true; then format C:; fi",
        "for item in a; do format C:; done",
        "while true; do format C:; done",
        "sh -c 'echo ready;\nformat C:'",
        '"format" C:',
        "for'mat' C:",
    ],
)
def test_format_guard_retains_common_shell_command_boundaries(
    command: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="disk format"):
        BashFunction()._guard_command(command, tmp_path, False)


@pytest.mark.parametrize(
    "command",
    [
        "printf '%s\\n' ';' format",
        'printf "%s\\n" ";" format',
        "printf '%s\\n' '\n' format",
        "printf '%s\\n' '\\\n' format",
        r"printf '%s\n' \; format",
        "echo '&&' format",
        "git log --format=%H",
        'sh -c \'printf "%s\\n" ";" format\'',
        'echo "quoted\nformat C:"',
        "echo ready # comment; format C:",
    ],
)
def test_format_guard_does_not_treat_literal_arguments_as_commands(
    command: str, tmp_path: Path
) -> None:
    BashFunction()._guard_command(command, tmp_path, False)


def test_format_guard_preserves_unrestricted_heredoc_commands(tmp_path: Path) -> None:
    """Unmatched quotes in heredoc data are valid shell input, not lexer errors."""
    heredoc = "cat <<'EOF'\nIt's ordinary text.\nEOF"
    function = BashFunction()
    function._guard_command(heredoc, tmp_path, False)
    with pytest.raises(ValueError, match="disk format"):
        function._guard_command(f"{heredoc}\nformat C:", tmp_path, False)
