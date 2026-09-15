# Voice Identity and data scope

Configure voice ownership from **Extended OpenAI → Assistant → Voice & identity**.

Voice Identity controls whose retained data a voice request is allowed to use. It is most useful when one Home Assistant installation has multiple users or several Assist satellites, especially when some devices are personal and others are shared.

It is separate from conversation continuity: identity answers **whose data may this request use?**, while continuity answers **which recent conversation should this request continue?**

## How a voice source is identified

For Assist requests that contain both a Home Assistant device-registry device ID and satellite metadata, Extended OpenAI uses the Home Assistant `device_id` as the canonical source identity. Satellite metadata remains useful as context and as a fallback when no registry device ID is available.

This avoids treating two identifiers for the same physical voice source as different owners.

## Voice scope policies

The Voice & identity page lets you configure:

- **Unidentified voice requests** — what retained-data scope is used when Home Assistant cannot identify the speaker/source sufficiently.
- **Unmapped-device fallback** — what happens when device assignment is enabled but the source device has no assignment.
- **Default voice user** — the Home Assistant user whose retained data is used when a policy explicitly selects the default-user scope.
- **Voice device assignments** — map a source voice device to a Home Assistant user, shared-household data, or no retained personal data.

A device mapped to a user can use that user's eligible personal memories, temporary context and retained conversation scope according to the agent's other settings. Shared-household and no-personal-data scopes keep personal information from being attached merely because a request came from a voice device.

## Shared devices

Do not map a genuinely shared kitchen or living-room satellite to one person simply because that person configured it. Doing so can make every speaker at that device appear to be the same user for retained-data purposes.

For shared devices, use shared-household data or no retained personal data unless the device can reliably identify the actual speaker by another supported mechanism.

## New, renamed, or replaced satellites

After changing voice hardware or Home Assistant device registrations, review **Voice device assignments** and the **Unmapped-device fallback**. A source that no longer matches its old assignment should not silently inherit somebody's personal data through an overly broad fallback.

## Relationship to archive and continuity

The resolved voice scope is also used when Extended OpenAI decides where eligible retained conversations belong. Device-to-user mappings therefore affect more than memory retrieval: they help keep personal archive/continuity data associated with the intended user or shared scope.

Changing an assignment does not merge historical user data or make Guest conversations part of an owner's archive.

## Relationship to Home Assistant permissions

Voice Identity chooses the user/data scope used by Extended OpenAI. It does not grant Home Assistant permissions that the resolved requesting user does not have. Entity actions remain subject to Home Assistant authorization and any stricter Extended OpenAI policy such as Guest Mode.

See [Home Assistant access and permissions](home-assistant-access.md) for the authorization layers.
