import {apiPathSelectable, parameterControlState} from "./model-catalog.js";

export function reasoningEffortOptionsForResult(result = {}) {
  const values = result?.model_capabilities?.reasoning_effort_options;
  return (Array.isArray(values) ? values : []).map((value) => ({value, label: String(value).charAt(0).toUpperCase() + String(value).slice(1)}));
}


export function webSearchControlState(panel) {
  const config = panel?._draft || panel?._result?.config || {};
  const data = panel?._modelCatalogData;
  const metadata = data?.requested_model === String(config.chat_model || "")
    ? data.model_capabilities : panel?._result?.model_capabilities;
  if (!metadata?.evaluations) return {disabled:false, note:""};
  const effort = config.reasoning_effort ?? metadata.recommended_profile?.reasoning_effort ?? null;
  const functions = toolsRequired(config);
  const api = config.api_mode === "auto"
    ? metadata.auto_paths?.[`${effort}:${Number(functions)}:1`] : config.api_mode;
  const allowed = api && metadata.evaluations?.[api]?.[String(effort)]?.web_search;
  return allowed ? {disabled:false, note:""} : {
    disabled:true,
    note:`Web Search is unavailable for this API and reasoning effort${config.web_search ? "; the saved setting is retained. Choose a compatible effort or disable Web Search before sending." : "."}`,
  };
}

function toolsRequired(config) {
  return Boolean(config.functions?.length || config.function_groups?.length)
    || ["memory_enabled", "knowledge_enabled", "archive_enabled", "guest_mode_enabled"].some((key) => Boolean(config[key]));
}

// Presentation only: the catalogue and backend still own capability semantics.
// Both browser and standalone renderers consume the same decisions before HTML.
export function modelFieldPresentation(panel, key, value) {
  const config = panel?._draft || panel?._result?.config || {};
  const catalog = panel?._modelCatalogData;
  const data = catalog?.requested_model === String(config.chat_model || "") ? catalog : null;
  const metadata = data?.model_capabilities || data?.model_metadata || panel?._result?.model_capabilities || {};
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
    const functions = toolsRequired(config);
    const key = `${selected || "null"}:${Number(functions)}:${Number(Boolean(config.web_search))}`;
    const api = config.api_mode === "auto" ? metadata.auto_paths?.[key] : config.api_mode;
    const efforts = reasoning.by_api?.[api]?.efforts || reasoning.efforts || [];
    const options = efforts.map((effort) => ({value:effort, label:String(effort).replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase())}));
    if (selected && !efforts.includes(selected)) options.push({value:selected, label:`${selected} (inactive)`});
    return {visible:Boolean(reasoning.supported), value:selected, options};
  }
  if (key === "temperature" || key === "top_p") {
    const state = parameterControlState(metadata[key], config.reasoning_effort, config[key]);
    return {visible:state.visible, disabled:!state.enabled, note:state.enabled ? "" : state.reason};
  }
  if (key === "api_mode") {
    const needsTools = toolsRequired(config);
    const effort = config.reasoning_effort ?? metadata.recommended_profile?.reasoning_effort ?? null;
    const disabledOption = (api) => !apiPathSelectable(metadata, api, needsTools, effort, Boolean(config.web_search));
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
