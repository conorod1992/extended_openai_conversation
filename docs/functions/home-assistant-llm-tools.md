# Home Assistant LLM Tools

Home Assistant LLM Tools are capabilities supplied by Home Assistant or installed
integrations and services. A custom ExtendedOpenAI Function contains a definition
you own. An HA LLM Tool is a reference to a capability owned elsewhere: its current
description, input schema and implementation come from Home Assistant at runtime.
There is no editable YAML or duplicate-definition action for these cards.

In **Functions**, choose **Add LLM Tools**. Search by name, description or source,
select individual tools, or filter sources and choose **Select all shown**. You can
assign the selected tools to an existing Function Group in the same dialog.
Already-added tools are marked and cannot be added twice.

Bulk addition saves **only those individual tools selected at that time**. Tools
introduced by future updates are never automatically exposed. A source is not
necessarily an integration: it can be a contribution from an integration into the
Assist API, or a complete registered LLM API combining capabilities. MCP servers
appear through Home Assistant's registered MCP APIs; their availability depends on
that integration and its remote server.

The card's Enabled switch controls exposure for this agent. Disabling retains the
reference and group membership. Removing deletes only this agent's reference; it
does not delete or change the Home Assistant capability or remote service.
Existing references from Request Rules or Guest settings must be removed first
when the normal Functions dependency checks require this.

Many always-visible schemas can increase provider input tokens. Function Groups
retain their existing token-saving behavior: an unloaded on-demand group's HA
schemas and associated source prompts are omitted. Loading a group makes its
available tools and relevant instructions visible. A source's instructions remain
intact even when only some of its tools are selected. The request preview and live
request footprint include the effective schemas and prompt contribution.

Discovery is an administrator preview, not a permission grant. Actual requests
resolve tools with the requesting user's Home Assistant context, device, language
and assistant surface. A tool can therefore be available in the picker but absent
from a particular request. Home Assistant and the source implementation enforce
their permissions with that context; selecting a tool never transfers the
administrator's identity. Only install and expose sources you trust to implement
their permission checks correctly. External HA tools are always unavailable while
Guest Mode is active, including tools in an otherwise guest-allowed Function Group.

External tools execute sequentially through ExtendedOpenAI's normal tool exchange
and Function Call budget. ExtendedOpenAI does not schedule them as delayed
Functions or retain request-owned tool/API objects for background execution. A
source can implement its own scheduling capability as part of its native behavior.
Both Responses and Chat Completions use the same live capability adapter.

Unavailable or renamed tools remain saved. They are omitted from model requests,
and the management preview marks them unavailable. No similar-name replacement is
chosen. When the exact saved identity returns, the tool becomes usable again.
Normal save, duplication, configuration export/import and full backup retain these
references, including when the destination lacks their sources.

## Identity and Home Assistant compatibility

The integration retains its existing minimum Home Assistant version. Traditional
registered LLM APIs work on that API architecture. The newer `llm.py` contribution
platform requires Home Assistant 2026.8 or later; the installed Home Assistant
version determines which sources can appear. Schema conversion follows Core's
serializer, using `probatio` on newer Core versions and `voluptuous-openapi` on older
ones. Live schemas are never copied into saved Function definitions.

For Core's Assist contribution platform, references contain the API surface,
contributing domain and exact native tool name. Core currently exposes contributor
domains through its lazy platform registry but removes source/prompt boundaries
from the assembled API result. The adapter reads that registry in one compatibility
boundary; it does not patch Core or infer source identity from a display name.

Opaque registered APIs do not expose reliable contributor identity. Their references
conservatively pin API ID, native name and implementation module/class. Duplicate
native names within an opaque API or a single contribution are rejected as
ambiguous. Implementation moves/renames can make such references unavailable and
require explicit replacement. The adapter cannot promise an immutable identity if
a third-party source reuses its own identifiers for different behavior.

An upgrade that moves an older Assist API tool into a new contribution source can
therefore require removing the unavailable old reference and explicitly adding the
new one. This deliberately avoids granting access to a replacement automatically.
Core's display-based merged API wrappers are not offered as persistent references;
select the tools through their original registered sources instead.

Provider names are deterministic local catalogue identifiers, separate from the
saved external reference. They avoid custom Function collisions and include a
short mapping in the model prompt so source instructions using native names remain
understandable. Group membership uses that local ID, not a mutable display label.

AI Tasks honor a caller-provided Home Assistant LLM API already assembled by Core,
including its context and custom serializer. These calls use the same sequential
exchange and budget. AI Tasks do not automatically receive the agent's custom
Functions or saved HA tool catalogue.

For the underlying API contract, see [Home Assistant's LLM developer documentation](https://developers.home-assistant.io/docs/core/llm/).
