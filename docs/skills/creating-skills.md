# Creating Skills

A Skill is a directory containing a required `SKILL.md` and optional reference files or scripts. Skills provide instructions and knowledge; Function Tools provide callable actions and data access.

## Directory layout

Skills are stored under:

```text
<config>/extended_openai_conversation_responses/skills/<skill-name>/
├── SKILL.md
├── scripts/       # optional helper scripts
└── references/    # optional reference material
```

## SKILL.md format

Use YAML frontmatter with a unique `name` and a concise `description`, followed by Markdown instructions:

```markdown
---
name: household_calendar
description: Explain how to summarize this household's calendar. Use when asked about upcoming events.
---

# Household calendar

## Instructions
1. Use the calendar Function Tool to retrieve events.
2. Report dates in the user's local time zone.
3. Ask before sharing private event details with another person.
```

Keep each Skill focused. State when it applies, give concrete steps and examples, and explain how to handle missing data or errors. Refer to bundled files by relative path; the loader resolves files within the Skill directory.

## Scripts and permissions

A Skill can instruct the model to use available Function Tools, including tools that can execute scripts or access files. Installing a Skill does not itself create a new Function Tool. Review all files and dependencies before installation, especially network calls, shell commands, file access, and Home Assistant actions. Only enable capabilities the Skill needs, and use narrow Function Tool permissions.

Do not rely on historical claims that shell commands are fully sandboxed: review the current Function Tool configuration and implementation before granting access to a command or filesystem tool.

## Install and reload

You can install repository examples with the `extended_openai_conversation_responses.download_skill` action. Normal releases use the matching integration release tag; development installs can set `source_ref` deliberately. See [Discover and install Skills](discover-skills.md).

After manually adding or editing files, use the `extended_openai_conversation_responses.reload_skills` action. Then select the installed Skill for the intended agent in **Extended OpenAI → Configuration → Skills**. Only selected, installed Skills with an available `load_skill` Function Tool are offered to the model.

See [Skills](../features/skills.md) for live availability and dependency behavior.
