# File Function Tools

The `read_file`, `write_file`, and `edit_file` implementations operate on UTF-8 text files. Relative paths resolve inside the integration workspace. Each operation checks the resolved path against its allowed directories; paths that escape those directories are rejected. The implementation enforces a bounded file size.

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

`path`, `old_text`, and `new_text` are required templates. The edit is applied only when the expected text and file version are still valid; concurrent changes cause a retryable error rather than silently overwriting newer content. Use a narrow `allow_dir` and clear tool descriptions so the model knows which files may be changed.

These tools are not general backups. Review write/edit permissions and keep recovery copies of important configuration files.
