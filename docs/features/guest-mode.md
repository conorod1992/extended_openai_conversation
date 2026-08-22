# Guest Mode

Configure it from **Extended OpenAI → Capabilities → Guest Mode**.

Guest Mode is a backend-enforced, per-agent privacy and safety boundary for
visitors using a Home Assistant conversation agent. It can start now or later,
expire at a chosen time, or remain active indefinitely. The integration owns the
schedule; its sensor is for visibility and automations.

## Configure the guest policy

Open **Extended OpenAI → Capabilities → Guest Mode**. Fresh version 2 policies
start with no exclusions. Guest entity access starts with Home Assistant's
normal Assist exposure and subtracts every matching entity, domain,
area, or label exclusion. A match in any category denies the entity from prompt
context, discovery, reads, history, controls, and entity-scoped native tools.

Optional separate control restrictions add another exclusion layer. When they
are off, control exactly matches read access; when on, control can only become
narrower. Home Assistant entity, area, label, and option selectors are used so
friendly names are shown while stable IDs are stored.

Knowledge and Functions each have **Off**, **On**, and **Custom** modes. Custom
Knowledge access selects source IDs from source titles and descriptions. Custom
Functions selects individual tools or complete Function Groups from their
metadata. Disabled functions remain disabled, and unsafe unscopable native
functions are always denied. Shared-household memory has **Off**, **Read only**,
and **Read & write** modes and defaults to Off. Personal memory is unavailable.

**Allow the assistant to activate Guest Mode** exposes the one-way
`guest_mode_restrict` model tool and defaults on for new agents. It controls only
the availability of that restriction tool. Turning it off never cancels,
shortens, deactivates, or weakens an active or scheduled Guest restriction.

### Existing configurations

Agents saved with the former allow-list policy keep those exact semantics. The
Guest page first shows **Previous Guest Mode settings found**, with choices to
review a conservative converted draft or start fresh after a warning. Neither
choice changes enforcement by itself. Version 2 becomes active only after an
administrator explicitly saves the draft. This prevents an old empty allow-list
from becoming broad access. Legacy Knowledge, function, and memory grants are
translated conservatively; a legacy write-only shared-memory combination becomes Off.

## Start, schedule, or end Guest Mode

The **Guest Mode** page shows status, scheduling controls, and non-sensitive resolved
policy counts. Administrators can activate now, replace the interval, shorten or
extend it, make it indefinite, end it, or cancel a future schedule.

Automations and scripts can use:

- `extended_openai_conversation_responses.guest_mode_update`
- `extended_openai_conversation_responses.guest_mode_disable`

Both actions target a config entry and conversation agent and require an
administrator. Datetimes without an offset use the Home Assistant timezone.

The model-facing restriction operation is deliberately one-way. It can enable
Guest Mode, move a start earlier, extend an end later, or make the interval
indefinite. It cannot disable, cancel, delay, or shorten Guest Mode.

## Enforced behavior

While active, Guest Mode filters prompt entity context, discovery, configured
tools and groups, Knowledge catalogs/search/direct retrieval, state and history
targets, control targets, and native function execution. Stale tool calls are
checked again against the live policy before execution. Denied calls return the
generic message `This capability is unavailable in Guest Mode.` without naming
hidden resources.

Personal memory, owner archive access and retention, temporary memory, skills,
and hosted Web Search are disabled. Guest turns can resume recent Guest context
under the selected continuity mode and normal timeout, using a structurally
separate Guest namespace. Guest never resumes owner context, owner never resumes
Guest context, and Guest turns are not written to the owner archive.

Model-visible context is fully re-resolved at the next user turn. If Guest Mode
becomes active after a provider request has already been sent, execution-time
restrictions tighten immediately, but already-sent context cannot be
retroactively removed. Deactivation during a request does not add permissions
until the next user turn.

## Status sensor

Each conversation agent has a `Guest Mode` sensor with `inactive`, `scheduled`,
`active`, and `active_indefinitely` states. Attributes include `active_from`,
`active_until`, `indefinite`, `currently_active`, and `scheduled`.

## Current limitations

Native `add_automation`, `get_energy`, and `get_user_from_user_id` are unavailable
because their effects or results cannot be reliably reduced to the guest entity
set. Execution restrictions cannot retract model context already transmitted to
a provider; the next user turn receives the fully rebuilt Guest context.
