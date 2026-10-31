# Bash Function Tool

The `bash` implementation runs a shell command with Home Assistant's operating-system privileges. Treat it as powerful code execution and enable it only for an agent you trust.

## Configuration

```yaml
function:
  type: bash
  command: "{{ command }}"
  allow_unsafe_shell: true
  restrict_to_workspace: true
```

- `command` is a template rendered from model-provided arguments.
- `allow_unsafe_shell` must be explicitly set to `true`; otherwise execution returns a disabled error.
- `cwd` optionally selects the working directory. Relative values resolve from the integration's default workspace.
- `restrict_to_workspace` defaults to `true` and enables defensive path checks relative to the chosen working directory.
- `allow_patterns` optionally requires the command to match one of the configured regular expressions.

Commands run through the host shell, so available programs depend on the Home Assistant installation. The default timeout is 300 seconds; a numeric `timeout` argument may lower it but cannot raise that maximum. Standard output and standard error are each truncated to 10,000 characters. Installing a Skill does not install its command-line or Python dependencies.

These checks reduce risk; they do not create an operating-system sandbox. Shell syntax, interpreters, and dynamically constructed paths can bypass lexical checks. Do not claim that Bash is safely confined to the workspace. Grant it only to trusted agents, keep its commands narrow, and avoid secrets in prompts or output.

Use `read_file`, `write_file`, or `edit_file` when a task only needs file access; those tools have path containment checks without arbitrary shell execution. See [File Function Tools](file-tools.md).
