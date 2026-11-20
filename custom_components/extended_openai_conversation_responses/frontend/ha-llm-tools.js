import {filterHATools, isHALlmTool} from "./ha-llm-tools-list.js";
import {captureAuthentication} from "./authentication-presentation.js";
import {captureConfigurationSubmission} from "./configuration-acknowledgement.js";
import {cacheTimestampIsFresh} from "./management-cache.js";
export {filterHATools, haToolName, isHALlmTool, renderHAToolCard, toolDescription} from "./ha-llm-tools-list.js";

const HA_TOOL_CATALOG_TTL_MS = 30_000;

export function bindHALlmTools(panel, acknowledge) {
  if (!panel?._draft) return;
  const root = panel.shadowRoot;
  const host = root.querySelector(".tools-surface");
  if (!host || host.__eocHaBound) return;
  host.__eocHaBound = true;
  const agentId = panel._agentId, entryId = panel._selectedAgent?.()?.entry_id;
  const ownsAgent = () => panel._agentId === agentId && panel._selectedAgent?.()?.entry_id === entryId;
  const load = () => {
    if (!ownsAgent()) return Promise.resolve(null);
    if (panel._haCatalogLoad?.current()) return panel._haCatalogLoad.promise;
    const authenticated = captureAuthentication(panel);
    const request = {current: () => ownsAgent() && authenticated(), promise: null};
    panel._haCatalogLoad = request;
    const ownsLoad = () => panel._haCatalogLoad === request && request.current();
    request.promise = (async () => {
      try {
        const catalog = await panel._call("tools", "ha_catalog");
        if (!ownsLoad()) return null;
        panel._haCatalog = catalog;
        panel._haCatalogAgent = agentId;
        panel._haCatalogLoadedAt = Date.now();
        return catalog;
      } catch (error) {
        if (ownsLoad()) throw error;
        return null;
      } finally {
        if (panel._haCatalogLoad === request) panel._haCatalogLoad = null;
      }
    })();
    return request.promise;
  };
  const refresh = async () => {
    const authenticated = captureAuthentication(panel);
    const visible = () => ownsAgent() && authenticated() && panel.isConnected !== false
      && root.querySelector(".tools-surface");
    try {
      if (await load() && visible() && !root.querySelector("dialog[open]")) panel._render();
    } catch (err) {
      if (visible()) panel._toast(err.message, true);
    }
  };
  const catalogFresh = panel._haCatalogAgent === agentId
    && cacheTimestampIsFresh(panel._haCatalogLoadedAt, HA_TOOL_CATALOG_TTL_MS);
  if ((panel._draft.functions || []).some(isHALlmTool) && !catalogFresh && !panel._haCatalogLoad?.current()) {
    void refresh();
  }
  root.querySelector("#refresh-ha-tools")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (button.disabled) return;
    button.disabled = true;
    try { await refresh(); }
    finally { if (button.isConnected) button.disabled = false; }
  });
  panel._openHALlmTools = async () => {
    const existing = root.querySelector('dialog[data-ha-llm-tools-dialog][open]');
    if (existing) { existing.focus?.(); return; }
    const dialog = document.createElement("dialog");
    dialog.id = "ha-llm-tools-dialog";
    dialog.className = "editor-dialog";
    dialog.dataset.haLlmToolsDialog = "";
    dialog.setAttribute("aria-label", "Add Home Assistant tools");
    dialog.innerHTML = `<div class="dialog-header"><h2>Add Home Assistant tools</h2></div><div class="dialog-body">
      <p>These tools are provided by Home Assistant or installed integrations. Choose the ones you want this assistant to use.</p>
      <p>Only the tools you select now are added. New tools must be added separately. Adding many tools can increase how much information is sent with each request. Function Groups can help by loading tools only when needed. Home Assistant tools are unavailable in Guest Mode.</p>
      <p role="status" data-status>Loading available tools…</p>
      <label>Preview device<select data-device><option value="">No device</option></select></label>
      <label>Search<input type="search" data-search></label><label>Sources<select multiple data-sources aria-label="Filter by sources"></select><small>Leave sources unselected to show all sources.</small></label>
      <label>Function Group<select data-group><option value="">Available on every request</option>${(panel._draft.function_groups || []).map(group => `<option value="${panel._e(group.id)}">${panel._e(group.name)}</option>`).join("")}</select></label>
      <button type="button" class="secondary" data-all>Select all shown</button><button type="button" class="secondary" data-clear>Clear selection</button><div data-tools></div></div>
      <div class="dialog-actions"><button type="button" class="secondary" data-cancel>Cancel</button><button type="button" data-add disabled>Add selected tools</button></div>`;
    root.append(dialog);
    dialog.showModal();
    dialog.addEventListener("close", () => dialog.remove());
    dialog.querySelector("[data-cancel]").onclick = () => dialog.close();
    const status = dialog.querySelector("[data-status]");
    try {
      const catalog = await load();
      if (!dialog.open) return;
      if (!catalog) throw new Error("The selected agent changed");
      // Identity survives filtering, catalogue reordering and panel recreation.
      const key = reference => JSON.stringify(reference, Object.keys(reference).sort());
      const selected = new Map();
      let available = [];
      let indexByTool = new Map();
      let availableKeys = new Set();
      let loading = false;
      let saving = false;
      const device = dialog.querySelector("[data-device]");
      const search = dialog.querySelector("[data-search]");
      const sources = dialog.querySelector("[data-sources]");
      const list = dialog.querySelector("[data-tools]");
      const add = dialog.querySelector("[data-add]");
      device.insertAdjacentHTML("beforeend", (catalog.preview_devices || []).map(({device_id, name}) =>
        `<option value="${panel._e(device_id)}">${panel._e(name)}</option>`).join(""));
      const visible = () => filterHATools(available, search.value, [...sources.selectedOptions].map(option => option.value));
      const group = dialog.querySelector("[data-group]");
      const updateAdd = () => {
        add.disabled = loading || saving || selected.size === 0
          || [...selected.keys()].some(id => !availableKeys.has(id))
          || Boolean(group.selectedOptions[0]?.disabled);
        add.textContent = `Add ${selected.size} selected tools`;
      };
      const render = () => {
        list.innerHTML = visible().map(tool => {
          const index = indexByTool.get(tool);
          return `<label class="group-function-choice"><input type="checkbox" data-index="${index}" ${selected.has(key(tool.reference)) ? "checked" : ""} ${tool.already_added ? "disabled" : ""}><span><strong>${panel._e(tool.name)}${tool.already_added ? " · Already added" : ""}</strong><small>${panel._e(tool.source)} · ${panel._e(tool.description)}</small></span></label>`;
        }).join("") || (loading ? "" : "No matching tools are available in this context.");
        updateAdd();
      };
      search.oninput = render;
      sources.onchange = render;
      list.onchange = event => {
        const index = Number(event.target.dataset.index);
        if (event.target.checked) selected.set(key(available[index].reference), available[index].reference); else selected.delete(key(available[index].reference));
        updateAdd();
      };
      const presentCatalog = result => {
        available = result.tools || [];
        indexByTool = new Map(available.map((tool, index) => [tool, index]));
        availableKeys = new Set(available.map(tool => key(tool.reference)));
        sources.innerHTML = [...new Set(available.map(tool => tool.source))].sort().map(source => `<option value="${panel._e(source)}">${panel._e(source)}</option>`).join("");
        status.textContent = result.unavailable_sources?.length
          ? "Some sources are unavailable. Other tools can still be added."
          : "Actual requests resolve tools with the caller's device and permissions.";
        if ([...selected.keys()].some(id => !availableKeys.has(id))) {
          status.textContent = "Some selected tools are unavailable on this device. Switch devices or clear the selection.";
        }
        render();
      };
      let deviceRequest = 0;
      const reloadDevice = async () => {
        const request = ++deviceRequest;
        const authenticated = captureAuthentication(panel);
        const current = () => request === deviceRequest && dialog.open && dialog.isConnected
          && ownsAgent() && authenticated();
        loading = true;
        available = [];
        availableKeys.clear();
        status.textContent = "Loading available tools…";
        render();
        try {
          const result = device.value
            ? await panel._call("tools", "ha_catalog", {preview_device_id: device.value})
            : await load();
          if (current() && result) {
            loading = false;
            presentCatalog(result);
          }
        } catch (err) {
          if (current()) {
            loading = false;
            status.textContent = err.message || String(err);
            render();
          }
        }
      };
      device.addEventListener("change", () => { void reloadDevice(); });
      dialog.querySelector("[data-all]").onclick = () => { for (const tool of visible()) if (!tool.already_added) selected.set(key(tool.reference), tool.reference); render(); };
      dialog.querySelector("[data-clear]").onclick = () => { selected.clear(); render(); };
      add.onclick = async () => {
        if (add.disabled) return;
        saving = true;
        updateAdd();
        device.disabled = true;
        try {
          if (!ownsAgent()) throw new Error("The selected agent changed");
          const submission = captureConfigurationSubmission(panel);
          const result = await panel._call("tools", "ha_add", {
            tools: [...selected.values()], group_id: group.value,
            ...(device.value ? {preview_device_id: device.value} : {}),
          });
          await acknowledge(result, submission, () => {
            if (result.ha_saved) {
              const latest = panel._haCatalogAgent === agentId ? panel._haCatalog : catalog;
              panel._haCatalog = {...latest, saved: {...latest?.saved, ...result.ha_saved}};
            }
            dialog.close();
            panel._render();
          });
        } catch (err) {
          status.textContent = err.message || String(err);
        } finally {
          saving = false;
          device.disabled = false;
          updateAdd();
        }
      };
      dialog.__eocReadDraft = () => ({tools:[...selected.values()], group:group.value, device:device.value});
      dialog.__eocRestoreDraft = draft => {
        selected.clear();
        for (const reference of draft.tools || []) selected.set(key(reference), reference);
        if (draft.group && ![...group.options].some(option => option.value === draft.group)) {
          const option = document.createElement("option");
          option.value = draft.group; option.textContent = "Unavailable selected group"; option.disabled = true; group.append(option);
        }
        group.value = draft.group || "";
        if (draft.device && [...device.options].some(option => option.value === draft.device)) {
          device.value = draft.device;
          void reloadDevice();
        } else {
          presentCatalog(catalog);
        }
      };
      group.addEventListener("change", render);
      panel._captureDialogBaseline?.(dialog);
      presentCatalog(catalog);
    } catch (err) { status.textContent = err.message || String(err); }
  };
  root.querySelector("#add-ha-tools")?.addEventListener("click", () => { void panel._openHALlmTools(); });
}
