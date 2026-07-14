# Skills

This directory contains example skills for Extended OpenAI Conversation (Responses). Skills provide reusable AI capabilities that can be enabled per conversation.

## Installing Skills

### Method 1: Download from Repository (Recommended)

Use the `download_skill` service to automatically download and install skills:

```yaml
service: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
```

This service:
- Downloads the skill from the GitHub repository
- Saves it to `<config>/extended_openai_conversation_responses/skills/`
- Automatically reloads all skills
- Returns the list of downloaded files

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

1. Go to Settings > Voice Assistants
2. Edit your Extended OpenAI Conversation (Responses) assistant
3. Click "Options"
4. Select the skills you want to enable from the list

**Note:** Skills are enabled per conversation entity. You can have different skills enabled for different assistants.

<img width="400" height="477" alt="스크린샷 2026-02-12 오후 5 11 15" src="https://github.com/user-attachments/assets/0264a5f1-c05c-4aae-90c6-78aea4b086f0" />

### System Prompt

When skills are enabled for a conversation, the following system prompt adds available skills to inform the AI:

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
- Lists all enabled skills with their names, descriptions, and file locations
- Instructs the AI to call `load_skill` to read detailed skill instructions
- Explains path resolution: relative paths in skill files must be converted to absolute paths using the skill's location directory
- Ensures bash commands use absolute paths to avoid execution errors

**Important for Skill Authors:** When writing skill instructions, you can reference files using relative paths (e.g., `scripts/crypto.py`), but remind the AI in your instructions that these must be resolved to absolute paths when executing commands.

### Functions

#### Load Skill Files

To read files from your skill's directory, configure the `load_skill` function:

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

This allows the AI to load data files, or other resources bundled with your skill.

#### Execute Bash Commands

To enable bash command execution in the workspace, configure the `bash` function:

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
```

This allows skills to run shell commands, execute scripts, call external programs, and process data using command-line tools.

**Security Note:** Bash execution is restricted to the workspace directory (by default `<config directory>/extended_openai_conversation_responses/`) and has deny patterns for destructive commands.

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
- After modifying skill metadata (name or description)

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
name: your_skill_name      # Used as identifier (max 64 chars)
description: Brief description of what this skill does and when to use it (max 1024 chars)
---
```

**Fields:**
- `name`: Directory name used as the skill identifier (required, max 64 characters)
- `description`: Brief description shown in the skills list and helps the AI decide when to use this skill (required, max 1024 characters)

#### 2. Markdown Body (Instructions for AI)

The body contains detailed instructions and examples for the AI agent:

```markdown
# Skill Title

## Contributing

To contribute new skills:

1. Create your skill following the structure above
2. Test thoroughly with various user queries
3. Submit a pull request with your skill in `examples/skills/`
4. Include a clear description and usage examples
```

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
   - ✅ Good: `python3 scripts/crypto.py btc`
   - ❌ Bad: "Run the crypto script"

5. **Response Format**: Show how to format responses to users, including:
   - What information to include
   - How to attribute sources
   - Handling of errors or edge cases