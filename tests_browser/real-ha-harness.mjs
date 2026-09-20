const params = new URLSearchParams(location.search);
const route = params.get("route") || "assistant/basics";
const backendUrl = params.get("backend");
if (!backendUrl) throw new Error("Real HA browser fixture requires a backend URL");

history.replaceState({}, "", `/extended-openai/${route}`);

const calls = [];
const hass = {
  config: {time_zone: "Europe/Dublin"},
  callWS: async (message) => {
    calls.push(structuredClone(message));
    const response = await fetch(backendUrl, {
      method: "POST",
      body: JSON.stringify(message),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.message || payload?.error?.message || `Home Assistant WebSocket bridge returned ${response.status}`);
    return payload;
  },
};

window.browserHarness = {calls, hass, windowErrors: [], rejections: []};
window.addEventListener("error", (event) => window.browserHarness.windowErrors.push(String(event.error || event.message)));
window.addEventListener("unhandledrejection", (event) => window.browserHarness.rejections.push(String(event.reason)));

await import("/custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
const panel = document.createElement("extended-openai-management-panel");
document.body.append(panel);
panel.hass = hass;
window.browserHarness.panel = panel;
