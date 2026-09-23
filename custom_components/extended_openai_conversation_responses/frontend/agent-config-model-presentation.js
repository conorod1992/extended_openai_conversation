import {apiPathSelectable, parameterControlState} from "./model-catalog.js";

export function reasoningEffortOptionsForResult(result = {}) {
  const values = result?.model_capabilities?.reasoning_effort_options;
  return (Array.isArray(values) ? values : []).map((value) => ({value, label: String(value).charAt(0).toUpperCase() + String(value).slice(1)}));
}

function toolsRequired(config) {
  return Boolean(config.functions?.length || config.function_groups?.length)
    || ["memory_enabled", "knowledge_enabled", "archive_enabled", "guest_mode_enabled", "web_search"].some((key) => Boolean(config[key]));
}

// Presentation only: the catalogue and backend still own capability semantics.
// Both browser and standalone renderers consume the same decisions before HTML.
export function modelFieldPresentation(panel, key, value) {
  const config = panel?._draft || panel?._result?.config || {};
  const catalog = panel?._modelCatalogData;
  const data = catalog?.requested_model === String(config.chat_model || "") ? catalog : null;
  const metadata = data?.model_metadata || panel?._result?.model_capabilities || {};
  if (key === "chat_model" && data) {
    const note = metadata.status === "deprecated"
      ? `Deprecated model. It remains selected for this existing configuration and will not be changed automatically.${metadata.lifecycle_note ? ` ${metadata.lifecycle_note}` : ""}`
      : metadata.status === "unknown" && config.chat_model !== undefined && config.chat_model !== null && config.chat_model !== ""
        ? "Custom or unknown model. Extended OpenAI will use conservative capabilities until explicit metadata is available."
        : "";
    return {models:data.catalog_models || [], note, noteClass:"model-lifecycle-note"};
  }
  if (key === "max_tokens") {
    const max = Number(metadata.limits?.max_output_tokens);
    return Number.isInteger(max) && max > 0 ? {max, note:`Selected model maximum output: ${max.toLocaleString()} tokens.`} : {};
  }
  if (key === "reasoning_effort") {
    const reasoning = metadata.reasoning || {};
    const selected = String(config.reasoning_effort ?? metadata.recommended_profile?.reasoning_effort ?? "");
    return {visible:Boolean(reasoning.supported), value:selected, options:(reasoning.efforts || []).map((effort) => ({value:effort, label:String(effort).replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase())}))};
  }
  if (key === "temperature" || key === "top_p") {
    const state = parameterControlState(metadata[key], config.reasoning_effort, config[key]);
    return {visible:state.visible, disabled:!state.enabled, note:state.enabled ? "" : state.reason};
  }
  if (key === "api_mode") {
    const needsTools = toolsRequired(config);
    const effort = config.reasoning_effort ?? metadata.recommended_profile?.reasoning_effort ?? null;
    const disabledOption = (api) => !apiPathSelectable(metadata, api, needsTools, effort);
    // A select with no matching configured value selects its first option.
    const options = panel?._result?.options?.api_mode || [];
    const selected = options.some((item) => item.value === value) ? value : options[0]?.value;
    const note = selected !== undefined && disabledOption(selected)
      ? needsTools
        ? "This API path cannot be used with the currently enabled tools for this model. Choose Auto or a supported path before saving."
        : "This API path is not supported by the selected model."
      : "";
    return {disabledOption, note};
  }
  return {};
}

export function modelFieldNotes(panel, presentation) {
  const {note, noteClass = "capability-note", models} = presentation;
  const list = models ? `<datalist id="extended-openai-model-catalog">${models.map((item) => `<option value="${panel._e(item.id)}" label="${panel._e(`${item.display_name || item.id}${item.status === "deprecated" ? " — Deprecated" : ""}`)}"></option>`).join("")}</datalist>` : "";
  return `${list}${note ? `<small class="${noteClass}">${panel._e(note)}</small>` : ""}`;
}
