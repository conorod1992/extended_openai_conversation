# Quiet Hours

Configure Quiet Hours from **Extended OpenAI → Capabilities → Quiet Hours**.

Quiet Hours gives Assist satellites a daily night-time schedule. During that period, Extended OpenAI can lower a satellite's speaker volume to a maximum level and optionally change its wake-word sound where Home Assistant exposes a compatible switch.

## Volume behaviour

Think of the configured volume as a **maximum**, not a fixed value. A satellite that is louder is turned down; a satellite that is already quieter is left alone.

When Extended OpenAI changes a speaker, it remembers the earlier volume. At the end of Quiet Hours it restores that earlier value only if the speaker still has the value Quiet Hours set. If you or another automation changes the volume in the meantime, that newer change is preserved.

If Home Assistant starts or restarts while the current time is already inside the saved Quiet Hours period, the integration catches up and applies the active settings.

## Wake-word sound

The wake-word sound can be set to:

- **Off during Quiet Hours**
- **On during Quiet Hours**
- **Don't change it**

This controls the short sound played when a compatible satellite detects its wake word. Many satellites do not expose a separate switch for this, so the option is applied only when Extended OpenAI can discover a compatible switch or you select one manually.

## Satellite discovery and overrides

Extended OpenAI discovers Assist satellites from Home Assistant and tries to associate each one with its speaker media player and wake-word sound switch. The Quiet Hours page shows the detected entities and whether the speaker is currently usable.

Use the per-satellite overrides when automatic discovery finds the wrong entity or cannot identify one. Leaving an override empty returns that satellite to automatic discovery.

## Use the schedule in automations

Quiet Hours exposes a read-only Home Assistant entity for the active period. It remains on for the whole scheduled window even when no speaker needs to be changed, so normal Home Assistant automations can use the same schedule for LEDs or other device-specific behaviour.

The saved schedule can also be enabled or disabled with:

- `extended_openai_conversation_responses.enable_quiet_hours`
- `extended_openai_conversation_responses.disable_quiet_hours`

These actions simply turn the saved daily schedule on or off; they do not create a second schedule.

## Practical notes

- The configured maximum applies to the satellite media player, so other audio from the same speaker may also be quieter during the period.
- Quiet Hours does not overwrite a newer user or automation volume change merely to restore an older value.
- A satellite discovered later can be picked up without recreating the schedule.
- LEDs and other vendor-specific night-mode settings are intentionally left to ordinary Home Assistant automations using the Quiet Hours entity.
