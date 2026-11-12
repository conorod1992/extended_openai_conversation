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


@pytest.mark.parametrize(
    "header,terminator",
    [
        ("EOF", "EOF"),
        ("'EOF'", "EOF"),
        ('"EOF"', "EOF"),
        (r"\EOF", "EOF"),
        ("-EOF", "\tEOF"),
        (" -EOF", "-EOF"),
    ],
)
def test_heredoc_data_is_not_directory_code(tmp_path, header, terminator):
    script = f"cat <<{header}\ncd ..\npushd ..\npopd\n{terminator}\ncd ."
    BashFunction()._guard_command(script, tmp_path, True)
    for change in ("cd ..", "pushd ..", "popd"):
        with pytest.raises(ValueError, match="blocked"):
            BashFunction()._guard_command(script + "\n" + change, tmp_path, True)


def test_multiple_heredocs_and_comment_delimiters(tmp_path):
    script = "cat <<'FIRST' <<SECOND # comment\ncd ..\n# not a comment here\nFIRST\npopd\nSECOND\ncd ."
    BashFunction()._guard_command(script, tmp_path, True)
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command(script + "\npushd ..", tmp_path, True)


@pytest.mark.parametrize("body", ["$(cd ..)", "`cd ..`", "$TARGET"])
def test_unquoted_heredoc_substitution_is_unverifiable(tmp_path, body):
    with pytest.raises(ValueError, match="heredoc substitution"):
        BashFunction()._guard_command(f"cat <<EOF\n{body}\nEOF", tmp_path, True)


def test_quoted_heredoc_substitutions_are_literal(tmp_path):
    BashFunction()._guard_command(
        "cat <<'EOF'\n$(cd ..)\n`pushd ..`\nEOF\ncd .", tmp_path, True
    )


@pytest.mark.parametrize(
    "script",
    [
        "cat << EOF\ncd ..\nEOF\ncat <<'END'\npopd\nEND\ncd .",
        "echo '<<EOF'\ncd . # <<FAKE\ncat <<'END'\npushd ..\nEND\ncd .",
        "cat <<'HASH'\n# HASH\ncd ..\nHASH\ncd .",
    ],
)
def test_sequential_and_literal_heredoc_syntax(tmp_path, script):
    BashFunction()._guard_command(script, tmp_path, True)
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command(script + "\ncd ..", tmp_path, True)


def test_directory_change_in_heredoc_header_is_still_checked(tmp_path):
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command("cat <<EOF && cd ..\ntext\nEOF", tmp_path, True)


@pytest.mark.parametrize(
    "script", ["echo $((1 << 2))\ncd ..", "(( value = 1 << 2 ))\npopd"]
)
def test_arithmetic_shift_cannot_hide_following_commands(tmp_path, script):
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command(script, tmp_path, True)


def test_plain_arithmetic_before_heredoc(tmp_path):
    BashFunction()._guard_command(
        "echo $((1 + 2))\ncat <<EOF\ncd ..\nEOF\ncd .", tmp_path, True
    )


@pytest.mark.parametrize(
    "script",
    [
        "echo $((1 << 2))",
        "(( value = (1 << 2) + (2 << 1) ))",
        "echo $((1 + 2))\ncat <<EOF\ncd ..\nEOF",
    ],
)
def test_arithmetic_shifts_are_not_heredocs(tmp_path, script):
    BashFunction()._guard_command(script, tmp_path, True)


def test_crlf_delimiter_cannot_hide_executable_tail(tmp_path):
    with pytest.raises(ValueError, match="heredoc CRLF"):
        BashFunction()._guard_command(
            "cat <<EOF\r\ntext\nEOF\r\ncd ..\n", tmp_path, True
        )


def test_continued_comment_cannot_introduce_a_fake_heredoc(tmp_path):
    with pytest.raises(ValueError, match="outside working directory"):
        BashFunction()._guard_command("\\\n# <<EOF\ncd ..\nEOF\n", tmp_path, True)


def test_unquoted_body_continuation_cannot_form_a_hidden_terminator(tmp_path):
    with pytest.raises(ValueError, match="heredoc substitution or continuation"):
        BashFunction()._guard_command("cat <<EOF\nEO\\\nF\ncd ..\n", tmp_path, True)


@pytest.mark.parametrize(
    "header",
    ["echo ${value/<<EOF/text}", "echo `printf '<<EOF'`", "echo $(printf '<<EOF')"],
)
def test_expansion_like_headers_cannot_create_fake_heredocs(tmp_path, header):
    with pytest.raises(ValueError, match="blocked"):
        BashFunction()._guard_command(header + "\ncd ..\nEOF\n", tmp_path, True)
