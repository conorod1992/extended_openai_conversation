# Broadcast

Broadcast sends a spoken message to announcement-capable Assist Satellites. Enable it from the **Extended OpenAI Overview** page. Administrators can choose destinations and manage delivery from the dedicated Broadcast UI; trusted automations can use the `broadcast` Home Assistant action. A model can use the `send_broadcast` native Function Tool when it is configured and available.

## Capacity limits

Broadcast accepts at most 4,096 characters and 32 destinations per message, 100 active messages, 256 queued or delivering destination deliveries globally, and 16 per satellite. A request exceeding any limit is rejected as a whole before any delivery is queued. Completion, expiry, and disabling Broadcast release capacity.

## Destinations and delivery

Select satellites directly or target them by device, area, floor, or label. A whole-home announcement targets eligible satellites except the originating satellite. Choose either whole-home delivery or specific destinations; combining them is rejected. Busy satellites are queued rather than interrupted; each queued delivery has an expiry (TTL). Turning Broadcast off stops pending delivery. Pending deliveries remain tracked independently of the recent-history limit, and queued announcements retain the originating Home Assistant context.

TTL limits waiting in the queue; it does not cancel an announcement already being delivered. Delivery waits for Home Assistant's blocking announcement call to finish synthesis and playback. A separate stall deadline allows 120 seconds plus one second per eight message characters, capped at 15 minutes. A timeout is reported as a failed delivery, and the next message still waits for an idle satellite.

The action and Function Tool require an explicit destination unless whole-home delivery is requested. Home Assistant access checks apply to target resolution. See [Home Assistant actions](../services.md) and [Native Function Tools](../functions/native-details.md).

### Retrying a send

The Broadcast composer retains the same attempted send after a temporary connection failure. Retrying its unchanged message and destinations returns the original queue admission instead of adding another announcement. A successful admission retires that attempt before history is refreshed. If your Home Assistant account changes before the result arrives, the retained draft keeps a new-send confirmation barrier: returning to the original account and refreshing devices cannot silently turn that already admitted message into a repeat. The composer keeps a visible reminder while an earlier send needs reconciliation or explicit new-send intent.

Each fresh send obtains a current server-issued retry window, even when the Broadcast page has been idle. Retry protection lasts up to ten minutes from that preparation and applies while the same Broadcast manager remains running. It is bounded to 512 admitted attempts across all users; reaching capacity refuses new protected sends while preserving existing retry receipts. The error confirms that the new send was not queued and gives the time until the earliest receipt expires. Other sends can consume that space, so this is a suggested wait rather than a reservation. Your message and destinations remain editable, and retrying an already admitted attempt still works at capacity.

Retry protection does not persist across Home Assistant restart or integration reload. The pending queue and recent delivery history are also held in memory and are cleared when the manager stops. An absent history entry after restart therefore does not prove that an announcement was never spoken. An old retry is rejected, rather than silently queued as a new announcement.

A changed message or destination, an expired retry window, or an uncertain admission requires confirmation before sending as a new announcement. Check recent delivery history first while it is available: the original announcement may already have played. Refreshing history alone does not renew an uncertain attempt. After restart, confirm with the intended recipients if repeating the announcement would be disruptive.

This protection concerns queue admission. Delivery status remains separate, and retry protection does not guarantee that a satellite spoke the announcement. Existing automation actions and model Function Tools retain their existing send semantics.

### Removal and reinstallation

The global Quiet Hours policy and Broadcast enabled setting are retained when the final EOAI integration entry is removed. Reinstalling EOAI loads these settings again. Disable each feature before removing EOAI if you do not want it to reactivate after reinstallation. Quiet Hours restoration records are also retained so unavailable controls can be restored when EOAI returns.
