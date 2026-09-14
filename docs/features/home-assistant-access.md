# Home Assistant access and permissions

Extended OpenAI uses several independent access layers. An entity being visible to Assist does not by itself guarantee that every user may control it.

## Assist exposure

Home Assistant's Assist exposure controls which normal entities are available to conversation agents. Extended OpenAI uses that exposure boundary for entity discovery and normal Home Assistant tool access.

The separate **Include exposed devices** setting under **Assistant → Prompt & context** only decides whether exposed entity names and current states are automatically added to the model prompt. Turning that prompt option off does not revoke access to entities that remain available through Home Assistant tools.

## Home Assistant user permissions

When a request is associated with an authenticated Home Assistant user, model-driven entity actions are checked against that user's Home Assistant permissions. Readable or exposed does not imply permission to control.

A permitted action runs with the same Home Assistant user context. If Home Assistant denies the operation, Extended OpenAI does not bypass that decision merely because the model requested it.

Administrator-only management operations are also enforced at the backend. Hiding a frontend control is not treated as the security boundary.

## Extended OpenAI restrictions

Extended OpenAI can narrow access further. Examples include:

- Guest Mode entity, area, domain and label exclusions
- Guest Mode control-only restrictions
- Guest Function Tool and Knowledge policies
- Function Tool enable/disable state
- Request Rules and other feature-specific validation

These layers do not grant access that Home Assistant has denied. When more than one policy applies, the effective result is the most restrictive combination.

## Guest Mode and permissions together

Guest Mode is an additional restriction layer, not a replacement Home Assistant user account. A resource must be allowed by the relevant Home Assistant permissions **and** by Guest Mode before it can be controlled through that path.

This means a permissive Guest policy cannot elevate a restricted Home Assistant user, while a restrictive Guest policy can still deny something that the underlying Home Assistant user could normally control.

## Delayed actions

A delayed Function Tool is not authorized only once at scheduling time. When it becomes due, Extended OpenAI rechecks the current active user and current control authorization before execution. If authorization has been removed, the delayed action is finalized without executing rather than using stale permission from the earlier request.

Pending delayed calls that survive a process restart retain the original Home Assistant user context. Execution remains at-most-once across the persisted execution boundary rather than replaying an action whose execution status is uncertain.

## Security guidance

For shared voice devices, combine these layers deliberately:

1. expose only entities that should be available to Assist;
2. use appropriate Home Assistant user permissions;
3. configure [Voice Identity](voice-identity.md) so retained personal data is scoped correctly;
4. use [Guest Mode](guest-mode.md) when visitors need an additional restriction policy.

Prompt instructions such as "do not control the alarm" can guide a model, but they are not a substitute for enforced authorization boundaries.
