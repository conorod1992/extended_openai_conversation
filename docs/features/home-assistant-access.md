# Home Assistant access and permissions

Extended OpenAI uses several separate access checks. The simplest way to think about them is: **an entity must be available to Assist, the Home Assistant user must be allowed to control it, and any Extended OpenAI restrictions must also allow it.**

An entity being visible to Assist does not by itself mean that every user may control it.

## 1. Assist exposure: which entities are available

Home Assistant's Assist exposure controls which normal entities are available to conversation agents. Extended OpenAI uses that exposure boundary for entity discovery and normal Home Assistant tool access.

The separate **Include exposed devices** setting under **Assistant → Prompt & context** only decides whether exposed entity names and current states are automatically added to the model prompt. Turning that prompt option off does not revoke access to entities that remain available through Home Assistant tools.

## 2. Home Assistant permissions: what this user may control

When a request is associated with an authenticated Home Assistant user, model-driven entity actions are checked against that user's Home Assistant permissions. Being readable or exposed does not automatically mean the user may control it.

A permitted action runs as that same Home Assistant user. If Home Assistant denies the operation, Extended OpenAI does not bypass that decision merely because the model requested it.

Administrator-only management operations are also enforced by the backend, not just hidden in the interface.

## 3. Extended OpenAI restrictions: additional limits

Extended OpenAI can make access narrower still. Examples include:

- Guest Mode entity, area, domain and label exclusions
- Guest Mode control-only restrictions
- Guest Function Tool and Knowledge policies
- Function Tool enable/disable state
- Request Rules and other feature-specific validation

These layers cannot grant access that Home Assistant has denied. If several restrictions apply, all of them must allow the operation.

## Guest Mode and permissions together

Guest Mode is an extra restriction layer, not a replacement Home Assistant user account. A resource must be allowed by the relevant Home Assistant permissions **and** by Guest Mode before it can be controlled through that path.

A permissive Guest policy therefore cannot give a restricted Home Assistant user extra rights, while a restrictive Guest policy can still block something that user could normally control.

## Delayed actions

A delayed Function Tool is checked again when it becomes due. If the relevant user no longer has permission, the delayed action is completed without executing rather than relying on permission that existed when it was scheduled.

Pending delayed calls that survive a process restart keep the Home Assistant user context that originally scheduled them. If Home Assistant stopped at a point where an action may already have begun, Extended OpenAI will not automatically perform that uncertain action a second time after restart.

## Security guidance

For shared voice devices:

1. expose only entities that should be available to Assist;
2. use appropriate Home Assistant user permissions;
3. configure [Voice Identity](voice-identity.md) so retained personal data is scoped correctly;
4. use [Guest Mode](guest-mode.md) when visitors need an additional restriction policy.

Prompt instructions such as "do not control the alarm" can guide a model, but they are not a substitute for enforced access controls.
