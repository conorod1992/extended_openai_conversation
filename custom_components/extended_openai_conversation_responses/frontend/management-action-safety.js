const PATCHED = Symbol.for("extended-openai.management-action-safety");

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

    return true;
  });
}

if (typeof customElements !== "undefined") {
  void installManagementActionSafety();
}
