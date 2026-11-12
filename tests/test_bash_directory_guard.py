"""The best-effort workspace guard sees directory commands, not quoted prose."""

import pytest

from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)


@pytest.mark.parametrize(
    "command",
    [
        "cd ..",
        "echo hello\ncd ..",
        "(cd ..)",
        "echo ok &&(cd ..)",
        "echo ok; cd ..",
        "echo ok | cd ..",
        "echo ok & cd ..",
        "if true; then cd ..; fi",
        "while cd ..; do true; done",
        "{ cd ..; }",
        "for x in one; do cd ..; done",
        "! cd ..",
        "X=1 cd ..",
        "builtin cd ..",
        "command cd ..",
        "command -- cd ..",
        "command -p -- cd ..",
        "builtin -- cd ..",
        "cd {..,}",
        "'cd' ..",
        "cd -P ..",
        "cd -L -- ..",
        "cd",
        "cd -P",
        "cd --",
        "cd; echo ok",
        "cd\necho ok",
        "cd -",
        "cd ~",
        'cd "$HOME"',
        "cd $TARGET",
        "cd $(pwd)",
        "cd sub*",
        "cd sub?",
        "cd [ab]",
        "pushd ..",
        "pushd -n ..",
        "pushd",
        "pushd +1",
        "pushd -1",
        "popd",
        "echo ok\npopd -n",
        "cd sub; cd ..",
        "echo $(cd ..)",
        "if (pushd ..); then true; fi",
        "c\\\nd ..",
    ],
)
def test_directory_escape_or_unverifiable_target_blocked(tmp_path, command):
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command(command, tmp_path, True)


@pytest.mark.parametrize(
    "command",
    [
        "cd .",
        "cd -- .",
        "cd -P -e .",
        "cd sub",
        "cd 'space dir'",
        "cd sub && cd deeper",
        "pushd sub",
        "pushd -n -- sub",
        "if true; then cd sub; else cd other; fi",
        "(cd sub); cd other",
        'echo "cd .."',
        "printf '%s' 'pushd ..; popd'",
        "echo ';' cd ..",
        "echo cd ..",
        "command -v cd",
        "echo '&&' pushd ..",
        "echo ok # cd ../private\ncd sub",
        "# popd\ncd sub",
        "echo '#'; cd sub",
        "cd sub # popd",
    ],
)
def test_literal_navigation_and_quoted_or_commented_text_allowed(tmp_path, command):
    (tmp_path / "sub").mkdir()
    BashFunction()._guard_command(command, tmp_path, True)


def test_absolute_in_workspace_and_symlink_escape(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "space dir"
    target.mkdir()
    # POSIX shell paths keep Windows drive separators out of this lexer test.
    command = f'cd "{target.as_posix()}"'
    BashFunction()._guard_command(command, root, True)
    try:
        (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create directory symlinks")
    with pytest.raises(ValueError, match="outside working directory"):
        BashFunction()._guard_command("cd -P outside", root, True)


@pytest.mark.parametrize("command", ["cd", "cd ..", "pushd ..", "popd", 'cd "$HOME"'])
def test_opt_out_keeps_trusted_shell_contract(tmp_path, command):
    BashFunction()._guard_command(command, tmp_path, False)
