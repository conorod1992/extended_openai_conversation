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
