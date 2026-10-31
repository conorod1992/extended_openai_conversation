# Skills

This directory contains example Skills for Extended OpenAI Conversation (Responses). Skills provide reusable instructions that can be enabled per conversation agent. They do not create executable tools or install dependencies.

## Installing Skills

### Method 1: Download from Repository (Recommended)

Use the `download_skill` service to automatically download and install skills:

```yaml
service: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
```

This service:

- Downloads the Skill from the repository tag matching the installed integration version
- Saves it to `<config>/extended_openai_conversation_responses/skills/`
- Automatically reloads all skills
- Returns the list of downloaded files

For a development install, explicitly add `source_ref: develop` (or another branch, tag, or commit) when you intend to use files from that ref. Downloads are staged before publication; an incomplete download does not become an installed Skill.

### Method 2: Manual Installation

1. **Copy the skill directory** to your Home Assistant config:
   ```bash
   cp -r crypto /config/extended_openai_conversation_responses/skills/
   ```

2. **Make scripts executable** (if applicable):
   ```bash
   chmod +x /config/extended_openai_conversation_responses/skills/crypto/scripts/crypto.py
   ```

3. **Reload skills** via service call:
   ```yaml
   service: extended_openai_conversation_responses.reload_skills
   ```

   This service returns the number of loaded skills.

### Enabling Skills

After installation, enable skills for your conversation:

1. Open **Extended OpenAI** from the Home Assistant sidebar.
2. Select the conversation agent.
3. Open **Capabilities → Web search & Skills**.
4. Enter each installed Skill's directory name on its own line, such as `crypto`, and select **Save**.

**Note:** Skills are enabled per conversation entity. You can have different skills enabled for different assistants.

Selected Skills require the built-in `load_skill` Function Tool, an enabled group if grouped, and a positive tool-call allowance. Only selected Skills that are currently installed are advertised. Installed files are shared by the integration; agent backups preserve the selection but do not bundle these directories.

### System Prompt

The default prompt includes selected, installed Skill metadata so the model can decide which instructions to read. A custom prompt can use the same `skills` template variable, for example:

```jinja2
{%- if skills %}
## Skills
The following skills extend your capabilities. To use a skill, call load_skill with the skill name to read its instructions.
When a skill file references a relative path, resolve it against the skill's location directory (e.g., skill at `/a/b/SKILL.md` references `scripts/run.py` → use `/a/b/scripts/run.py`) and always use the resulting absolute path in bash commands, as relative paths will fail.

<available_skills>
{%- for skill in skills %}
  <skill>
    <name>{{ skill.name }}</name>
    <description>{{ skill.description }}</description>
    <location>{{skill.path}}</location>
  </skill>
 {%- endfor %}
</available_skills>
{% endif %}
```

This prompt:

- Lists selected, installed Skills with their names, descriptions, and file locations
- Instructs the AI to call `load_skill` to read detailed skill instructions
- Explains path resolution: relative paths in skill files must be converted to absolute paths using the skill's location directory
- Guides the model to use absolute paths when an authorized shell tool executes a helper

**Important for Skill Authors:** When writing skill instructions, you can reference files using relative paths (e.g., `scripts/crypto.py`), but remind the AI in your instructions that these must be resolved to absolute paths when executing commands.

### Functions

#### Load Skill Files

The default Function Tools include `load_skill`. If it was removed, restore the built-in loader with this definition rather than adding a duplicate:

```yaml
- spec:
    name: load_skill
    description: Load a file from a skill's directory.
    parameters:
      type: object
      properties:
        name:
          type: string
          description: Skill name
        file:
          type: string
          description: Relative file path within the skill directory
      required:
      - name
      - file
  function:
    type: read_file
    path: '{{extended_openai.skill_dir(name)}}/{{file}}'
```

This allows the AI to read `SKILL.md` and text resources bundled with a Skill. Supporting files are loaded on demand, not all at once. Each read uses the normal tool-call allowance and is limited to 1 MiB. Retrieved instructions and content may be sent to the configured provider.

#### Execute Bash Commands

Only enable shell execution for trusted agents and reviewed Skills. A Bash Function Tool requires explicit opt-in:

```yaml
- spec:
    name: bash
    description: Execute a bash command in workspace.
    parameters:
      type: object
      properties:
        command:
          type: string
          description: Bash command to execute
      required:
      - command
  function:
    type: bash
    command: '{{command}}'
    allow_unsafe_shell: true
    restrict_to_workspace: true
```

This allows the model to request shell commands, execute scripts, call installed external programs, and process data using command-line tools. Install the example's required Python packages and executables yourself; the Skill downloader does not do so. Prefer a narrowly defined Function Tool over a general shell wherever possible.

**Security Note:** Bash runs with Home Assistant's operating-system privileges. Workspace checks and destructive-command deny patterns are defensive checks, not a sandbox; scripts and shell syntax can access other paths. See [Bash Function Tool](../../docs/functions/bash.md) before enabling it.

## Managing Skills

### Reload Skills

After manually creating or modifying skills, reload them to apply changes:

```yaml
service: extended_openai_conversation_responses.reload_skills
```

This service:

- Scans the skills directory
- Reloads all `SKILL.md` files
- Returns the number of loaded skills

**When to reload:**

- After manually creating a new skill
- After editing existing `SKILL.md` files
- After modifying the description or renaming a Skill directory

**Note:** You don't need to reload after using `download_skill` service, as it automatically reloads skills.

## Creating Your Own Skills

### Skill Directory Structure

Each skill is a directory under `<config>/extended_openai_conversation_responses/skills/` with a `SKILL.md` file:

```
<config>/extended_openai_conversation_responses/skills/
└── your_skill_name/
    ├── SKILL.md           # Required: skill definition
    ├── scripts/           # Optional: scripts, tools, etc.
    │   └── helper.py
    └── references/        # Optional: reference data, documentation, etc.
        └── example.md
```

### SKILL.md Format

The `SKILL.md` file consists of two parts:

#### 1. YAML Frontmatter (Required)

```yaml
---
name: your_skill_name      # Optional metadata; keep it consistent with the directory
description: Brief description of what this skill does and when to use it (max 1024 chars)
---
```

**Fields:**

- The directory name is the runtime identifier (1–64 characters); the frontmatter `name` does not override it.
- `description`: Brief description shown in the skills list and helps the AI decide when to use this skill (required, max 1024 characters)

#### 2. Markdown Body (Instructions for AI)

The body contains detailed instructions and examples for the AI agent:

```markdown
# Skill Title

## Instructions
1. Load references/example.md when additional detail is needed.
2. Use the configured Function Tool to retrieve current data.
3. Explain any missing information rather than inventing a result.
```

## Contributing

To contribute new skills:

1. Create your skill following the structure above
2. Test thoroughly with various user queries
3. Submit a pull request with your skill in `examples/skills/`
4. Include a clear description and usage examples

### Best Practices

1. **Clear Description**: Write a concise description (under 1024 chars) that clearly explains when the skill should be used. This helps the AI decide whether to activate this skill.

2. **Detailed Instructions**: Provide step-by-step guidance that the AI can follow autonomously. Be specific about:
   - Exact commands to run
   - File paths (use relative paths from skill directory)
   - Expected inputs and outputs
   - Error handling

3. **Concrete Examples**: Include 2-3 examples showing:
   - Typical user requests
   - Exact actions the AI should take
   - Expected response format

4. **Command Syntax**: If using bash or scripts, specify exact syntax:
   - ✅ Good: `python3 /config/extended_openai_conversation_responses/skills/crypto/scripts/crypto.py btc` (adapt the configuration path to the installation)
   - ❌ Bad: "Run the crypto script"

5. **Response Format**: Show how to format responses to users, including:
   - What information to include
   - How to attribute sources
   - Handling of errors or edge cases
