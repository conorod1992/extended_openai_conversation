# Skills

Skills are reusable instruction sets that give a conversation agent specialized knowledge or behavior without adding a new executable tool.

They are useful when you want to teach the agent how to handle a particular domain, workflow, or style of task consistently.

## Where skills live

Skills are loaded from:

```text
<config>/extended_openai_conversation_responses/skills/
```

Each conversation agent can choose which installed skills it is allowed to load.

`SKILL.md` must fit the loader's **1 MiB (1,048,576 bytes)** file limit. Discovery skips larger instruction files with a warning rather than offering a Skill that cannot be loaded. Split larger material into supporting files, each within the same read limit. After reducing an oversized file, reload Skills to make it available again.

The maintained `load_skill` loader returns large instructions and supporting files in pages that fit the model's serialized tool-result budget, including JSON escaping and continuation metadata. Start at character offset `0` (or omit `offset`). While `has_more` is true, call the loader again with the returned `next_offset`; offsets count Unicode characters, not UTF-8 bytes. Each page consumes one ordinary Function Tool call. Previously saved maintained loader presets gain this optional argument at runtime without rewriting their saved configuration. Custom file readers can opt in with a `page_offset` template.

## Enable skills

1. Open **Extended OpenAI** in the Home Assistant sidebar.
2. Select the conversation agent and open **Capabilities → Web search & Skills**.
3. Enter the directory names of the installed Skills this agent may use, one per line, and select **Save**. Commas are treated as part of a name, not as separators.

For example, enter `crypto` after installing the repository's `crypto` example. The Skills field remains available even when no Skills are installed; entering a name does not download or install it.

Selected Skills depend on the integration's built-in `load_skill` Function Tool. New configurations require the loader to exist, be individually enabled, belong to an enabled/reachable Function Group if grouped, and have at least one Function Tool call allowed per request. Edits that introduce new loader problems or newly unusable Skill selections are rejected with a specific error.

Older releases, including 6.8.3, allowed selected Skills without a usable loader. Those saved configurations and their backups remain accessible after upgrading. Management shows an inactive-Skills warning with repair instructions; unrelated edits and partial repairs preserve the original selection and disabled settings. Explicitly restore or enable the loader and its group, allow a tool call, or clear the Skill selection. Migration and restore do not enable capabilities automatically.

The **Test agent** diagnostic also reports when a selected Skill is not installed or its loader is unavailable.

## Runtime availability

Saved selection and live availability are deliberately separate. A Skill can remain selected in an agent's configuration while its files are temporarily absent, for example after the Skill is removed or while it is being reinstalled. Only selected **and currently installed** Skills are advertised to the model.

The built-in `load_skill` tool is likewise exposed only when at least one selected Skill is installed and the loader is reachable. If `load_skill` belongs to an on-demand Function Group, that group must itself be loadable; the model first loads the group and then receives `load_skill` on the next tool round. If the Function Tool runtime is unavailable, the Skill loader and any otherwise-empty Skill group are omitted rather than advertised as unusable capabilities.

These checks affect the live conversation path and the effective-request Preview in the management UI, so Preview reflects the same advertised Skill/tool set as a real request. Discovery does not guarantee a later read: a file that grows beyond the shared 1 MiB limit after discovery is still rejected when loaded.

## Install a Skill

Copy the complete Skill directory into the Skills directory, then run **Reload skills** in Home Assistant. Add its directory name to the relevant agent’s selection. See [Discover and install Skills](../skills/discover-skills.md).

After an upgrade, discovery can still recover an interrupted install or removal recorded by an older version. It restores the previous version for an unfinished transaction and cleans up a committed transaction. If files have changed outside the transaction and ownership is uncertain, discovery leaves them for manual recovery.

## Skills versus custom functions

A **skill** gives the model reusable instructions.

A **custom function** gives the model a callable tool that can execute logic, query data, call a service, request an API, scrape a page, or perform another defined operation.

Use a skill when the assistant mainly needs to know *how to reason or respond*. Use a custom function when it needs a new *action or data source*.

## Creating your own skills

The repository's `examples/skills` directory contains examples and source material for creating skills.

Keep skills focused. A small skill with a clear purpose is easier for both users and models to understand than one large instruction file attempting to cover unrelated tasks.

Only the selected, installed Skills' names, descriptions, and locations are initially added to the prompt. The model uses `load_skill` to read `SKILL.md` or a supporting file when needed; these reads consume the normal Function Tool allowance. Skill instructions and retrieved files may then be sent to the configured AI provider. Installing a Skill does not install Python packages, create Function Tools, or enable shell execution.

Skills are installed in a shared directory for the integration, while each agent's allowed names are saved separately. Full agent backups preserve that selection but do not bundle the installed Skill directories. Install the required files on the destination before using a restored agent. See [Discover and install Skills](../skills/discover-skills.md) and [Creating Skills](../skills/creating-skills.md).
