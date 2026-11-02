# Broadcast

Broadcast sends a spoken message to announcement-capable Assist Satellites. Enable it from the **Extended OpenAI Overview** page. Administrators can choose destinations and manage delivery from the dedicated Broadcast UI; trusted automations can use the `broadcast` Home Assistant action. A model can use the `send_broadcast` native Function Tool when it is configured and available.

## Capacity limits

Broadcast accepts at most 4,096 characters and 32 destinations per message, 100 active messages, 256 queued or delivering destination deliveries globally, and 16 per satellite. A request exceeding any limit is rejected as a whole before any delivery is queued. Completion, expiry, and disabling Broadcast release capacity.

## Destinations and delivery

Select satellites directly or target them by device, area, floor, or label. A whole-home announcement targets eligible satellites except the originating satellite. Choose either whole-home delivery or specific destinations; combining them is rejected. Busy satellites are queued rather than interrupted; each queued delivery has an expiry (TTL). Turning Broadcast off stops pending delivery. Pending deliveries remain tracked independently of the recent-history limit, and queued announcements retain the originating Home Assistant context.

TTL limits waiting in the queue; it does not cancel an announcement already being delivered. Delivery waits for Home Assistant's blocking announcement call to finish synthesis and playback. A separate stall deadline allows 120 seconds plus one second per eight message characters, capped at 15 minutes. A timeout is reported as a failed delivery, and the next message still waits for an idle satellite.

The action and Function Tool require an explicit destination unless whole-home delivery is requested. Home Assistant access checks apply to target resolution. See [Home Assistant actions](../services.md) and [Native Function Tools](../functions/native-details.md).

### Retrying a send

The Broadcast composer retains the same attempted send after a temporary connection failure. Retrying its unchanged message and destinations returns the original queue admission instead of adding another announcement. A successful admission retires that attempt before history is refreshed.

Retry protection lasts up to ten minutes from the loaded Broadcast view and applies while the same Broadcast manager remains running. It is bounded to 512 admitted attempts; reaching capacity refuses new protected sends while preserving existing retry receipts. It does not persist across Home Assistant restart or integration reload. A changed message or destination, an expired retry window, or an uncertain admission requires confirmation before sending as a new announcement. Check recent delivery history first: the original announcement may already have played. Refreshing history alone does not renew an uncertain attempt.

This protection concerns queue admission. Delivery status remains separate, and retry protection does not guarantee that a satellite spoke the announcement. Existing automation actions and model Function Tools retain their existing send semantics.

### Removal and reinstallation

The global Quiet Hours policy and Broadcast enabled setting are retained when the final EOAI integration entry is removed. Reinstalling EOAI loads these settings again. Disable each feature before removing EOAI if you do not want it to reactivate after reinstallation. Quiet Hours restoration records are also retained so unavailable controls can be restored when EOAI returns.
