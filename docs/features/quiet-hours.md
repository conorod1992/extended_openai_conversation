# Quiet Hours

Configure Quiet Hours from **Extended OpenAI → Capabilities → Quiet Hours**.

Quiet Hours provides one daily schedule for Assist satellites. During the configured period, Extended OpenAI can lower satellite speaker volume to a maximum level and optionally change the wake-word sound where a compatible switch is available.

## Volume behaviour

Quiet Hours applies a ceiling, not a fixed volume. A satellite that is louder than the configured maximum is turned down; a satellite that is already quieter is left alone.

When Extended OpenAI changes a speaker, it records the value it replaced. At the end of Quiet Hours it restores that earlier value only if Quiet Hours still owns the current value. If you or another automation changes the volume while Quiet Hours is active, that newer value is preserved rather than overwritten.

If Home Assistant starts or restarts while the current time is already inside the saved Quiet Hours period, the integration catches up and applies the active policy.

## Wake-word sound

The wake-word sound can be set to:

- **Off during Quiet Hours**
- **On during Quiet Hours**
- **Don't change it**

This controls the short sound played when a compatible satellite detects its wake word. Many satellites do not expose a separate switch for this, so the option is applied only when Extended OpenAI can discover a compatible switch or you select one manually.

## Satellite discovery and overrides

Extended OpenAI discovers Assist satellites from Home Assistant and attempts to associate each one with its speaker media player and wake-word sound switch. The Quiet Hours page shows the detected entities and whether the speaker is currently usable.

Use the per-satellite overrides when automatic discovery finds the wrong entity or cannot identify one. Leaving an override empty returns that satellite to automatic discovery.

## Use the schedule in automations

Quiet Hours exposes a read-only Home Assistant entity for the active period. It remains on for the whole scheduled window even when no speaker needs to be changed, so normal Home Assistant automations can use the same schedule for LEDs or other device-specific behaviour.

The saved schedule can also be enabled or disabled with:

- `extended_openai_conversation_responses.enable_quiet_hours`
- `extended_openai_conversation_responses.disable_quiet_hours`

These actions change whether the saved daily schedule is enabled; they do not create a separate second schedule.

## Practical notes

- The configured maximum applies to the satellite media player, so other audio from the same speaker may also be quieter during the period.
- Quiet Hours does not overwrite a newer user or automation volume change merely to restore an older value.
- A satellite discovered later can be picked up without recreating the schedule.
- LEDs and other vendor-specific night-mode settings are intentionally left to ordinary Home Assistant automations using the Quiet Hours entity.
