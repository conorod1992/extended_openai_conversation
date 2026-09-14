const PATCHED = Symbol.for("extended-openai.management-mutation-safety");

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

export function installManagementMutationSafety(registry = globalThis.customElements) {
  if (typeof window === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const Panel = registry.get("extended-openai-management-panel");
    const prototype = Panel?.prototype;
    if (!prototype || prototype[PATCHED]) return false;
    prototype[PATCHED] = true;

    const originalCall = prototype._call;
    prototype._call = async function(section, action, extra = {}) {
      if (!isAgentMutation(section, action)) {
        return originalCall.call(this, section, action, extra);
      }
      this._eocAgentMutations = Number(this._eocAgentMutations || 0) + 1;
      syncAgentPicker(this);
      try {
        return await originalCall.call(this, section, action, extra);
      } finally {
        this._eocAgentMutations = Math.max(0, Number(this._eocAgentMutations || 1) - 1);
        syncAgentPicker(this);
      }
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

void installManagementMutationSafety();
