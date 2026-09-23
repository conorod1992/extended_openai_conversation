export const isHALlmTool = (tool) => tool?.function?.type === "ha_llm";
export const haToolName = (tool) => isHALlmTool(tool) ? tool.function.tool_name : tool?.spec?.name;
export const toolDescription = (panel, tool) => isHALlmTool(tool)
  ? `${tool.function.source_id} · ${tool.function.api_id} · ${panel._haCatalogAgent === panel._agentId ? panel._haCatalog?.saved?.[tool.spec.name]?.description || "Home Assistant LLM Tool" : "Home Assistant LLM Tool"}`
  : tool?.spec?.description || "No description";

export function filterHATools(tools, query = "", sources = []) {
  const selected = new Set(sources);
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return tools.filter(tool => (!selected.size || selected.has(tool.source)) &&
    terms.every(term => `${tool.name} ${tool.description} ${tool.source}`.toLocaleLowerCase().includes(term)));
}

export function renderHAToolCard(panel, tool, index, assignment = "") {
  const info = panel._haCatalogAgent === panel._agentId ? panel._haCatalog?.saved?.[tool.spec.name] : undefined;
  const name = haToolName(tool);
  const source = info?.source || `${tool.function.source_id} · ${tool.function.api_id}`;
  const enabled = tool.enabled !== false;
  return `<article class="list-card tool-card ${enabled ? "" : "is-disabled"}" data-tool-key="${panel._e(tool.spec.name)}" data-tool-index="${index}" data-tool-search="${panel._e(`${name} ${source} ${info?.description || ""} ${enabled ? "enabled" : "disabled"} ${info?.available === false ? "unavailable" : ""}`.toLowerCase())}">
    <div class="card-main"><div class="tool-title"><h4>${panel._e(name)}</h4><span class="type-badge">HA LLM Tool</span>${info?.available === false ? '<span class="disabled-badge">Unavailable</span>' : ""}</div>
    <p>${panel._e(source)}</p><p class="description">${panel._e(info?.description || "Live capability supplied by Home Assistant or an installed service.")}</p>${assignment}</div>
    <div class="actions tool-card-actions"><label class="tool-enabled-control"><span>Enabled</span><span class="switch-control"><input class="tool-enabled" data-index="${index}" type="checkbox" role="switch" aria-label="Enable ${panel._e(name)}" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></span></label>
    <button type="button" class="danger delete-tool" data-index="${index}">Remove</button></div></article>`;
}
