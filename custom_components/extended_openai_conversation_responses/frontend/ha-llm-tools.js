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

export function renderHAToolCard(panel, tool, index) {
  const info = panel._haCatalogAgent === panel._agentId ? panel._haCatalog?.saved?.[tool.spec.name] : undefined;
  const name = haToolName(tool);
  const source = info?.source || `${tool.function.source_id} · ${tool.function.api_id}`;
  const enabled = tool.enabled !== false;
  return `<article class="list-card tool-card ${enabled ? "" : "is-disabled"}" data-tool-index="${index}" data-tool-search="${panel._e(`${name} ${source} ${info?.description || ""} ${enabled ? "enabled" : "disabled"} ${info?.available === false ? "unavailable" : ""}`.toLowerCase())}">
    <div class="card-main"><div class="tool-title"><h4>${panel._e(name)}</h4><span class="type-badge">HA LLM Tool</span>${info?.available === false ? '<span class="disabled-badge">Unavailable</span>' : ""}</div>
    <p>${panel._e(source)}</p><p class="description">${panel._e(info?.description || "Live capability supplied by Home Assistant or an installed service.")}</p></div>
    <div class="actions tool-card-actions"><label class="tool-enabled-control"><span>Enabled</span><span class="switch-control"><input class="tool-enabled" data-index="${index}" type="checkbox" role="switch" aria-label="Enable ${panel._e(name)}" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></span></label>
    <button type="button" class="danger delete-tool" data-index="${index}">Remove</button></div></article>`;
}

export function bindHALlmTools(panel, synchronize) {
  if (!panel?._draft) return;
  const root = panel.shadowRoot;
  const agentId = panel._agentId;
  const load = async () => {
    const catalog = await panel._call("tools", "ha_catalog");
    if (panel._agentId !== agentId) throw new Error("The selected agent changed");
    panel._haCatalog = catalog;
    panel._haCatalogAgent = agentId;
    panel._haCatalogLoadedAt = Date.now();
    return catalog;
  };
  if ((panel._draft.functions || []).some(isHALlmTool) && (panel._haCatalogAgent !== agentId || Date.now() - (panel._haCatalogLoadedAt || 0) > 30000) && !panel._haCatalogLoading) {
    panel._haCatalogLoading = true;
    load().then(() => { if (!root.querySelector("dialog[open]")) panel._render(); }).catch(err => panel._toast(err.message, true)).finally(() => { panel._haCatalogLoading = false; });
  }
  root.querySelector("#refresh-ha-tools")?.addEventListener("click", async () => {
    try { await load(); panel._render(); } catch (err) { panel._toast(err.message, true); }
  });
  root.querySelector("#add-ha-tools")?.addEventListener("click", async () => {
    const dialog = document.createElement("dialog");
    dialog.className = "editor-dialog";
    dialog.setAttribute("aria-label", "Add Home Assistant LLM Tools");
    dialog.innerHTML = `<div class="dialog-header"><h2>Add LLM Tools</h2></div><div class="dialog-body">
      <p>These capabilities are supplied by Home Assistant or installed integrations/services. A source can be an integration's contribution or a complete LLM API, including an MCP server.</p>
      <p>Adding all saves the individual tools selected now. Future tools require explicit addition. Many schemas can increase input tokens; use Function Groups to load them when needed. HA tools are unavailable in Guest Mode.</p>
      <p role="status" data-status>Loading available tools…</p>
      <label>Search<input type="search" data-search></label><label>Sources<select multiple data-sources aria-label="Filter by sources"></select><small>Leave sources unselected to show all sources.</small></label>
      <label>Function Group<select data-group><option value="">Available on every request</option>${(panel._draft.function_groups || []).map(group => `<option value="${panel._e(group.id)}">${panel._e(group.name)}</option>`).join("")}</select></label>
      <button type="button" class="secondary" data-all>Select all shown</button><button type="button" class="secondary" data-clear>Clear selection</button><div data-tools></div></div>
      <div class="dialog-actions"><button type="button" class="secondary" data-cancel>Cancel</button><button type="button" data-add disabled>Add selected tools</button></div>`;
    root.append(dialog);
    dialog.showModal();
    dialog.addEventListener("close", () => dialog.remove());
    dialog.querySelector("[data-cancel]").onclick = () => dialog.close();
    try {
      const catalog = await load();
      if (!dialog.open) return;
      const selected = new Set();
      const available = catalog.tools || [];
      const status = dialog.querySelector("[data-status]");
      status.textContent = catalog.unavailable_sources?.length ? "Some sources are unavailable. Other tools can still be added." : "Preview uses your administrator context. Actual requests resolve tools with the caller's context and permissions.";
      dialog.querySelector("[data-sources]").innerHTML = [...new Set(available.map(tool => tool.source))].sort().map(source => `<option value="${panel._e(source)}">${panel._e(source)}</option>`).join("");
      const visible = () => filterHATools(available, dialog.querySelector("[data-search]").value, [...dialog.querySelector("[data-sources]").selectedOptions].map(option => option.value));
      const render = () => {
        dialog.querySelector("[data-tools]").innerHTML = visible().map(tool => {
          const index = available.indexOf(tool);
          return `<label class="group-function-choice"><input type="checkbox" data-index="${index}" ${selected.has(index) ? "checked" : ""} ${tool.already_added ? "disabled" : ""}><span><strong>${panel._e(tool.name)}${tool.already_added ? " · Already added" : ""}</strong><small>${panel._e(tool.source)} · ${panel._e(tool.description)}</small></span></label>`;
        }).join("") || "No matching tools are available in this context.";
        dialog.querySelector("[data-add]").disabled = selected.size === 0;
        dialog.querySelector("[data-add]").textContent = `Add ${selected.size} selected tools`;
      };
      dialog.querySelector("[data-search]").oninput = render;
      dialog.querySelector("[data-sources]").onchange = render;
      dialog.querySelector("[data-tools]").onchange = event => {
        const index = Number(event.target.dataset.index);
        if (event.target.checked) selected.add(index); else selected.delete(index);
        dialog.querySelector("[data-add]").disabled = selected.size === 0;
        dialog.querySelector("[data-add]").textContent = `Add ${selected.size} selected tools`;
      };
      dialog.querySelector("[data-all]").onclick = () => { for (const tool of visible()) if (!tool.already_added) selected.add(available.indexOf(tool)); render(); };
      dialog.querySelector("[data-clear]").onclick = () => { selected.clear(); render(); };
      dialog.querySelector("[data-add]").onclick = async () => {
        const button = dialog.querySelector("[data-add]");
        button.disabled = true;
        try {
          if (panel._agentId !== agentId) throw new Error("The selected agent changed");
          const result = await panel._call("tools", "ha_add", {tools: [...selected].map(index => available[index].reference), group_id: dialog.querySelector("[data-group]").value});
          if (panel._agentId !== agentId) return;
          synchronize(panel, result);
          for (const tool of available) if (selected.has(available.indexOf(tool))) tool.already_added = true;
          panel._haCatalogAgent = null;
          dialog.close();
          panel._toast("HA LLM Tool references added");
          panel._render();
        } catch (err) { status.textContent = err.message || String(err); button.disabled = false; }
      };
      render();
    } catch (err) { dialog.querySelector("[data-status]").textContent = err.message || String(err); }
  });
}
