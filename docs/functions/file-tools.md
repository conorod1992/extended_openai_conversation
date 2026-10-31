# File Function Tools

The `read_file`, `write_file`, and `edit_file` implementations operate on UTF-8 text files. Relative paths resolve inside the integration workspace. Each operation checks the resolved path against its allowed directories; paths that escape those directories are rejected. Reads and written results are limited to 1 MiB each.

## Read a file

```yaml
function:
  type: read_file
  path: "{{ extended_openai.skill_dir(name) }}/{{ file }}"
```

`path` is required and templated. `allow_dir` adds allowed directories; `restrict_to_allow_dir: true` disables the default allowed directories so only the explicit list is used. Read results include the content and size. The built-in `load_skill` tool uses this implementation with a Skill-specific path boundary.

## Write a file

```yaml
function:
  type: write_file
  path: "{{ filename }}"
  content: "{{ content }}"
  allow_dir:
    - "{{ config_dir }}/www/generated"
```

`path` and `content` are required templates. Writes use an atomic replacement, preserve the existing file mode where possible, and are rejected if the output exceeds the file-size limit. Avoid allowing model-supplied paths to choose sensitive destinations.

## Edit a file

```yaml
function:
  type: edit_file
  path: "{{ filename }}"
  old_text: "{{ old_text }}"
  new_text: "{{ new_text }}"
  allow_dir:
    - "{{ config_dir }}/automations"
```

`path`, `old_text`, and `new_text` are required templates. The expected text must be present. Edits are serialized with other integration writes to the same path, and detected changes to the file version cause a retryable error. A separate program can still change a file between the final version check and replacement, so avoid concurrent external edits. Use a narrow `allow_dir` and clear tool descriptions so the model knows which files may be changed.

These tools are not general backups. Review write/edit permissions and keep recovery copies of important configuration files.

Relative `allow_dir` paths are anchored to Home Assistant's configuration directory, independent of the process working directory. Relative file targets continue to use the configured Function Tool working directory.
