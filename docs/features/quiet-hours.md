# Quiet Hours

Configure Quiet Hours from **Extended OpenAI → Capabilities → Quiet Hours**.

Quiet Hours gives Assist satellites a daily night-time schedule. When that period starts, Extended OpenAI can lower louder satellites' speaker volumes to a starting limit and optionally change their wake-word sound where Home Assistant exposes a compatible switch.

## Volume behaviour

Think of the configured volume as a **starting volume limit**, not a ceiling that is continuously enforced. When Quiet Hours starts, a louder satellite is turned down and an already quieter satellite is left alone. Satellites that become available later can also be adjusted.

When Extended OpenAI changes a speaker, it remembers the earlier volume. If you or an automation changes the volume afterwards, Quiet Hours respects that independent change for the rest of that quiet period instead of continually lowering it again. When Quiet Hours ends, the earlier volume is restored only if the speaker has not been independently changed.

If Home Assistant starts or restarts while the current time is already inside the saved Quiet Hours period, the integration catches up and applies the active settings.

The daily schedule follows Home Assistant’s configured timezone. Changing that timezone in Home Assistant immediately rechecks the current period and resets its start/end timers. Existing control ownership and manual adjustments still govern application and restoration.

## Wake-word sound

The wake-word sound can be set to:

- **Off during Quiet Hours**
- **On during Quiet Hours**
- **Don't change it**

This controls the short sound played when a compatible satellite detects its wake word. Many satellites do not expose a separate switch for this, so the option is applied only when Extended OpenAI can discover a compatible switch or you select one manually.

## Satellite discovery and overrides

Extended OpenAI discovers Assist satellites from Home Assistant and selects controls automatically only when their association is unambiguous. The Quiet Hours page distinguishes automatic and manual selections, **Manual selection required**, no compatible control, and a selected control that is temporarily unavailable.

For speaker volume, exactly one enabled media player registered to the satellite's Home Assistant device is selected automatically. With multiple registered media players, select the correct speaker manually. Offline players and players without a current volume attribute still count; changes in availability, names, entity IDs, or platforms cannot make one player win over another. With no registered player, no speaker is selected. A selected speaker is adjusted only when it reports a usable volume.

Automatic wake-word sound selection is limited to one recognised stock Voice PE configuration switch: an ESPHome switch whose original firmware name is **Wake sound**, on a device identified as **Nabu Casa / Home Assistant Voice PE**. Multiple such switches require a manual selection. Generic switches, descriptive user names, and other devices or custom firmware do not establish this optional capability; select a switch manually only if it controls the wake-word chime. Unsupported wake-word sound is not a fault.

Saved manual selections take precedence. They follow the Home Assistant entity registry when a satellite, speaker or wake-sound switch is renamed, including across restarts. Removing a selected entity does not transfer its selection or restoration ownership to another entity that reuses its old ID. Leaving an override empty returns that satellite to conservative automatic discovery.

If discovery becomes ambiguous during a quiet period, EOAI retains verified existing ownership until that period ends, including after a restart. It does not transfer that ownership to a different automatic candidate or save the old association as a manual override. Your independent changes still release ownership, and explicit selection changes use the normal restoration and retargeting rules. The next quiet period uses the new discovery rules. An unavailable control's restoration obligation remains stored until restoration can be safely completed.

## Use the schedule in automations

Quiet Hours exposes a read-only Home Assistant entity for the active period. It remains on for the whole scheduled window even when no speaker needs to be changed, so normal Home Assistant automations can use the same schedule for LEDs or other device-specific behaviour.

The saved schedule can also be enabled or disabled with:

- `extended_openai_conversation_responses.enable_quiet_hours`
- `extended_openai_conversation_responses.disable_quiet_hours`

These actions simply turn the saved daily schedule on or off; they do not create a second schedule.

## Practical notes

- The starting volume adjustment applies to the satellite media player, so other audio from the same speaker may also be quieter during the period.
- Quiet Hours does not overwrite a newer user or automation volume change merely to restore an older value.
- A satellite discovered later can be picked up without recreating the schedule.
- LEDs and other vendor-specific night-mode settings are intentionally left to ordinary Home Assistant automations using the Quiet Hours entity.

### Retrying restoration

If a controlled speaker or wake-sound switch is unavailable when Quiet Hours ends, its original value remains stored. EOAI retries restoration on discovery ticks, including while the schedule is disabled, and keeps the baseline across Home Assistant restarts. A confirmed independent device change releases ownership without overwriting that change. Released choices remain respected if the Quiet Hours manager is replaced during the same policy period. Controls that EOAI still owns are restored on shutdown and can be reapplied by the replacement manager; a new policy period can apply the policy again.

Saving a changed policy succeeds before EOAI restores or changes devices. A failed settings save leaves the existing policy and device values in place.

Saving the same policy or changing only its schedule does not reclaim a manually adjusted control. Changing the speaker volume target also leaves manual wake-sound choices alone, and changing the wake-sound target leaves manual volume choices alone.

### Removal and reinstallation

The global Quiet Hours policy and Broadcast enabled setting are retained when the final EOAI integration entry is removed. Reinstalling EOAI loads these settings again. Disable each feature before removing EOAI if you do not want it to reactivate after reinstallation. Quiet Hours restoration records are also retained so unavailable controls can be restored when EOAI returns.

Quiet Hours saves check the policy revision loaded by the editor. If selections
or settings change while the page is open, reload the latest policy before
saving. Renaming a selected satellite also migrates its pending applications,
so interrupted changes can resume under the same physical satellite identity.

Unsaved policy edits recovered after Home Assistant recreates the panel retain
the revision they were based on. If the policy changed meanwhile, saving asks
you to reload; explicitly discarding the recovered edits adopts the latest
saved policy. Pending applications retain satellite registry identities for
both automatic discovery and manual selections, so a rename can resume safely
without treating a replacement satellite that reuses the old ID as the same
application.
