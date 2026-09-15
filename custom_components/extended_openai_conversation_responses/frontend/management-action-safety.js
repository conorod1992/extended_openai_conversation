const PATCHED = Symbol.for("extended-openai.management-action-safety");

const MUTATIONS = new Map([
  ["backup", new Set(["restore"])],
  ["configuration", new Set(["update", "save"])],
  ["conversations", new Set(["delete", "end_active"])],
  ["guest_mode", new Set(["save_policy", "update", "disable"])],
  ["knowledge", new Set(["create", "update", "delete"])],
  ["memories", new Set(["add", "update", "delete", "temporary_delete", "reassign_legacy"])],
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

export function installManagementActionSafety(registry = globalThis.customElements) {
  if (typeof window === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const Panel = registry.get("extended-openai-management-panel");
    const prototype = Panel?.prototype;
    if (!prototype || prototype[PATCHED]) return false;
    prototype[PATCHED] = true;

    const originalSaveGuestPolicy = prototype._saveGuestPolicy;
    prototype._saveGuestPolicy = function(...args) {
      if (this._eocGuestPolicySavePromise) return this._eocGuestPolicySavePromise;

      const button = this.shadowRoot?.querySelector?.("#guest-policy-save");
      if (button?.disabled) return Promise.resolve();
      this._setSaving?.(button, true);

      const pending = Promise.resolve().then(() => originalSaveGuestPolicy.apply(this, args));
      this._eocGuestPolicySavePromise = pending;
      return pending.finally(() => {
        if (this._eocGuestPolicySavePromise === pending) this._eocGuestPolicySavePromise = null;
        this._setSaving?.(button, false);
      });
    };

    const originalCall = prototype._call;
    prototype._call = function(section, action, extra = {}) {
      if (!isAgentMutation(section, action)) {
        return originalCall.call(this, section, action, extra);
      }
      if (section !== "tools") {
        return runMutation(this, originalCall, section, action, extra);
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
  });
}

if (typeof customElements !== "undefined") {
  void installManagementActionSafety();
}
