# File Function Tools

The `read_file`, `write_file`, and `edit_file` implementations operate on UTF-8 text files. Relative paths resolve inside the integration workspace. Each operation checks the resolved path against its allowed directories; paths that escape those directories are rejected. Reads and written results are limited to 1 MiB each.

## Read a file

```yaml
function:
  type: read_file
  path: "{{ extended_openai.skill_dir(name) }}/{{ file }}"
```

`path` is required and templated. `allow_dir` adds allowed directories; `restrict_to_allow_dir: true` disables the default allowed directories so only the explicit list is used. Read results include the content and size. The built-in `load_skill` tool uses this implementation with a Skill-specific path boundary.

### Reading large files in pages

The 1 MiB file limit is separate from the **32,000-character serialized model tool-result limit** (including JSON escaping and metadata). A successful unpaged read can exceed the latter and return an `output_overflow` result to the model. Custom file readers can opt into bounded pages with a `page_offset` template. For example, paste this complete definition into the single-tool YAML editor:

```yaml
spec:
  name: read_workspace_file
  description: Read a workspace text file. Start at offset 0; if has_more is true, call again using next_offset. Stop when has_more is false or absent.
  parameters:
    type: object
    properties:
      filename:
        type: string
        description: Relative file path in the workspace
      offset:
        type: integer
        minimum: 0
        description: Character offset (omit for the first page)
    required:
      - filename
function:
  type: read_file
  path: "{{ filename }}"
  page_offset: "{{ offset | default(0) }}"
```

Offsets count Unicode characters, not UTF-8 bytes. `content` contains the returned text and `size` reports the file size. For paged results, `next_offset` is the next character position and `has_more` indicates whether to continue. A small file returned in full on the first read can omit the paging fields entirely. Each page uses one ordinary tool call, so the assistant may need several calls to read a large file. The 1 MiB limit and path checks still apply to every read. Use narrow allowed paths for tools exposed to the model.

## Write a file

```yaml
function:
  type: write_file
  path: assistant_summary.txt
  content: "{{ content }}"
```

`path` and `content` are required templates. Writes use an atomic replacement, preserve the existing file mode where possible, and are rejected if the output exceeds the file-size limit. Prefer a fixed filename as shown, or validate any filename the model supplies against an explicit list. For write and edit tools, `allow_dir` adds allowed locations alongside the default integration workspace. The `restrict_to_allow_dir` option is available for reads only.

## Edit a file

```yaml
function:
  type: edit_file
  path: assistant_summary.txt
  old_text: "{{ old_text }}"
  new_text: "{{ new_text }}"
```

`path`, `old_text`, and `new_text` are required templates. The expected text must be present. Edits are serialized with other integration writes to the same path, and detected changes to the file version cause a retryable error. A separate program can still change a file between the final version check and replacement, so avoid concurrent external edits. If you expose this tool to the model, describe exactly which file it edits.

These tools are not general backups. Review write/edit permissions and keep recovery copies of important configuration files.

Relative `allow_dir` paths are anchored to Home Assistant's configuration directory, independent of the process working directory. Relative file targets resolve under the fixed integration workspace, `<config>/extended_openai_conversation_responses/`; these file tools do not expose Bash's configurable `cwd` setting.
