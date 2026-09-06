const PANEL_TAG = "extended-openai-management-panel";
const PATCHED = Symbol.for("extended-openai.management-function-dependencies");
const TOOL_MUTATIONS = new Set(["save", "set_enabled", "delete", "save_group", "delete_group"]);
const REQUEST_RULE_CACHE_KEY = "capabilities/request-rules";

export function installFunctionDependencyIntegrity(registry = globalThis.customElements) {
  if (!registry) return false;

  const install = () => {
    const constructor = registry.get(PANEL_TAG);
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;
    prototype[PATCHED] = true;

    const originalCall = prototype._call;
    prototype._call = async function(section, action, extra = {}) {
      let payload = extra;
      if (
        section === "tools"
        && TOOL_MUTATIONS.has(action)
        && extra.revision === undefined
        && typeof this._configData?.revision === "string"
      ) {
        payload = {revision: this._configData.revision, ...extra};
      }

      const result = await originalCall.call(this, section, action, payload);
      if (
        section === "tools"
        && TOOL_MUTATIONS.has(action)
        && typeof result?.revision === "string"
        && this._configData
      ) {
        this._configData = {...this._configData, revision: result.revision};
      }
      return result;
    };

    const originalInvalidate = prototype._invalidateAfterMutation;
    prototype._invalidateAfterMutation = function(agentId, section, action) {
      originalInvalidate.call(this, agentId, section, action);
      if (!agentId) return;
      const affectsRequestRules =
        (section === "tools" && TOOL_MUTATIONS.has(action))
        || (section === "request_rules" && action === "move");
      if (!affectsRequestRules) return;
      const key = `${agentId}|${REQUEST_RULE_CACHE_KEY}`;
      this._sectionCache?.delete(key);
      this._eocSectionCacheTimes?.delete(key);
    };
    return true;
  };

  if (registry.get?.(PANEL_TAG)) return install();
  registry.whenDefined?.(PANEL_TAG).then(install);
  return true;
}

if (typeof customElements !== "undefined") installFunctionDependencyIntegrity(customElements);

export {REQUEST_RULE_CACHE_KEY, TOOL_MUTATIONS};
