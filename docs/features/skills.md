# Skills

Skills are reusable instruction sets that give a conversation agent specialized knowledge or behavior without adding a new executable tool.

They are useful when you want to teach the agent how to handle a particular domain, workflow, or style of task consistently.

## Where skills live

Skills are loaded from:

```text
<config>/extended_openai_conversation_responses/skills/
```

Each conversation agent can choose which installed skills it is allowed to load.

## Enable skills

1. Open **Settings > Voice assistants**.
2. Edit the assistant/conversation agent.
3. Open **Options**.
4. Select the skills the agent should be allowed to use.

The Skills option is hidden when no skills are installed.

## Download a skill

The integration provides a Home Assistant action for downloading supported skills. For example:

```yaml
service: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
```

After installation, enable the skill for the relevant conversation agent in Options.

## Skills versus custom functions

A **skill** gives the model reusable instructions.

A **custom function** gives the model a callable tool that can execute logic, query data, call a service, request an API, scrape a page, or perform another defined operation.

Use a skill when the assistant mainly needs to know *how to reason or respond*. Use a custom function when it needs a new *action or data source*.

## Creating your own skills

The repository's `examples/skills` directory contains examples and source material for creating skills.

Keep skills focused. A small skill with a clear purpose is easier for both users and models to understand than one large instruction file attempting to cover unrelated tasks.
