# Broadcast

Broadcast sends a spoken message to announcement-capable Assist Satellites. Enable it from the **Extended OpenAI Overview** page. Administrators can choose destinations and manage delivery from the dedicated Broadcast UI; trusted automations can use the `broadcast` Home Assistant action. A model can use the `send_broadcast` native Function Tool when it is configured and available.

## Capacity limits

Broadcast accepts at most 4,096 characters and 32 destinations per message, 100 active messages, 256 queued or delivering destination deliveries globally, and 16 per satellite. A request exceeding any limit is rejected as a whole before any delivery is queued. Completion, expiry, and disabling Broadcast release capacity.

## Destinations and delivery

Select satellites directly or target them by device, area, floor, or label. A whole-home announcement targets eligible satellites except the originating satellite. Busy satellites are queued rather than interrupted; each queued delivery has an expiry (TTL). Turning Broadcast off stops pending delivery.

The action and Function Tool require an explicit destination unless whole-home delivery is requested. Home Assistant access checks apply to target resolution. See [Home Assistant actions](../services.md) and [Native Function Tools](../functions/native-details.md).

### Removal and reinstallation

The global Quiet Hours policy and Broadcast enabled setting are retained when the final EOAI integration entry is removed. Reinstalling EOAI loads these settings again. Disable each feature before removing EOAI if you do not want it to reactivate after reinstallation. Quiet Hours restoration records are also retained so unavailable controls can be restored when EOAI returns.
