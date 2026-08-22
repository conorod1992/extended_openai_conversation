# Guest Mode

Guest Mode is a backend-enforced, per-agent safety boundary for visitors using a
Home Assistant conversation agent. It can start immediately, start at a future
time, expire at a chosen time, or remain active indefinitely. Its schedule is
stored by the integration and exposed through a Guest Mode sensor; the sensor is
for visibility and automations, not the source of truth.

## Configure the guest policy

Open **Extended OpenAI → Configuration → Guest Mode policy**. Guest access is
deny-by-default:

- choose readable and controllable entities using entity, domain, area, or label
  IDs; controllable entities are always intersected with the readable set;
- optionally allow shared-household memory reads and writes independently;
- optionally allow Knowledge Library reads;
- mark an individual custom Function Tool as **Guest** only after reviewing the
  complete function, including any file, shell, network, administrative, or
  indirect data access it provides;
- turn on **Enable Guest Mode voice/model controls** to expose the one-way
  `guest_mode_restrict` tool. This setting controls tool availability only.
  Turning it off does not cancel, shorten, or deactivate an active or scheduled
  Guest Mode interval; use the trusted Guest page or Home Assistant service for
  those changes.

Existing custom tools and Function Groups are owner-only after migration. A tool
needs top-level `guest_allowed: true`; a grouped tool additionally requires its
group's **Available in Guest Mode** switch. The restrictive intersection is used
for both eager and on-demand group loading.

## Start, schedule, or end Guest Mode

The **Guest** page in the integration panel shows the current state and resolved
non-sensitive policy counts. Administrators can activate now, schedule or replace
the interval, change its end, make it indefinite, end it, or cancel a future
schedule.

Automations and scripts can use:

- `extended_openai_conversation_responses.guest_mode_update`
- `extended_openai_conversation_responses.guest_mode_disable`

Both actions target a config entry and conversation agent and require an
administrator. Datetimes without an offset use the Home Assistant timezone.

The model-facing `guest_mode_restrict` operation is deliberately one-way. It may
enable Guest Mode, move a start earlier, extend an end later, or make the interval
indefinite. It cannot disable, cancel, move a start later, or shorten an end. An
immediate activation without an expiry is indefinite.

## Enforced behavior

While Guest Mode is active, the integration filters prompt entity context,
configured tools, Function Groups, state/history targets, and control targets.
Model-visible context is fully re-resolved at the next user turn. If Guest Mode
becomes active after a provider request has already been sent, execution-time
restrictions tighten immediately, but context already sent to the model cannot be
retroactively removed. Deactivation during a request does not add permissions
until the next user turn.

Personal memory is always unavailable. Shared-household memory is available only
according to its two Guest switches. Conversation archive access and retention,
temporary memory, skills, and hosted Web Search are disabled. Knowledge is
independently opt-in. Guest turns can resume recent Guest context under the
selected continuity mode's normal session and timeout behavior, using a
structurally separate Guest namespace. They never resume owner context, owner
turns never resume Guest context, and Guest turns are not written to the owner's
archive.

Forbidden calls return the generic message `This capability is unavailable in
Guest Mode.` without naming hidden entities or tools. Guest state and policy
counts appear in diagnostics and the effective-request preview without exposing
private data. Full agent backups include the Guest Mode schedule; older version 1
backups restore with Guest Mode inactive.

## Status sensor

Each conversation agent has a `Guest Mode` sensor with these states:

- `inactive`
- `scheduled`
- `active`
- `active_indefinitely`

Its attributes include `active_from`, `active_until`, `indefinite`,
`currently_active`, and `scheduled`.

## Current limitation

Broad `area_id` and `device_id` targets supplied directly to a custom tool call
are rejected in Guest Mode. Configure areas and labels in the Guest policy; the
integration resolves them to individual allowed entities before prompt and
execution filtering. This conservative behavior prevents a broad service target
from including a newly added private entity.

Native `add_automation`, `get_energy`, and `get_user_from_user_id` tools are also
unavailable because their effect or result cannot be reliably reduced to the
guest entity set. Entity-scoped history and statistics calls remain available
only when the tool/group flags and every requested entity or statistic ID pass the
Guest policy.
