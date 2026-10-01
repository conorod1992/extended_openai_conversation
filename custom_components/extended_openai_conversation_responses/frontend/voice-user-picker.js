// HA's automation editor registers the same native picker used by its user conditions.
let pickerLoadPromise;
const userRequests = new WeakMap();
const hassFor = panel => panel.hass || panel._hass;
const connectionFor = hass => hass?.connection || hass;

export function voiceUserCatalogue(panel) {
  if (panel._voiceUsersConnection === connectionFor(hassFor(panel)) && Array.isArray(panel._voiceUsers)) {
    return panel._voiceUsers.filter(user => !user.system_generated);
  }
  const scopes = panel._baseScopes?.length ? panel._baseScopes : panel._data?.scopes || [];
  return scopes.filter(scope => scope.scope_type === "user")
    .map(scope => ({id: String(scope.scope_id || "").replace(/^user:/, ""), name: scope.display_name}));
}

export async function ensureVoiceUserPicker(documentRef = globalThis.document) {
  const registry = documentRef?.defaultView?.customElements || globalThis.customElements;
  if (registry?.get("ha-user-picker")) return;
  if (!pickerLoadPromise) {
    pickerLoadPromise = (async () => {
      const resolver = documentRef.createElement("partial-panel-resolver");
      const routes = (resolver.getRoutes || resolver._getRoutes)?.call(resolver, [
        {component_name: "config", url_path: "config"},
      ]);
      await routes?.routes?.config?.load?.();
      const config = documentRef.createElement("ha-panel-config");
      await config.routerOptions?.routes?.automation?.load?.();
      if (!registry?.get("ha-user-picker")) throw new Error("Home Assistant user picker could not be loaded.");
    })().finally(() => { pickerLoadPromise = null; });
  }
  await pickerLoadPromise;
}

export function initializeVoiceUserPicker(panel, picker, value, onReady) {
  if (!picker) return;
  picker.hass = hassFor(panel);
  const initialValue = String(value || "").replace(/^user:/, "");
  // The native generic picker caches its selected label. Do not render a saved
  // ID against an empty catalogue, or it can retain an "unknown user" label.
  picker.value = "";
  let edited = false;
  const markEdited = () => { edited = true; };
  picker.addEventListener("value-changed", markEdited);
  const hass = hassFor(panel);
  const connection = connectionFor(hass);
  if (!hass?.callWS) { picker.removeEventListener("value-changed", markEdited); return; }
  const agentId = panel._agentId;
  let request = userRequests.get(connection);
  if (!request) {
    request = hass.callWS({type: "config/auth/list"}).finally(() => userRequests.delete(connection));
    userRequests.set(connection, request);
  }
  return Promise.all([ensureVoiceUserPicker(picker.ownerDocument), request]).then(([, users]) => {
    if (!picker.isConnected || panel._agentId !== agentId
        || panel._viewKey?.() !== "assistant/voice" || connectionFor(hassFor(panel)) !== connection) return;
    panel._voiceUsers = users;
    panel._voiceUsersConnection = connection;
    picker.hass = hassFor(panel);
    picker.users = users;
    // Reapply after custom-element upgrade using the current value and HA context.
    picker.value = edited ? String(picker.value || "").replace(/^user:/, "") : initialValue;
    onReady?.();
  }).catch(error => {
    if (picker.isConnected && panel._agentId === agentId && connectionFor(hassFor(panel)) === connection) {
      if (!edited) picker.value = initialValue;
      panel._toast?.(`Unable to load Home Assistant users: ${error.message || String(error)}`, true);
    }
  }).finally(() => picker.removeEventListener("value-changed", markEdited));
}
