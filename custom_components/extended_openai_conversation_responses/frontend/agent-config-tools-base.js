import {searchTokens, matchesFunctionSearchTokens, cachedFunctionSearchTokens} from "./function-search.js";
export {matchesFunctionSearch} from "./function-search.js";
import {functionAddMenu, bindFunctionAddMenu} from "./function-add-menu.js";
import {bindOwnedEvent} from "./management-owned-events.js";
import {dialogState, setDialogBaseline} from "./management-dialogs.js";
import {COMPACT_CHOICE_STYLE} from "./compact-choice-styles.js";
const ROUTE_STYLE = `.function-groups-help{margin-bottom:18px}.tool-search{display:block;margin-bottom:18px}.function-groups{display:grid;gap:18px}.function-group-card{border:1px solid var(--divider-color);border-radius:12px;padding:18px;background:var(--card-background-color)}.function-group-heading{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:20px}.function-group-heading>.function-group-actions{display:grid;grid-template-columns:auto auto;gap:10px;align-items:center;justify-content:end}.function-group-actions>.group-enabled-control{grid-column:1/-1;justify-self:end}.function-group-heading h3,.tool-title h4{margin:0}.function-group-heading p{margin:7px 0;color:var(--secondary-text-color);line-height:1.45}.function-group-heading code{font-size:12px;color:var(--secondary-text-color)}.availability-badge,.function-count{display:inline-flex;padding:4px 9px;border-radius:999px;background:color-mix(in srgb,var(--success-color,#0f9d58) 15%,transparent);color:var(--eoc-success-text);font-size:11px;font-weight:600}.availability-badge.on-demand{background:color-mix(in srgb,var(--primary-color) 14%,transparent);color:var(--eoc-accent-text)}.availability-badge.group-disabled-badge{background:var(--secondary-background-color);color:var(--secondary-text-color)}.function-count{background:var(--secondary-background-color);color:var(--secondary-text-color)}.function-group-card details{margin-top:14px;padding-top:10px}.function-group-card summary{min-height:40px}.function-group-card .tool-list{padding-top:10px}.group-dialog{width:min(820px,calc(100vw - 28px))}.group-dialog-body{max-height:calc(100vh - 170px)}.group-functions-fieldset{margin:0;padding-top:18px}.${COMPACT_CHOICE_STYLE}@media(max-width:679px){.group-dialog{width:calc(100vw - 12px)}.function-group-heading{grid-template-columns:minmax(0,1fr)}.function-group-heading>.function-group-actions{justify-content:start}.function-group-actions>.group-enabled-control{justify-self:start}.group-function-choices{max-height:34vh}}`;
function ensureRouteStyle(panel) {
  if (typeof document === "undefined") return;
  if (panel.shadowRoot.querySelector("style[data-eoc-feature-style=\"function-groups\"]")) return;
  const style = document.createElement("style");
  style.dataset.eocFeatureStyle = "function-groups";
  style.textContent = ROUTE_STYLE;
  panel.shadowRoot.append(style);
}

import {adoptKeyedElements, elementFromMarkup, keyedElement, placeChildren, pruneKeys, setAttribute, setText} from "./keyed-collection.js";
import {captureConfigurationOwner, captureConfigurationSubmission, configurationSubmissionIsCurrent, reconcileConfigurationAcknowledgement, completeConfigurationAcknowledgement, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";
import {same} from "./unsaved-state.js";
import { haToolName, isHALlmTool, renderHAToolCard, toolDescription } from "./ha-llm-tools-list.js";
import { bindHelp, helpButton, helpPopover } from "./agent-config-help.js";
import {getToolYamlEditor, captureToolEditorOwner, captureToolEditorOperation, acknowledgeToolEditor} from "./tool-yaml-editor-adapter.js";

const clone = (value) => JSON.parse(JSON.stringify(value));
export const functionGroupIdFromName = (name) => String(name || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 64);
export const isFunctionGroupEnabled = (group = {}) => group?.enabled !== false;
export const isFunctionToolEnabled = (tool) => tool?.enabled !== false;

export function functionToolCountLabel(tools = []) {
  const enabled = tools.filter(isFunctionToolEnabled).length;
  const disabled = tools.length - enabled;
  if (!disabled) return `${enabled} ${enabled === 1 ? "function" : "functions"}`;
  return `${enabled} enabled · ${disabled} disabled`;
}

export const canReplaceToolYamlWithoutConfirmation = (current, replaceable) => !String(current || "").trim() || current === replaceable;

export function deleteFunctionGroup(config, groupId) {
  const result = clone(config);
  result.function_groups = (result.function_groups || []).filter((group) => group.id !== groupId);
  return result;
}

function indexFunctionToolGroups(config) {
  const tools = config.functions || [];
  const groups = config.function_groups || [];
  const indexedGroups = groups.map((group) => ({...group, tools: []}));
  const groupIndexesByTool = new Map();
  const membership = new Map();

  groups.forEach((group, groupIndex) => {
    for (const name of group.functions || []) {
      const indexes = groupIndexesByTool.get(name);
      if (indexes) indexes.push(groupIndex);
      else groupIndexesByTool.set(name, [groupIndex]);
      membership.set(name, group);
    }
  });

  const alwaysAvailable = [];
  for (const tool of tools) {
    const name = tool.spec?.name;
    const groupIndexes = groupIndexesByTool.get(name);
    if (!groupIndexes) {
      alwaysAvailable.push(tool);
      continue;
    }
    for (const groupIndex of groupIndexes) indexedGroups[groupIndex].tools.push(tool);
  }

  return {alwaysAvailable, groups: indexedGroups, membership};
}

export function categorizeFunctionTools(config) {
  const {alwaysAvailable, groups} = indexFunctionToolGroups(config);
  return {alwaysAvailable, groups};
}

const FUNCTION_GROUP_ASSIGNMENT_STYLE = `
  .function-group-assignment-control {
    display: inline-flex;
    align-items: center;
    gap: 0;
    width: fit-content;
    max-width: min(300px, 100%);
    min-height: 32px;
    margin-top: 9px;
    border: 1px solid var(--divider-color, rgba(127, 127, 127, .35));
    border-radius: 999px;
    background: var(--card-background-color, transparent);
    color: var(--primary-text-color);
    transition: border-color 120ms ease, background 120ms ease;
  }
  .function-group-assignment-control:hover,
  .function-group-assignment-control:focus-within {
    border-color: var(--primary-color);
    background: var(--secondary-background-color, transparent);
  }
  .function-group-assignment-control.is-disabled-group { opacity: .72; }
  .function-group-assignment-icon {
    --mdc-icon-size: 16px;
    display: inline-flex;
    align-items: center;
    flex: 0 0 auto;
    padding-inline-start: 10px;
    color: var(--secondary-text-color);
  }
  .function-group-assignment {
    width: auto;
    min-width: 0;
    max-width: 250px;
    min-height: 30px;
    border: 0;
    border-radius: 999px;
    outline: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 13px;
    font-weight: 500;
    padding: 4px 10px 4px 6px;
    cursor: pointer;
  }
  .function-group-assignment:disabled { cursor: progress; }
  .function-group-card[data-group-id],
  .function-group-card.always-card {
    background: var(--secondary-background-color, var(--card-background-color));
    background: color-mix(in srgb, var(--primary-color) 5%, var(--card-background-color));
    box-shadow: inset 3px 0 0 color-mix(in srgb, var(--primary-color) 28%, transparent);
  }
  .function-group-card.function-repair-attention {
    background: color-mix(in srgb, var(--warning-color, #ff9800) 8%, var(--card-background-color));
    box-shadow: inset 3px 0 0 color-mix(in srgb, var(--warning-color, #ff9800) 45%, transparent);
  }
  .function-group-card > details .tool-card {
    background: var(--card-background-color);
  }
  .function-group-card[data-group-id] + .function-group-card[data-group-id] {
    margin-top: 16px;
  }
  .group-enabled-control { flex: 0 0 auto; }
  @media (max-width: 700px) {
    .function-group-assignment-control { max-width: 100%; }
    .function-group-assignment { max-width: 210px; }
  }
`;

function assignmentOptions(panel, groups, currentId) {
  return [
    `<option value="" ${currentId ? "" : "selected"}>Available on every request</option>`,
    ...(groups || []).map((group) => {
      const disabled = group.enabled === false ? " (disabled)" : "";
      return `<option value="${panel._e(group.id)}" ${group.id === currentId ? "selected" : ""}>${panel._e(group.name)}${disabled}</option>`;
    }),
  ].join("");
}

function functionGroupAssignment(panel, tool, index) {
  const name = tool?.spec?.name;
  if (!name) return "";
  const groups = (panel._draft || panel._result?.config || {}).function_groups || [];
  const current = groups.find((group) => (group.functions || []).includes(name));
  const title = current?.enabled === false
    ? `Function group: ${current.name}. This group is currently disabled.`
    : `Function group: ${current?.name || "Available on every request"}`;
  return `<label class="function-group-assignment-control${current?.enabled === false ? " is-disabled-group" : ""}" title="${panel._e(title)}"><span class="sr-only">Function group for ${panel._e(name)}</span><ha-icon class="function-group-assignment-icon" icon="mdi:folder-outline" aria-hidden="true"></ha-icon><select class="function-group-assignment" data-index="${index}" aria-label="Function group for ${panel._e(name)}">${assignmentOptions(panel, groups, current?.id || "")}</select></label>`;
}

function functionToolCard(panel, tool, allTools) {
  const index = allTools.indexOf(tool);
  const assignment = functionGroupAssignment(panel, tool, index);
  if (isHALlmTool(tool)) return renderHAToolCard(panel, tool, index, assignment);
  const enabled = isFunctionToolEnabled(tool);
  return `<article class="list-card tool-card ${enabled ? "" : "is-disabled"}" data-tool-key="${panel._e(tool.spec?.name || "")}" data-tool-index="${index}" data-tool-search="${panel._e(`${tool.spec?.name || ""} ${tool.spec?.description || ""} ${tool.function?.type || ""} ${enabled ? "enabled" : "disabled"}`.toLowerCase())}"><div class="card-main"><div class="tool-title"><h4>${panel._e(tool.spec?.name||"Unnamed tool")}</h4><span class="type-badge">${panel._e(tool.function?.type||"Unknown type")}</span>${enabled ? "" : '<span class="disabled-badge">Disabled</span>'}</div><p class="description">${panel._e(tool.spec?.description||"No description")}</p>${assignment}</div><div class="actions tool-card-actions"><label class="tool-enabled-control"><span>Enabled</span><span class="switch-control"><input class="tool-enabled" data-index="${index}" type="checkbox" role="switch" aria-label="Enable ${panel._e(tool.spec?.name||"Function Tool")}" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></span></label><button type="button" class="secondary edit-tool" data-index="${index}">Edit</button><button type="button" class="secondary duplicate-tool" data-index="${index}">Duplicate</button><button type="button" class="danger delete-tool" data-index="${index}">Delete</button></div></article>`;
}

function functionGroupCard(panel, group, tools) {
  const enabled = isFunctionGroupEnabled(group);
  const mode = group.loading_mode === "on_demand" ? "Load when needed" : "Always available";
  const members = group.tools.map((tool) => functionToolCard(panel,tool,tools)).join("");
  return `<article class="function-group-card ${enabled ? "" : "is-disabled"}" data-group-id="${panel._e(group.id)}" data-group-search="${panel._e(`${group.name} ${group.description} ${group.id}`.toLowerCase())}"><div class="function-group-heading"><div><div class="tool-title"><h3>${panel._e(group.name)}</h3><span class="availability-badge ${group.loading_mode === "on_demand" ? "on-demand" : ""}">${mode}</span><span class="function-count">${functionToolCountLabel(group.tools)}</span>${enabled ? "" : `<span class="availability-badge group-disabled-badge">Disabled</span>`}</div><p>${panel._e(group.description)}</p></div><div class="actions function-group-actions"><label class="tool-enabled-control group-enabled-control" title="Disable the group without changing the enabled state of its member Function Tools"><span>Enabled</span><span class="switch-control"><input type="checkbox" role="switch" class="group-enabled" data-group-id="${panel._e(group.id)}" aria-label="Enable Function Group ${panel._e(group.name)}" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></span></label><button type="button" class="secondary edit-group" data-group-id="${panel._e(group.id)}" ${enabled ? "" : "disabled"} aria-label="Edit Function Group ${panel._e(group.name)}" title="${enabled ? `Edit Function Group ${panel._e(group.name)}` : "Enable this Function Group before editing it"}">Edit</button><button type="button" class="danger delete-group" data-group-id="${panel._e(group.id)}" aria-label="Delete Function Group ${panel._e(group.name)}" title="Delete Function Group ${panel._e(group.name)}">Delete</button></div></div><details class="eoc-details-base"><summary>Show member functions</summary><div class="list tool-list">${members || panel._empty("This group has no functions yet. Edit it to choose functions.")}</div></details></article>`;
}

export function renderTools(panel, {repairCards = ""} = {}) {
  const config = panel._draft || panel._result?.config || {};
  const tools = config.functions || [];
  const enabledCount = tools.filter(isFunctionToolEnabled).length;
  const categories = categorizeFunctionTools(config);
  const ungrouped = categories.alwaysAvailable.map((tool)=>functionToolCard(panel,tool,tools)).join("");
  return `<style data-function-group-assignment>${FUNCTION_GROUP_ASSIGNMENT_STYLE}</style><section class="page-intro"><h1>Functions</h1><p>Manage Function Tools and groups that extend what this assistant can do.</p></section><section class="content-card tools-surface"><div class="section-heading"><div><span class="setting-label-row"><h2>Function Tools & Groups</h2>${helpButton(panel,"function_tools")}</span><p>Function Tools give the assistant actions beyond normal Home Assistant access. Changes to functions and groups save immediately. <button type="button" class="guide-topic-link guide-link" data-guide-topic="functions">Learn more</button></p><small data-function-totals>${enabledCount} enabled · ${tools.length - enabledCount} disabled · ${categories.groups.length} groups</small></div>${functionAddMenu()}</div><div class="notice function-groups-help"><strong>Function group availability</strong><p>Always available groups send full tool schemas with the initial request. Load when needed groups initially send only their name and description, then load full schemas when requested. On-demand loading reduces input-token usage but may add one model round-trip the first time a group is used.</p></div><label class="tool-search"><span class="sr-only">Search functions and groups</span><input id="tool-search" type="search" placeholder="Search functions and groups..." aria-label="Search functions and groups"></label><div class="function-groups">${repairCards}<article class="function-group-card always-card" data-group-search="always available ungrouped general"><div class="function-group-heading"><div><div class="tool-title"><h3>Available on every request</h3><span class="function-count">${functionToolCountLabel(categories.alwaysAvailable)}</span></div><p>These ungrouped functions send their full instructions with every request, so the assistant can use them immediately.</p></div></div><details class="eoc-details-base" open><summary>Show included functions</summary><div class="list tool-list">${ungrouped || panel._empty("No ungrouped functions.")}</div></details></article>${categories.groups.map((group)=>functionGroupCard(panel,group,tools)).join("")||panel._empty("No groups yet. Existing functions remain available on every request until you create one.")}</div><div id="function-search-empty" class="empty-state" role="status" hidden><h3>No functions or groups match your search</h3><button type="button" class="secondary" id="clear-tool-search">Clear search</button></div><div class="section-actions tools-actions"><button type="button" class="secondary" id="refresh-ha-tools" ${tools.some(isHALlmTool)?"":"hidden"}>Refresh HA tool availability</button><button type="button" class="secondary" id="validate-tools">Check tool configuration</button><span id="tool-status" class="validation" aria-live="polite"></span></div></section>`;
}

// Only DOM references and presentation signatures are retained here. Mutations
// still synchronize _draft/_configData from the backend before reconciling.
const toolCollections = new WeakMap();
function applyFunctionSearch(panel) {
  const root = panel.shadowRoot;
  const query = root.querySelector("#tool-search")?.value || "";
  const hasQuery = Boolean(query.trim());
  const queryTokens = hasQuery ? searchTokens(query) : [];
  let visible = 0;
  root.querySelectorAll(".function-group-card").forEach(card => {
    const groupMatch = hasQuery && matchesFunctionSearchTokens(
      queryTokens,
      cachedFunctionSearchTokens(card, card.dataset.groupSearch),
    );
    let toolMatch = false;
    card.querySelectorAll(".tool-card").forEach(tool => {
      const matches = !hasQuery || groupMatch || matchesFunctionSearchTokens(
        queryTokens,
        cachedFunctionSearchTokens(tool, tool.dataset.toolSearch),
      );
      if (tool.hidden === matches) tool.hidden = !matches;
      toolMatch ||= matches;
    });
    const hidden = hasQuery && !groupMatch && !toolMatch;
    if (card.hidden !== hidden) card.hidden = hidden;
    if (!hidden) visible++;
    if (hasQuery && toolMatch) {
      const details = card.querySelector("details");
      if (details && !details.open) details.open = true;
    }
  });
  const empty = root.querySelector("#function-search-empty");
  if (empty) empty.hidden = !hasQuery || visible > 0;
  const noGroups = toolCollections.get(root.querySelector(".tools-surface"))?.noGroups;
  if (noGroups) noGroups.hidden = hasQuery;
}

function prepareToolsCollection(panel) {
  const host = panel.shadowRoot.querySelector(".tools-surface");
  if (!host || toolCollections.has(host)) return;
  toolCollections.set(host, {
    tools: adoptKeyedElements(host, "[data-tool-key]", "toolKey"),
    groups: adoptKeyedElements(host, ".function-group-card[data-group-id]", "groupId"),
    always: {node: host.querySelector(".always-card")},
    repair: host.querySelector(".function-repair-attention"),
    repairSignature: JSON.stringify(panel._result?.function_repair),
    noGroups: [...host.querySelector(".function-groups").children].find(node => node.classList.contains("empty")),
  });
  reconcileTools(panel);
}

export function reconcileTools(panel, {repairCards} = {}) {
  const host = panel.shadowRoot.querySelector(".tools-surface");
  const state = toolCollections.get(host);
  if (!state) return false;
  const config = panel._draft || panel._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];
  const categories = indexFunctionToolGroups(config);
  const membership = categories.membership;
  const choices = JSON.stringify(groups.map(({id, name, enabled}) => ({id, name, enabled})));
  const toolNodes = new Map();
  tools.forEach((tool, index) => {
    const name = tool.spec?.name || "";
    const info = isHALlmTool(tool) && panel._haCatalogAgent === panel._agentId ? panel._haCatalog?.saved?.[name] : null;
    const record = keyedElement(state.tools, name, JSON.stringify([tool, info]), () => functionToolCard(panel, tool, tools));
    const card = record.node;
    record.indices ||= [card, ...card.querySelectorAll("[data-index]")];
    for (const node of record.indices) setAttribute(node, node === card ? "data-tool-index" : "data-index", index);
    const current = membership.get(name);
    const assignment = `${choices}|${current?.id || ""}`;
    if (record.assignment !== undefined && record.assignment !== assignment) {
      const old = card.querySelector(".function-group-assignment-control");
      old?.replaceWith(elementFromMarkup(functionGroupAssignment(panel, tool, index)));
      record.indices = null;
    }
    record.assignment = assignment;
    toolNodes.set(name, card);
  });

  function updateMembers(record, members, emptyText) {
    record.list ||= record.node.querySelector(".tool-list");
    record.count ||= record.node.querySelector(".function-count");
    setText(record.count, functionToolCountLabel(members));
    if (!members.length) record.empty ||= record.list.querySelector(".empty") || elementFromMarkup(panel._empty(emptyText));
    placeChildren(record.list, members.length ? members.map(tool => toolNodes.get(tool.spec.name)) : [record.empty]);
  }
  updateMembers(state.always, categories.alwaysAvailable, "No ungrouped functions.");
  const groupNodes = categories.groups.map(group => {
    let record = state.groups.get(group.id);
    if (!record) {
      record = {node: elementFromMarkup(functionGroupCard(panel, {...group, tools: []}, tools))};
      state.groups.set(group.id, record);
    }
    const {functions, tools: members, ...metadata} = group;
    const signature = JSON.stringify(metadata);
    if (record.signature !== undefined && record.signature !== signature) {
      const fresh = elementFromMarkup(functionGroupCard(panel, {...group, tools: []}, tools));
      record.node.querySelector(".function-group-heading").replaceWith(fresh.querySelector(".function-group-heading"));
      record.node.className = fresh.className;
      setAttribute(record.node, "data-group-search", fresh.dataset.groupSearch);
      record.count = null;
    }
    record.signature = signature;
    updateMembers(record, members, "This group has no functions yet. Edit it to choose functions.");
    return record.node;
  });
  if (repairCards !== undefined && state.repairMarkup !== repairCards) {
    // Repair cards have a separate owner; only rebuild that small section when
    // its rendered validation/quarantine information actually changes.
    if (state.repairSignature !== JSON.stringify(panel._result?.function_repair) || !state.repair) state.repair = repairCards ? elementFromMarkup(repairCards) : null;
    state.repairSignature = JSON.stringify(panel._result?.function_repair);
    state.repairMarkup = repairCards;
  }
  if (!groupNodes.length) state.noGroups ||= elementFromMarkup(panel._empty("No groups yet. Existing functions remain available on every request until you create one."));
  placeChildren(host.querySelector(".function-groups"), [
    ...(state.repair ? [state.repair] : []), state.always.node,
    ...(groupNodes.length ? groupNodes : [state.noGroups]),
  ]);
  pruneKeys(state.tools, new Set(tools.map(tool => tool.spec?.name || "")));
  pruneKeys(state.groups, new Set(groups.map(group => group.id)));
  const enabled = tools.filter(isFunctionToolEnabled).length;
  setText(host.querySelector("[data-function-totals]"), `${enabled} enabled · ${tools.length - enabled} disabled · ${groups.length} groups`);
  const refresh = host.querySelector("#refresh-ha-tools");
  const hideRefresh = !tools.some(isHALlmTool);
  if (refresh.hidden !== hideRefresh) refresh.hidden = hideRefresh;
  applyFunctionSearch(panel);
  return true;
}

function collectionToolIndex(panel, control) {
  const name = control.closest("[data-tool-key]")?.dataset.toolKey;
  return (panel._draft.functions || []).findIndex(tool => tool.spec?.name === name);
}

function queueFunctionGroupMutation(panel, operation) {
  const owner = captureConfigurationOwner(panel);
  const previous = panel._eocFunctionGroupUiTail || Promise.resolve();
  const pending = previous.catch(() => {}).then(async () => {
    const backendTail = panel._eocFunctionMutationTail;
    if (backendTail) await backendTail.catch(() => {});
    if (!configurationSubmissionIsCurrent(panel, owner)) throw new Error("The selected assistant changed");
    return operation();
  });
  const tracked = pending.finally(() => {
    if (panel._eocFunctionGroupUiTail === tracked) panel._eocFunctionGroupUiTail = null;
  });
  panel._eocFunctionGroupUiTail = tracked;
  return tracked;
}

function bindToolCollection(panel) {
  const root = panel.shadowRoot;
  const host = root.querySelector(".tools-surface");
  if (!host || host.__eocToolsBound) return;
  host.__eocToolsBound = true;
  bindFunctionAddMenu(host);
  host.addEventListener("input", event => {
    if (event.target.id === "tool-search") applyFunctionSearch(panel);
  });
  host.addEventListener("click", async event => {
    const button = event.target.closest?.("button");
    if (!button || button.disabled) return;
    if (button.id === "clear-tool-search") {
      const search = root.querySelector("#tool-search");
      search.value = "";
      applyFunctionSearch(panel);
      search.focus();
      return;
    }
    if (button.id === "add-tool") return openTool(panel);
    if (button.id === "add-group") return openFunctionGroup(panel);
    if (button.matches(".edit-group")) return openFunctionGroup(panel, button.dataset.groupId, Boolean(button.closest(".group-repair-entry")));
    if (button.matches(".delete-group")) {
      const submission = captureConfigurationSubmission(panel);
      const group = editableFunctionGroup(panel, button.dataset.groupId);
      panel._eocDeletingFunctionGroups ||= new Set();
      if (!group || panel._eocDeletingFunctionGroups.has(group.id)
        || !await panel._confirm("Delete function group?", `The group “${group.name}” will be removed. Its functions will not be deleted; they will move to Always available.`, "Delete group")) return;
      if (!configurationSubmissionIsCurrent(panel, submission) || panel._eocDeletingFunctionGroups.has(group.id)) return;
      panel._eocDeletingFunctionGroups.add(group.id);
      const idleLabel = button.textContent;
      button.disabled = true;
      button.textContent = "Deleting…";
      button.setAttribute("aria-busy", "true");
      try {
        const result = await queueFunctionGroupMutation(panel, () => panel._call("tools", "delete_group", {group_id: group.id, confirm: true}));
        await completeFunctionMutation(panel, result, submission, "Function group deleted");
      } catch (err) {
        if (!configurationSubmissionIsCurrent(panel, submission)) return;
        button.disabled = false;
        button.textContent = idleLabel;
        button.removeAttribute("aria-busy");
        panel._toast(`Unable to delete group: ${err.message || String(err)}`, true);
      } finally {
        panel._eocDeletingFunctionGroups.delete(group.id);
      }
      return;
    }
    const index = collectionToolIndex(panel, button);
    const tool = panel._draft.functions[index];
    if (!tool) return;
    if (button.matches(".edit-tool")) return openTool(panel, index);
    if (button.matches(".duplicate-tool")) {
      const copy = clone(tool);
      const names = new Set(panel._draft.functions.map(item => item.spec?.name));
      const original = copy.spec.name;
      const duplicateName = (suffix) => `${original.slice(0, 64 - suffix.length)}${suffix}`;
      copy.spec.name = duplicateName("_copy");
      let suffix = 2;
      while (names.has(copy.spec.name)) copy.spec.name = duplicateName(`_copy_${suffix++}`);
      return openTool(panel, null, copy);
    }
    if (button.matches(".delete-tool")) {
      const submission = captureConfigurationSubmission(panel);
      const functionName = tool.spec?.name;
      panel._eocDeletingFunctions ||= new Set();
      if (!functionName || panel._eocDeletingFunctions.has(functionName)) return;
      if (!await panel._confirm(isHALlmTool(tool) ? "Remove Home Assistant Tool?" : "Delete function tool?", isHALlmTool(tool)
        ? `Remove “${haToolName(tool)}” from this agent and its groups? The underlying Home Assistant capability remains unchanged. Saved dependencies must be removed first.`
        : `The Function Tool “${tool.spec?.name || "Unnamed"}” will be deleted and removed from any Function Group. Deletion is refused while Request Rules or Guest Mode still reference it.`, isHALlmTool(tool) ? "Remove tool" : "Delete function")) return;
      if (!configurationSubmissionIsCurrent(panel, submission) || panel._eocDeletingFunctions.has(functionName)) return;
      panel._eocDeletingFunctions.add(functionName);
      const idleLabel = button.textContent;
      button.disabled = true;
      button.textContent = "Deleting…";
      button.setAttribute("aria-busy", "true");
      try {
        const result = await panel._call("tools", "delete", {name: functionName, confirm: true});
        const syncStarted = globalThis.performance?.now?.() ?? Date.now();
        await completeFunctionMutation(panel, result, submission, "Function deleted", () => {
          const syncCompleted = globalThis.performance?.now?.() ?? Date.now();
          panel._render();
          const renderCompleted = globalThis.performance?.now?.() ?? Date.now();
          panel._recordFunctionMutationUi?.(result, {
            syncMs: syncCompleted - syncStarted,
            renderAndReconcileMs: renderCompleted - syncCompleted,
          });
        });
      } catch (err) {
        if (!configurationSubmissionIsCurrent(panel, submission)) return;
        button.disabled = false;
        button.textContent = idleLabel;
        button.removeAttribute("aria-busy");
        panel._toast(`Unable to delete function: ${err.message || String(err)}`, true);
      } finally {
        panel._eocDeletingFunctions.delete(functionName);
      }
    }
  });
  host.addEventListener("change", async event => {
    const input = event.target;
    if (!input.matches?.(".tool-enabled,.group-enabled") || input.disabled) return;
    const isGroup = input.matches(".group-enabled");
    const item = isGroup
      ? (panel._draft.function_groups || []).find(group => group.id === input.dataset.groupId)
      : panel._draft.functions[collectionToolIndex(panel, input)];
    if (!item) return;
    const enabled = input.checked;
    const submission = captureConfigurationSubmission(panel);
    input.disabled = true;
    try {
      const result = isGroup
        ? await queueFunctionGroupMutation(panel, () => {
            const latest = (panel._draft.function_groups || []).find(group => group.id === item.id);
            if (!latest) throw new Error("The Function Group no longer exists");
            return panel._call("tools", "save_group", {group: {...latest, enabled}, original_id: latest.id});
          })
        : await panel._call("tools", "set_enabled", {name: item.spec.name, enabled});
      const references = result.references || {};
      const affected = (references.request_rules || []).length + (references.guest_mode ? 1 : 0);
      await completeFunctionMutation(panel, result, submission, isGroup
        ? enabled ? "Function group enabled" : "Function group disabled; member Function Tool settings were kept"
        : enabled ? "Function enabled" : affected ? `Function disabled; ${affected} saved reference${affected === 1 ? "" : "s"} remain configured but unavailable until it is re-enabled` : "Function disabled");
    } catch (err) {
      if (!configurationSubmissionIsCurrent(panel, submission)) return;
      input.checked = item.enabled !== false;
      panel._toast(`Unable to update ${isGroup ? "Function Group" : "function"}: ${err.message || String(err)}`, true);
    }
    finally { input.disabled = false; }
  });
}

function functionAcknowledgement(result) {
  return {...result, function_repair: result.function_repair ?? null, config:{functions:result.functions, function_groups:result.function_groups}};
}

export function synchronizePersistedFunctions(panel, result, submission = captureConfigurationSubmission(panel)) {
  if (!reconcileConfigurationAcknowledgement(panel, functionAcknowledgement(result), submission, {fields:["functions", "function_groups"], metadata:["function_repair"]})) return false;
  panel._result = panel._configData;
  return true;
}

function completeFunctionMutation(panel, result, submission, message, refresh = () => panel._render()) {
  requireMutationAcknowledgement(typeof result?.revision === "string" && result.revision.length > 0
    && Array.isArray(result.functions) && Array.isArray(result.function_groups), "saving Function configuration");
  return completeConfigurationAcknowledgement(panel, functionAcknowledgement(result), submission, {
    fields:["functions", "function_groups"], metadata:["function_repair"], message, response:result,
    refresh:() => {
      if (!panel._viewKey || panel._viewKey() === "capabilities/functions") panel._result = panel._configData;
      return refresh();
    },
  });
}

function setToolEditorLoading(root, loading) {
  const textarea = root.querySelector("#tool-yaml");
  textarea.readOnly = loading;
  // The native HA YAML editor is inserted beside the textarea in this label.
  // Inert gates both editors without depending on HA's internal editor API.
  if (textarea.parentElement) textarea.parentElement.inert = loading;
  for (const id of ["tool-save", "tool-validate", "built-in-function"]) {
    const control = root.querySelector(`#${id}`);
    if (control) control.disabled = loading;
  }
  root.querySelector("#tool-dialog").setAttribute("aria-busy", String(loading));
}

export function populateToolPresetPicker(panel, catalog) {
  panel._builtInFunctions = catalog.functions || [];
  const selector = panel.shadowRoot.querySelector("#built-in-function");
  if (!selector) return;
  selector.innerHTML = '<option value="">Insert Built-in Function…</option>' + panel._builtInFunctions.map((preset) => `<option value="${panel._e(preset.implementation)}" ${preset.already_configured ? "disabled" : ""}>${panel._e(preset.label)}${preset.already_configured ? " — Already configured" : ""}</option>`).join("");
  selector.value = "";
}

export async function openTool(panel, index = null, initialTool = null) {
  panel._repairToolIndex = null;
  panel._repairToolLoad = null;
  panel._repairToolRevision = null;
  const tool = initialTool || (index === null ? null : panel._draft.functions[index]);
  panel._toolIndex = index;
  panel._toolOriginalName = index === null ? null : tool?.spec?.name || null;
  const root = panel.shadowRoot;
  const dialog = root.querySelector("#tool-dialog");
  let editor = getToolYamlEditor(panel);
  const status = root.querySelector("#tool-error");
  panel._toolEditorLoad = {owner:captureConfigurationOwner(panel)};
  const {isCurrent} = captureToolEditorOwner(panel);
  root.querySelector("#tool-dialog-title").textContent = index === null ? "Add Function Tool" : "Edit Function Tool";
  root.querySelector("#tool-dialog-meta").textContent = "Loading YAML...";
  panel._setSaving(root.querySelector("#tool-save"), false);
  setToolEditorLoading(root, true);
  editor.setYaml("");
  status.className = "validation";
  status.textContent = "Loading editor...";
  root.querySelector("#built-in-picker").hidden = index !== null || Boolean(initialTool);
  panel._toolRevision = panel._configData?.revision;
  panel._toolInitialYaml = null;
  dialog.showModal();
  try {
    const [nativeYaml, [response, catalog]] = await Promise.all([
      import("./agent-config-native-yaml.js"),
      tool
        ? panel._call("tools", "serialize", index === null
          ? {tool}
          : {name: tool.spec?.name, revision: panel._toolRevision}).then((response) => [response, null])
        : Promise.all([
          panel._call("tools", "starter"),
          panel._call("tools", "built_in_catalog", {tools: panel._draft.functions || []}),
        ]),
    ]);
    if (!isCurrent()) return;
    nativeYaml.bindNativeToolYaml(panel);
    editor = getToolYamlEditor(panel);
    if (catalog) {
      populateToolPresetPicker(panel, catalog);
    }
    if (!isCurrent()) return;
    editor.setYaml(response.yaml);
    panel._toolInitialYaml = response.yaml;
    panel._toolReplaceableYaml = response.yaml;
    root.querySelector("#tool-dialog-meta").textContent = index === null ? "New tool / YAML" : `${tool.spec?.name || "Unnamed"} / ${tool.function?.type || "unknown"}`;
    status.textContent = "Edit the YAML, then save the function.";
    setToolEditorLoading(root, false);
    editor.focus();
  } catch (err) {
    if (!isCurrent()) return;
    status.className = "validation invalid";
    status.textContent = err.message || String(err);
    // Keep the unloaded editor/save disabled; Cancel remains available.
    dialog.setAttribute("aria-busy", "false");
  }
}

function toolErrorText(errors={}) { return Object.entries(errors).map(([key,value])=>`${key}: ${value}`).join(" "); }

async function validateDialogTool(panel, operation = captureToolEditorOperation(panel)) {
  if (!operation.isCurrent()) {
    panel._toast?.("The Function Tool editor owner changed. Reopen it before continuing.", true);
    return null;
  }
  const root=panel.shadowRoot;
  const status=root.querySelector("#tool-error");
  const validation = {};
  panel._toolValidation = validation;
  const isCurrent = () => operation.isUnchanged() && panel._toolValidation === validation;
  status.className="validation";
  status.textContent="Validating...";
  try {
    const result=await panel._call("tools","validate_yaml",{yaml:operation.yaml, ...operation.destination});
    if (!isCurrent()) return null;
    if(!result.valid){status.className="validation invalid";status.textContent=`Function configuration is invalid: ${toolErrorText(result.errors)}`;return null;}
    status.className="validation valid";
    status.textContent=`Valid function tool / Name: ${result.name} / Type: ${result.type}`;
    root.querySelector("#tool-dialog-meta").textContent=`${result.name} / ${result.type}`;
    return result.config;
  } catch(err){if(isCurrent()){status.className="validation invalid";status.textContent=err.message||String(err);}return null;}
}

function renderGroupFunctionChoices(panel, selected = []) {
  const root=panel.shadowRoot;
  const selectedNames=new Set(selected);
  const tools=panel._draft.functions || [];
  const list=root.querySelector("#group-functions");
  list.innerHTML=tools.map((tool)=>{const name=tool.spec?.name||"";const enabled=isFunctionToolEnabled(tool);return `<label class="group-function-choice ${enabled?"":"is-disabled"}" data-choice-search="${panel._e(`${haToolName(tool)} ${tool.function?.source_id||""} ${toolDescription(panel,tool)} ${enabled?"enabled":"disabled"}`.toLowerCase())}"><input type="checkbox" value="${panel._e(name)}" ${selectedNames.has(name)?"checked":""}><span><strong>${panel._e(haToolName(tool))}${enabled?"":" · Disabled"}</strong><small>${panel._e(toolDescription(panel,tool))}</small></span></label>`;}).join("") || panel._empty("Add function tools before assigning them to a group.");
}

function editableFunctionGroup(panel, groupId) {
  const usable = (panel._draft.function_groups || []).find(item => item.id === groupId);
  const raw = panel._result?.function_repair?.persisted_groups;
  return usable || (Array.isArray(raw) ? raw.find(item => item && item.id === groupId) : null);
}

export function openFunctionGroup(panel, groupId = null, repair = false) {
  const root=panel.shadowRoot;
  panel._groupEditorLoad = {};
  panel._setSaving(root.querySelector("#group-save"), false);
  const group=editableFunctionGroup(panel, groupId);
  panel._groupOriginalId=group?.id??null;
  panel._groupRepair=repair;
  panel._groupIdEdited=Boolean(group);
  root.querySelector("#group-dialog-title").textContent=group?"Edit function group":"Create function group";
  root.querySelector("#group-name").value=group?.name||"";
  root.querySelector("#group-id").value=group?.id||"";
  root.querySelector("#group-description").value=group?.description||"";
  root.querySelector("#group-loading-mode").value=["on_demand", "always"].includes(group?.loading_mode)?group.loading_mode:"on_demand";
  root.querySelector("#group-function-search").value="";
  root.querySelector("#group-functions-empty").hidden=true;
  root.querySelector("#group-error").textContent="";
  delete root.querySelector("#group-error").dataset.field;
  renderGroupFunctionChoices(panel,Array.isArray(group?.functions)?group.functions:[]);
  panel._groupRevision = panel._configData?.revision;
  root.querySelector("#group-dialog").showModal();
  panel._captureDialogBaseline?.(root.querySelector("#group-dialog"));
  root.querySelector("#group-name").focus();
}

function functionGroupValidation(root) {
  if (!root.querySelector("#group-name").value.trim()) return {field: "name", message: "Group name is required."};
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(root.querySelector("#group-id").value.trim())) return {field: "id", message: "Group ID must start with a lowercase letter and use only lowercase letters, numbers, underscores, or hyphens."};
  if (!root.querySelector("#group-description").value.trim()) return {field: "description", message: "Add a concise description so the model knows when this group is relevant."};
  return null;
}

function refreshFunctionGroupValidation(root) {
  const error = root.querySelector("#group-error");
  if (!error?.dataset.field) return;
  const validation = functionGroupValidation(root);
  if (validation) {
    error.dataset.field = validation.field;
    error.textContent = validation.message;
  } else {
    delete error.dataset.field;
    error.textContent = "";
  }
}

async function saveFunctionGroup(panel) {
  const root=panel.shadowRoot;
  const dialog = root.querySelector("#group-dialog");
  const button = root.querySelector("#group-save");
  if (button.disabled) return;
  const generation = panel._groupEditorLoad;
  const originalId = panel._groupOriginalId, repair = panel._groupRepair, revision = panel._groupRevision;
  const baselineConfig = panel._configData?.config;
  const authored = dialogState(dialog);
  const name=root.querySelector("#group-name").value.trim();
  const id=root.querySelector("#group-id").value.trim();
  const description=root.querySelector("#group-description").value.trim();
  const loading_mode=root.querySelector("#group-loading-mode").value;
  const originalGroup=editableFunctionGroup(panel,panel._groupOriginalId);
  const guest_allowed=originalGroup?.guest_allowed===true;
  const enabled=originalGroup?.enabled!==false;
  const functions=[...root.querySelectorAll("#group-functions input:checked")].map((input)=>input.value);
  const error=root.querySelector("#group-error");
  const validation = functionGroupValidation(root);
  if (validation) {
    error.dataset.field = validation.field;
    error.textContent = validation.message;
    return;
  }
  delete error.dataset.field;
  error.textContent="Saving...";
  panel._setSaving(button, true);
  const submission = captureConfigurationSubmission(panel);
  const ownsEditor = () => configurationSubmissionIsCurrent(panel, submission)
    && panel._groupEditorLoad === generation && panel.shadowRoot === root
    && root.querySelector("#group-dialog") === dialog && dialog.open;
  const unchanged = () => ownsEditor() && same(dialogState(dialog), authored);
  try {
    const result=await queueFunctionGroupMutation(panel,()=>unchanged()
      ? panel._call("tools","save_group",{group:{id,name,description,loading_mode,functions,guest_allowed,enabled},
        revision, ...(originalId!==null?{original_id:originalId}:{}), ...(repair?{repair:true}:{})}) : null);
    if (!result) {
      if (ownsEditor()) error.textContent = "Changes updated while waiting. Save again to submit them.";
      return;
    }
    const previous = {revision:panel._configData?.revision, config:panel._configData?.config};
    await completeFunctionMutation(panel, result, submission, "Function group saved", () => {
      const closeAfterAcknowledgement = unchanged();
      if (ownsEditor()) {
        // Identity and dirty tracking belong to the commit. A failed modal close
        // must leave the next command updating this group at its saved revision.
        setDialogBaseline(panel, "group-dialog", authored);
        if (previous.config === baselineConfig && [revision, result.revision].includes(previous.revision)) {
          panel._groupOriginalId = id;
          panel._groupRevision = result.revision;
          panel._groupRepair = false;
          if (!closeAfterAcknowledgement) error.textContent = "Submitted version saved. Your newer changes are still unsaved.";
        } else if (!closeAfterAcknowledgement) error.textContent = "Submitted version saved. Reload the latest settings before saving these edits.";
      }
      if (closeAfterAcknowledgement) dialog.close();
      panel._render();
    });
  } catch(err) { if (ownsEditor()) { delete error.dataset.field; error.textContent=err.message||String(err); } }
  finally { if (ownsEditor()) panel._setSaving(button, false); }
}

async function assignToolToGroup(panel, select) {
  const name = select.closest?.("[data-tool-key]")?.dataset.toolKey;
  const targetId = select.value;
  if (!name) return;
  select.disabled = true;
  let previousGroupId = "";
  const submission = captureConfigurationSubmission(panel);
  try {
    const result = await queueFunctionGroupMutation(panel, async () => {
      const config = panel._draft || panel._result?.config || {};
      const groups = config.function_groups || [];
      const current = groups.find(group => (group.functions || []).includes(name));
      previousGroupId = current?.id || "";
      if (previousGroupId === targetId) return null;
      const target = targetId ? groups.find(group => group.id === targetId) : current;
      if (!target) throw new Error("The selected Function Group no longer exists");
      const functions = targetId
        ? [...new Set([...(target.functions || []), name])]
        : (target.functions || []).filter(item => item !== name);
      return panel._call("tools", "save_group", {
        group: {...target, functions}, original_id: target.id,
      });
    });
    if (!result) return;
    const target = (result.function_groups || []).find(group => group.id === targetId);
    await completeFunctionMutation(panel, result, submission, targetId ? `${name} moved to ${target?.name || targetId}` : `${name} is now available on every request`);
  } catch (err) {
    if (!configurationSubmissionIsCurrent(panel, submission)) return;
    select.value = previousGroupId;
    panel._toast(`Unable to change Function Group: ${err.message || String(err)}`, true);
  } finally { select.disabled = false; }
}

export function bindTools(panel) {
  ensureRouteStyle(panel);
  const root=panel.shadowRoot;
  const host = root.querySelector(".tools-surface");
  if (host && !host.__eocAssignmentBound) {
    host.__eocAssignmentBound = true;
    host.addEventListener("change", event => {
      if (event.target.matches?.(".function-group-assignment") && !event.target.disabled) void assignToolToGroup(panel, event.target);
    });
  }
  // Catalogue discovery and its dialog code are optional for the initial list.
  const attachHA = () => import("./ha-llm-tools.js").then(({bindHALlmTools}) => {
    if (panel.shadowRoot === root && root.querySelector(".tools-surface") === host) {
      bindHALlmTools(panel, (result, submission, refresh) => completeFunctionMutation(panel, result, submission, "Home Assistant tools added", refresh));
    }
  });
  panel._ensureHALlmTools = attachHA;
  if ((panel._draft?.functions || []).some(isHALlmTool)) void attachHA().catch(() => {});
  for (const id of ["add-ha-tools", "refresh-ha-tools"]) {
    const button = root.querySelector(`#${id}`);
    if (!button) continue;
    const firstClick = async () => {
      button.removeEventListener("click", firstClick);
      button.disabled = true;
      try {
        await attachHA();
        if (button.isConnected) {
          button.disabled = false;
          button.click();
        }
      } catch (err) {
        button.disabled = false;
        button.addEventListener("click", firstClick);
        panel._toast(err.message || String(err), true);
      }
    };
    button.addEventListener("click", firstClick);
  }
  bindHelp(panel);
  prepareToolsCollection(panel);
  bindToolCollection(panel);
  root.querySelector("#validate-tools")?.addEventListener("click",async()=>{const status=root.querySelector("#tool-status");try{const result=await panel._call("tools","validate_current");status.className=`validation ${result.valid?"valid":"invalid"}`;status.textContent=result.valid?"All saved tools and groups are valid":toolErrorText(result.errors);}catch(err){status.className="validation invalid";status.textContent=err.message||String(err);}});
  getToolYamlEditor(panel);
  root.querySelector("#built-in-function")?.addEventListener("change",async(event)=>{const selector=event.target;const preset=(panel._builtInFunctions||[]).find((item)=>item.implementation===selector.value);if(!preset)return;const editor=getToolYamlEditor(panel);const replaceable=canReplaceToolYamlWithoutConfirmation(editor.getYaml(),panel._toolReplaceableYaml);if(!replaceable&&!await panel._confirm("Replace current YAML with this built-in function preset?","Your current Function Tool YAML will be replaced in the editor. Nothing is saved until you select Save.","Replace YAML")){selector.value="";return;}editor.setYaml(preset.yaml);panel._toolReplaceableYaml=preset.yaml;await validateDialogTool(panel);});
  root.querySelector("#tool-cancel")?.addEventListener("click",()=>root.querySelector("#tool-dialog").close());
  root.querySelector("#tool-dialog")?.addEventListener("cancel",()=>{panel._toolIndex=null;});
  root.querySelector("#tool-dialog")?.addEventListener("close", (event) => {
    // Ignore queued close events after navigation, DOM replacement or reopening.
    if (event.target !== root.querySelector("#tool-dialog") || event.target.open) return;
    panel._toolEditorLoad = null;
    panel._setSaving(root.querySelector("#tool-save"), false);
    setToolEditorLoading(root, false);
  });
  root.querySelector("#tool-validate")?.addEventListener("click",()=>validateDialogTool(panel));
  root.querySelector("#tool-save")?.addEventListener("click", async () => {
    const button = root.querySelector("#tool-save");
    if (button.disabled) return;
    const operation = captureToolEditorOperation(panel);
    if (!operation.isCurrent()) {
      panel._toast?.("The Function Tool editor owner changed. Reopen it before continuing.", true);
      return;
    }
    let finishingOperation = operation;
    const submission = captureConfigurationSubmission(panel);
    panel._setSaving(button, true);
    try {
      const result = await panel._call("tools", "save", {yaml: operation.yaml, ...operation.destination,
        ...(operation.revision !== undefined ? {revision: operation.revision} : {}),
        ...(operation.originalName ? {original_name: operation.originalName} : {})});
      if (result.valid === false) {
        if (!operation.isUnchanged()) return;
        root.querySelector("#tool-error").className = "validation invalid";
        root.querySelector("#tool-error").textContent = `Function configuration is invalid: ${toolErrorText(result.errors)}`;
        return;
      }
      const previous = {revision:panel._configData?.revision, config:panel._configData?.config};
      await completeFunctionMutation(panel, result, submission, "Changes saved", () => {
        const closeAfterAcknowledgement = operation.isUnchanged();
        finishingOperation = acknowledgeToolEditor(panel, operation, result, previous);
        if (closeAfterAcknowledgement) operation.dialog.close();
        panel._render();
      });
    } catch (err) {
      if (!operation.isUnchanged()) return;
      root.querySelector("#tool-error").className = "validation invalid";
      root.querySelector("#tool-error").textContent = err.message || String(err);
    } finally { if (finishingOperation.isCurrent()) panel._setSaving(button, false); }
  });
  root.querySelector("#group-name")?.addEventListener("input",(event)=>{if(!panel._groupIdEdited)root.querySelector("#group-id").value=functionGroupIdFromName(event.target.value);refreshFunctionGroupValidation(root);});
  root.querySelector("#group-id")?.addEventListener("input",()=>{panel._groupIdEdited=true;refreshFunctionGroupValidation(root);});
  root.querySelector("#group-description")?.addEventListener("input",()=>refreshFunctionGroupValidation(root));
  bindOwnedEvent(root.querySelector("#group-function-search"), "input", "group-assignment", (event)=>{
    const queryTokens=searchTokens(event.target.value);
    const choices=[...root.querySelectorAll("#group-functions .group-function-choice")];
    choices.forEach(choice=>{choice.hidden=!matchesFunctionSearchTokens(queryTokens,cachedFunctionSearchTokens(choice,choice.dataset.choiceSearch));});
    root.querySelector("#group-functions-empty").hidden=!queryTokens.length||choices.some(choice=>!choice.hidden);
  });
  bindOwnedEvent(root.querySelector("#group-function-clear"), "click", "group-assignment", ()=>{
    const search=root.querySelector("#group-function-search");search.value="";search.dispatchEvent(new Event("input",{bubbles:true}));search.focus();
  });
  root.querySelector("#group-cancel")?.addEventListener("click",()=>root.querySelector("#group-dialog").close());
  root.querySelector("#group-save")?.addEventListener("click",()=>saveFunctionGroup(panel));
}


export function configurationDialogs(panel) {
  const view = panel._viewKey?.();
  return [
    (!view || view === "assistant/prompt-context") ? `<dialog id="prompt-preview-dialog" class="editor-dialog wide prompt-preview-dialog" aria-labelledby="prompt-preview-title"><div class="dialog-header"><div><h2 id="prompt-preview-title">Preview effective request</h2><p class="dialog-meta">Locally assembled fresh-request content</p></div></div><div class="dialog-body prompt-preview-body"><div class="notice"><strong>Current request preview</strong><p>Shows locally assembled content that would accompany a brand-new message now. Current templates and Home Assistant context are resolved. User input and conversation history are excluded.</p><p>This may contain private entity state, memory data, Knowledge context, user instructions, and Function Tool schemas. Provider-internal framing and hidden server-side content cannot be inspected.</p></div><div class="request-preview-metrics"><strong id="request-footprint">Calculating...</strong><span id="function-group-savings"></span></div><div id="request-preview-sections" class="request-preview-sections"></div><ul id="prompt-preview-notes" class="prompt-preview-notes"></ul><div id="prompt-preview-status" class="validation" role="status" aria-live="polite"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="prompt-preview-close">Close</button><button type="button" id="copy-prompt-preview" disabled>Copy all</button></div></dialog>` : "",
    `<dialog id="import-dialog" class="editor-dialog wide" aria-labelledby="import-dialog-title"><div class="dialog-header"><h2 id="import-dialog-title">Import agent configuration</h2></div><div class="dialog-body">${panel._configDirty ? `<div class="notice warning"><strong>Unsaved shared draft</strong><p>Creating a new agent preserves this draft. Overwriting the current agent discards it after confirmation.</p></div>` : ""}<label>Exported JSON or YAML<textarea id="import-document" class="yaml-editor" spellcheck="false"></textarea></label><div class="mode-row"><label><input type="radio" name="import-mode" value="new" checked> Create a new agent</label><label><input type="radio" name="import-mode" value="current"> Overwrite current agent</label></div><div id="import-summary" class="validation" aria-live="polite">Validate the document to preview it.</div></div><div class="dialog-actions"><button type="button" class="secondary" id="import-cancel">Cancel</button><button type="button" class="secondary" id="import-preview">Validate & preview</button><button type="button" id="import-apply" disabled>Import</button></div></dialog>`,
    (!view || view === "capabilities/functions") ? `<dialog id="tool-dialog" class="editor-dialog tool-dialog" aria-labelledby="tool-dialog-title"><div class="dialog-header"><div><span class="setting-label-row"><h2 id="tool-dialog-title">Function Tool</h2>${helpButton(panel,"function_tools")}</span><p id="tool-dialog-meta" class="dialog-meta">YAML</p></div></div><div class="dialog-body tool-dialog-body"><div id="built-in-picker" class="built-in-picker"><label for="built-in-function">Insert Built-in Function</label><select id="built-in-function"><option value="">Insert Built-in Function…</option></select><small>Built-in functions are implemented directly by Extended OpenAI Conversation. Selecting one inserts an editable Function Tool that exposes the capability to the model.</small></div><label class="tool-editor-label"><span class="sr-only">Function Tool YAML</span><textarea data-native-yaml-fallback id="tool-yaml" class="yaml-editor tool-yaml-editor" spellcheck="false" wrap="off" aria-describedby="tool-error"></textarea><ha-yaml-editor id="tool-yaml-native" class="tool-yaml-native-editor" hidden in-dialog aria-label="Function Tool YAML" aria-describedby="tool-error"></ha-yaml-editor></label><div id="tool-error" class="validation" role="status" aria-live="polite"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="tool-cancel">Cancel</button><button type="button" class="secondary" id="tool-validate">Validate</button><button type="button" id="tool-save">Save</button></div></dialog>` : "",
    (!view || view === "capabilities/functions") ? `<dialog id="group-dialog" class="editor-dialog group-dialog" aria-labelledby="group-dialog-title"><div class="dialog-header"><div><h2 id="group-dialog-title">Function group</h2><p>Choose Always available to send full schemas immediately, or Load when needed to send a compact catalogue first.</p></div></div><div class="dialog-body group-dialog-body"><div class="form-grid"><label>Group name<input id="group-name" maxlength="100" autocomplete="off" placeholder="Reminders"></label><label>Group ID<input id="group-id" maxlength="64" autocomplete="off" spellcheck="false" placeholder="reminders"><small>Stable lowercase ID used by the model. Advanced users may edit it.</small></label></div><label>Description<textarea id="group-description" class="short-textarea" maxlength="500" placeholder="Create and manage scheduled, recurring, and triggered reminders."></textarea><small>Keep this concise. On-demand groups send it in the compact catalogue; Always available groups send their full tool schemas.</small></label><label>Availability<select id="group-loading-mode"><option value="on_demand">Load when needed</option><option value="always">Always available</option></select><small>On-demand groups add one model round-trip the first time they are needed in an active conversation.</small></label><fieldset class="group-functions-fieldset"><legend>Functions</legend><input id="group-function-search" type="search" placeholder="Search functions..." aria-label="Search functions to assign"><small>Selecting a function moves it from any other group. Guest availability is managed centrally on the Guest page.</small><div id="group-functions" class="group-function-choices"></div><div id="group-functions-empty" class="empty" hidden>No functions match this search. <button type="button" class="secondary compact-button" id="group-function-clear">Clear search</button></div></fieldset><div id="group-error" class="validation invalid" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="group-cancel">Cancel</button><button type="button" id="group-save">Save</button></div></dialog>` : "",
    helpPopover(),
  ].join("");
}
