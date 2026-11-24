# Discover and install Skills

The repository includes example Skills under [`examples/skills`](https://github.com/conorod1992/extended_openai_conversation/tree/develop/examples/skills). Review the Skill files before installing them, and only use sources you trust. A Skill can guide the model to use the Function Tools enabled for its agent.

## Install a Skill

Create the Skills directory if needed. Copy the complete Skill directory, including any supporting files, into it. For example, the `crypto` Skill should have this layout:

```text
<config>/extended_openai_conversation_responses/skills/crypto/SKILL.md
```

After copying the files, run **Reload skills** in Home Assistant's **Developer Tools → Actions**:

```yaml
action: extended_openai_conversation_responses.reload_skills
```

## Enable a Skill for an agent

Open **Extended OpenAI**, select the agent, and open **Capabilities → Web search & Skills**. Enter each installed Skill's directory name on its own line and select **Save**. A saved selection only becomes available when the Skill is installed and the built-in `load_skill` Function Tool is usable. See [Skills](../features/skills.md) and [Creating Skills](creating-skills.md).
