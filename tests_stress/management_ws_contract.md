# Browser → Home Assistant management contract

The nightly browser job runs `real-ha-backend.spec.mjs` against a genuine Home Assistant config entry and authenticated Home Assistant WebSocket client. Its loopback HTTP bridge forwards browser messages to that client; it does not call `async_management_command()` directly. This is the boundary where Home Assistant's `websocket_command` schema rejected the Request Rule `groups` payload.

The feature evidence manifest names this layer `browser_real_ha`, distinct from browser tests backed by JavaScript fixtures. Only the genuine-HA backend and shared-backend two-tab specs may claim that layer.

`management_ws_contract.json` lists reviewed mutation and transfer actions. Each entry names a browser journey, WebSocket action, required top-level payload keys, and (where useful) expected values or the minimum number of calls. The browser suite checks the actual messages it sent. The Python guard checks the keys against the registered Home Assistant command schemas. UI tests then refresh the page to check authoritative durable state, including deleted objects staying deleted. The existing two-tab genuine-HA suite covers stale writers; provider-wire suites separately check runtime behavior.

The first contract pass also found that Rule Pack export/review/import payloads were rejected before reaching their handlers. The same genuine-HA browser journey now covers export → review → import, including the nullable group and selected-rule fields sent by the export form.

When adding a browser management mutation, update the manifest and exercise the new payload through the genuine-HA browser suite. Prefer a browser click, an asserted outgoing action and payload, a visible result, and a fresh authoritative read. Security-sensitive changes should also receive a public Assist/provider-wire assertion in the relevant campaign.

This is a reviewed list of high-value mutation payloads, not a claim that every browser read or preview action is exercised. The fixture-backed endurance and stale-response suites continue to cover long-lived UI state; their mocked backend calls are not counted as genuine-HA WebSocket evidence.
