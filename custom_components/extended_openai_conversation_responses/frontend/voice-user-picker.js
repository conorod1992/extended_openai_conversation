import {ensureNativePicker} from "./native-picker-loader.js";
const userRequests = new WeakMap();
const pickerInitializations = new WeakMap();
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
  await ensureNativePicker("ha-user-picker", documentRef);
}

export function voiceUserPickerValue(picker) {
  const initial = pickerInitializations.get(picker);
  // Loading the native catalogue temporarily blanks only its display. Keep the
  // selected user's meaning until an actual choice or clear replaces it.
  return initial && !initial.edited ? initial.value : picker?.value;
}

export function initializeVoiceUserPicker(panel, picker, value, onReady) {
  if (!picker) return;
  picker.hass = hassFor(panel);
  const initialValue = String(value || "").replace(/^user:/, "");
  // The native generic picker caches its selected label. Do not render a saved
  // ID against an empty catalogue, or it can retain an "unknown user" label.
  picker.value = "";
  const initial = {value:initialValue, edited:false};
  pickerInitializations.set(picker, initial);
  const markEdited = () => { initial.edited = true; };
  picker.addEventListener("value-changed", markEdited);
  const hass = hassFor(panel);
  const connection = connectionFor(hass);
  if (!hass?.callWS) { picker.removeEventListener("value-changed", markEdited); pickerInitializations.delete(picker); return; }
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
    picker.value = initial.edited ? String(picker.value || "").replace(/^user:/, "") : initialValue;
    onReady?.();
  }).catch(error => {
    if (picker.isConnected && panel._agentId === agentId && connectionFor(hassFor(panel)) === connection) {
      if (!initial.edited) picker.value = initialValue;
      panel._toast?.(`Unable to load Home Assistant users: ${error.message || String(error)}`, true);
    }
  }).finally(() => {
    picker.removeEventListener("value-changed", markEdited);
    if (pickerInitializations.get(picker) === initial) pickerInitializations.delete(picker);
  });
}
