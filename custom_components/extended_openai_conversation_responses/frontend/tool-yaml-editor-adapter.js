import {captureConfigurationOwner, configurationSubmissionIsCurrent} from "./configuration-acknowledgement.js";

export function toolYamlChangeHandler(panel, _yaml, detail = {}) {
  const status = panel?.shadowRoot?.querySelector("#tool-error");
  if (!status) return;
  if (detail?.isValid === false) {
    status.className = "validation invalid";
    status.textContent = detail.errorMsg || "Function Tool YAML is invalid.";
    return;
  }
  status.className = "validation";
  status.textContent = "YAML changed; validate to refresh metadata.";
}

export function createTextareaToolYamlEditor(
  textarea,
  {onChange = () => {}} = {},
) {
  const input = () => onChange(String(textarea?.value ?? ""), {});
  textarea?.addEventListener?.("input", input);
  return {
    textarea,
    getYaml() {
      return String(textarea?.value ?? "");
    },
    setYaml(value) {
      if (textarea) textarea.value = String(value ?? "");
    },
    focus() {
      textarea?.focus?.();
    },
    destroy() {
      textarea?.removeEventListener?.("input", input);
    },
  };
}

export function installToolYamlEditor(panel, adapter) {
  const current = panel?._toolYamlEditorAdapter;
  if (current && current !== adapter) current.destroy?.();
  if (panel) panel._toolYamlEditorAdapter = adapter;
  return adapter;
}

export function getToolYamlEditor(panel) {
  const textarea = panel?.shadowRoot?.querySelector("#tool-yaml");
  const current = panel?._toolYamlEditorAdapter;
  if (current?.textarea === textarea) return current;
  return installToolYamlEditor(
    panel,
    createTextareaToolYamlEditor(textarea, {
      onChange: (yaml, detail) => toolYamlChangeHandler(panel, yaml, detail),
    }),
  );
}

export function destroyToolYamlEditor(panel) {
  const current = panel?._toolYamlEditorAdapter;
  current?.destroy?.();
  if (panel && panel._toolYamlEditorAdapter === current) {
    panel._toolYamlEditorAdapter = null;
  }
}

// Loading, validation and acknowledgement belong to the same editor visit.
// Keep its normal/repair generations as well as the configuration owner: a
// dialog can remain mounted while its user, entry or draft session changes.
export function captureToolEditorOwner(panel) {
  const root = panel.shadowRoot;
  const dialog = root.querySelector("#tool-dialog");
  const normalGeneration = panel._toolEditorLoad;
  const repairGeneration = panel._repairToolLoad;
  const repairIndex = panel._repairToolIndex;
  const owner = captureConfigurationOwner(panel);
  const isCurrent = () => configurationSubmissionIsCurrent(panel, owner)
    && panel._toolEditorLoad === normalGeneration
    && panel._repairToolLoad === repairGeneration && panel._repairToolIndex === repairIndex
    && panel.shadowRoot === root && root.querySelector("#tool-dialog") === dialog && dialog.open;
  return {root, dialog, repairIndex, isCurrent};
}

// A submitted YAML snapshot owns only its validation/result presentation.
// Typing keeps the editor visit while invalidating that snapshot.
export function captureToolEditorOperation(panel) {
  const owner = captureToolEditorOwner(panel);
  const agent = panel._selectedAgent?.();
  const yaml = getToolYamlEditor(panel).getYaml();
  const originalName = panel._toolOriginalName;
  const otherNames = new Set((panel._configData?.config?.functions || [])
    .map(tool => tool.spec?.name).filter(name => name !== originalName));
  return {
    ...owner, yaml, baselineConfig:panel._configData?.config,
    originalName, otherNames,
    revision:Number.isInteger(owner.repairIndex) ? panel._repairToolRevision : panel._toolRevision,
    destination:agent ? {entry_id:agent.entry_id, subentry_id:agent.subentry_id} : {},
    isUnchanged:() => owner.isCurrent() && getToolYamlEditor(panel).getYaml() === yaml,
  };
}

export function acknowledgeToolEditor(panel, operation, saved, previous) {
  const tools = saved?.config?.functions || saved?.functions;
  if (!operation.isCurrent() || saved?.valid === false || saved?.revision == null || !Array.isArray(tools)) return operation;
  // The acknowledged text is now saved even when a newer read prevents this
  // editor from adopting its revision. Reverting to the earlier text is an
  // unsaved edit, not a clean draft that may be discarded on navigation.
  panel._toolInitialYaml = operation.yaml;
  // Only this editor's own commit may advance its authoring revision. Another
  // acknowledged/read baseline requires the existing conflict/review path.
  if (previous.config !== operation.baselineConfig
      || ![operation.revision, saved.revision].includes(previous.revision)) return operation;
  const candidates = tools.filter(tool => !operation.otherNames.has(tool.spec?.name));
  if (candidates.length !== 1 || saved.revision == null) return operation;
  const tool = candidates[0];
  panel._toolOriginalName = tool.spec.name;
  panel._toolIndex = tools.findIndex(item => item.spec?.name === tool.spec.name);
  panel._toolRevision = saved.revision;
  panel._toolReplaceableYaml = null;
  // A repaired definition is now a normal persisted tool. Its newer draft must
  // update that tool, rather than add a duplicate or call a finished repair.
  panel._repairToolIndex = null;
  panel._repairToolLoad = null;
  panel._repairToolRevision = null;
  panel._toolEditorLoad ||= {};
  return captureToolEditorOperation(panel);
}
