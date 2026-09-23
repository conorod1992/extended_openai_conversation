# Discover and install Skills

The repository includes example Skills under [`examples/skills`](https://github.com/conorod1992/extended_openai_conversation/tree/develop/examples/skills). Review the Skill files before installing them, and only use sources you trust. A Skill can guide the model to use the Function Tools enabled for its agent.

## Download a repository Skill

Use the Home Assistant action:

```yaml
action: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
```

Normal integration releases download the Skill from the Git tag matching the installed integration version. For development and testing, you can intentionally select another repository ref:

```yaml
action: extended_openai_conversation_responses.download_skill
data:
  skill_name: crypto
  source_ref: develop
```

Downloads are staged before they are published into the installed Skills directory. The Skill catalogue is refreshed after a successful download. No separate reload is needed after this action.

## Install manually

Copy a Skill directory into:

```text
<config>/extended_openai_conversation_responses/skills/
```

Then call:

```yaml
action: extended_openai_conversation_responses.reload_skills
```

## Enable a Skill for an agent

Open the dedicated Extended OpenAI management page, select the agent, and choose its Skills in **Configuration**. A saved selection only becomes available when the Skill is installed and the built-in `load_skill` Function Tool is usable. See [Skills](../features/skills.md) and [Creating Skills](creating-skills.md).
