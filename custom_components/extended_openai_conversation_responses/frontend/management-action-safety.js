const PATCHED = Symbol.for("extended-openai.management-action-safety");

const MUTATIONS = new Map([
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

function syncAgentPicker(panel) {
  const picker = panel.shadowRoot?.querySelector?.("#agent");
  if (!picker) return;
  const pending = Number(panel._eocAgentMutations || 0);
  picker.disabled = pending > 0;
  if (pending > 0) picker.setAttribute("aria-busy", "true");
  else picker.removeAttribute("aria-busy");
}

async function runMutation(panel, originalCall, section, action, extra) {
  panel._eocAgentMutations = Number(panel._eocAgentMutations || 0) + 1;
  syncAgentPicker(panel);
  try {
    const result = await originalCall.call(panel, section, action, extra);
    if (section === "tools" && typeof result?.revision === "string" && panel._configData) {
      panel._configData = {...panel._configData, revision: result.revision};
    }
    return result;
  } finally {
    panel._eocAgentMutations = Math.max(0, Number(panel._eocAgentMutations || 1) - 1);
    syncAgentPicker(panel);
  }
}

export function installManagementActionSafety(Panel) {
  // A constructor is the production API; registry callers remain supported.
  if (typeof Panel !== "function") {
    const registry = Panel || globalThis.customElements;
    if (!registry?.whenDefined) return Promise.resolve(false);
    return registry.whenDefined("extended-openai-management-panel").then(() => installManagementActionSafety(registry.get("extended-openai-management-panel")));
  }
  const prototype = Panel?.prototype;
  if (!prototype || prototype[PATCHED]) return false;
  prototype[PATCHED] = true;

  const originalCall = prototype._call;
  prototype._call = function(section, action, extra = {}) {
    if (!isAgentMutation(section, action)) {
      return originalCall.call(this, section, action, extra);
    }
    if (section !== "tools") {
      this._pendingMutations ||= new Map();
      const key = JSON.stringify([section, action, extra]);
      if (this._pendingMutations.has(key)) return this._pendingMutations.get(key);
      const pending = runMutation(this, originalCall, section, action, extra).finally(() => this._pendingMutations.delete(key));
      this._pendingMutations.set(key, pending);
      return pending;
    }

    const previous = this._eocFunctionMutationTail || Promise.resolve();
    const pending = previous.catch(() => {}).then(() =>
      runMutation(this, originalCall, section, action, extra));
    this._eocFunctionMutationTail = pending;
    return pending.finally(() => {
      if (this._eocFunctionMutationTail === pending) this._eocFunctionMutationTail = null;
    });
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    syncAgentPicker(this);
    return result;
  };

  return true;
}
