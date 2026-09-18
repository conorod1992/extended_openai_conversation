export const MUTATIONS = new Map([
  ["backup", new Set(["restore"])],
  ["quiet_hours", new Set(["update"])],
  ["configuration", new Set(["update", "save"])],
  ["conversations", new Set(["delete", "end_active"])],
  ["guest_mode", new Set(["save_policy", "update", "disable"])],
  ["knowledge", new Set(["create", "update", "delete"])],
  ["memories", new Set(["add", "update", "delete", "temporary_delete", "temporary_update", "reassign_legacy"])],
  ["request_rules", new Set(["defaults", "wording_groups", "create", "update", "delete", "duplicate", "move"])],
  ["tools", new Set(["save", "delete", "set_enabled", "save_group", "delete_group"])],
  ["usage", new Set(["clear_details"])],
]);

export function isAgentMutation(section, action) {
  return Boolean(MUTATIONS.get(section)?.has(action));
}

export function syncAgentPicker(panel) {
  for (const id of ["guest-now", "guest-update", "guest-disable"]) {
    const button = panel.shadowRoot?.querySelector?.(`#${id}`);
    if (button) button.disabled = Boolean(panel._guestOperation);
  }
  const picker = panel.shadowRoot?.querySelector?.("#agent");
  if (!picker) return;
  const pending = Number(panel._eocAgentMutations || 0);
  picker.disabled = pending > 0;
  if (pending > 0) picker.setAttribute("aria-busy", "true");
  else picker.removeAttribute("aria-busy");
}

