# Broadcast

Broadcast sends a spoken message to announcement-capable Assist Satellites. Enable it from the **Extended OpenAI Overview** page. Administrators can choose destinations and manage delivery from the dedicated Broadcast UI; trusted automations can use the `broadcast` Home Assistant action. A model can use the `send_broadcast` native Function Tool when it is configured and available.

## Built-in voice command

With **Enable Broadcast** on, say **“Broadcast dinner is ready.”** EOAI queues “dinner is ready” for the whole home without an AI request. **Built-in voice command** is on by default beside the master switch in **Overview → Broadcast**. It works even when the selected agent's general local handling is off, and also accepts text sent directly to the conversation agent.

The first word must be `Broadcast` (case-insensitive), followed by whitespace and a nonempty message. Leading/trailing whitespace is ignored; the message itself is literal. This shortcut does not resolve destinations, aliases, or room names, and does not recognize `tell`, `announce`, or `send`. Leading `to`, `in`, `at`, `after`, `for`, and the old `a message` form are reserved: for example, `Broadcast to kitchen saying hello` and `Broadcast in ten minutes dinner is ready` continue through normal handling. This is a small syntactic boundary, not a language or scheduling parser. Wording inside the message is spoken literally; use a Request Rule or AI for targeted or future actions.

Request Rules run first. A rule that handles the request or routes it to AI takes precedence over this shortcut. After the shortcut, other eligible Home Assistant local intents can run, then normal AI handling. EOAI never delegates `HassBroadcast` through its internal generic local-intent path, including when this shortcut is disabled. Once a command attempts queue admission, a failure is returned locally and never retried through AI. “Broadcast queued” confirms admission, not completed playback.

Administrators can turn **Built-in voice command** off to manage Broadcast phrases entirely with Request Rules or their AI model. This preference is saved globally and independently of **Enable Broadcast**. Disabling it does not disable the typed composer, Home Assistant action, Request Rules or Function Tools. Guest Mode skips the shortcut and retains its existing policy path. Every admission retains caller permissions and the originating satellite/device exclusion; all resolved destinations require the caller's Home Assistant CONTROL permission.

Home Assistant's pipeline-level **Prefer handling commands locally** (also labelled **Prefer local handling** in some versions) is separate. An upstream Home Assistant intent may execute before EOAI receives a request. Neither EOAI's shortcut switch nor its intent exclusions control that upstream behavior.

### Custom phrase: tell the kitchen

Configure and enable the native `send_broadcast` Function Tool for the selected agent under **Function Tools** first. Its Function Group, if any, must also be enabled. Enable Broadcast globally and ensure Kitchen resolves to the intended announcement-capable satellites.

Create a Request Rule with:

1. **Trigger phrases or patterns:** `tell the kitchen {message}`.
2. **How should it match?:** **ExtendedOpenAI sentence pattern**.
3. **Behaviour:** **Run actions locally**. Add the **Call configured function** Home Assistant action (`extended_openai_conversation_responses.call_function`). Set **Function tool** to `send_broadcast`.
4. **Arguments:** fixed destination `kitchen`, captured message `message`. The current editor uses an Arguments object; enter the action below in its YAML editor. `{{ message }}` reads the request capture.
5. Leave **Continue to AI** off, use **Success response:** `Broadcast queued.`, then save the rule enabled.

```yaml
- action: extended_openai_conversation_responses.call_function
  data:
    function: send_broadcast
    arguments:
      destination: kitchen
      message: "{{ message }}"
```

“Tell the kitchen dinner is ready” calls `send_broadcast` with `destination: kitchen` and `message: dinner is ready`, without an LLM request. **Match Preview** checks matching without sending; **Live Test** performs the announcement. Unknown/ambiguous destinations, disabled tools, policy denials and queue failures use the rule's failure response. No new rule capabilities are required. See [Request Rules](request-rules.md#function-actions).

### Upgrading from 6.8.3

The published 6.8.3 free-form targeted local parser has been removed. Its `tell`, `announce`, `send`, and targeted `Broadcast to …` phrases now need Request Rules or AI. Backend destination resolution and delivery remain available to the UI, Function Tool and Home Assistant action.

Existing Broadcast master-switch values are preserved. Older global storage without the new field defaults **Built-in voice command** to on. Explicit saved per-agent `HassBroadcast` exclusions remain opt-outs from the new command, even when general local handling is off; the global switch does not override them. They appear as unavailable legacy choices in the local-handling exception list, so they can be retained or explicitly removed. New agents do not offer `HassBroadcast` as a generic delegated intent. No automatic migration rewrites agent exclusions.

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

The global Quiet Hours policy and both Broadcast settings are retained when the final EOAI integration entry is removed. Reinstalling EOAI loads these settings again. Disable each feature before removing EOAI if you do not want it to reactivate after reinstallation. Quiet Hours restoration records are also retained so unavailable controls can be restored when EOAI returns.
