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

Selected Skills depend on the integration's built-in `load_skill` Function Tool. Extended OpenAI protects that dependency when configuration is saved: the loader must still exist, be individually enabled, belong to an enabled/reachable Function Group if grouped, and the agent must allow at least one Function Tool call per request. If one of those settings would make selected Skills unusable, the configuration change is rejected with a specific error instead of leaving the agent in a broken state.

The **Test agent** diagnostic also reports when a selected Skill is not installed or its loader is unavailable.

## Runtime availability

Saved selection and live availability are deliberately separate. A Skill can remain selected in an agent's configuration while its files are temporarily absent, for example after the Skill is removed or while it is being reinstalled. Only selected **and currently installed** Skills are advertised to the model.

The built-in `load_skill` tool is likewise exposed only when at least one selected installed Skill can actually be loaded. If `load_skill` belongs to an on-demand Function Group, that group must itself be loadable; the model first loads the group and then receives `load_skill` on the next tool round. If the Function Tool runtime is unavailable, the Skill loader and any otherwise-empty Skill group are omitted rather than advertised as unusable capabilities.

These checks affect the live conversation path and the effective-request Preview in the management UI, so Preview reflects the same currently usable Skill/tool set as a real request.

## Download a skill

The integration provides a Home Assistant action for downloading supported skills. For example:

```yaml
service: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
```

For a normal installed release, the action downloads the example Skill from the Git tag matching the integration version. This keeps a released integration paired with the Skill files that were released with it instead of silently pulling later changes from the moving `develop` branch.

Development and testing installs can deliberately choose another Git ref with `source_ref`:

```yaml
service: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
  source_ref: develop
```

Use `source_ref` only when you intentionally want Skill files from a different branch, tag, or commit in this repository.

Downloads are completed in a staging area outside the installed-Skills directory. A completed Skill is then published into the installed directory as one managed operation and the Skill catalogue is refreshed. Reloads, publication/removal, and canonical Skill reads share the same concurrency boundary, so a request does not discover a half-downloaded directory or cross a Skill replacement/removal halfway through reading it. A failed or cancelled publication is brought back to a stable old-or-new state before the boundary is released.

After installation, enable the skill for the relevant conversation agent in Options.

## Skills versus custom functions

A **skill** gives the model reusable instructions.

A **custom function** gives the model a callable tool that can execute logic, query data, call a service, request an API, scrape a page, or perform another defined operation.

Use a skill when the assistant mainly needs to know *how to reason or respond*. Use a custom function when it needs a new *action or data source*.

## Creating your own skills

The repository's `examples/skills` directory contains examples and source material for creating skills.

Keep skills focused. A small skill with a clear purpose is easier for both users and models to understand than one large instruction file attempting to cover unrelated tasks.
