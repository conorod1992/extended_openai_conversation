import {adoptKeyedElements, reconcileKeyedChildren, delegateCollectionActions, restoreCollectionFocus, setText} from "./keyed-collection.js";
import {decodeCollectionId, encodeCollectionId} from "./collection-identifiers.js";
export {restoreCollectionFocus} from "./keyed-collection.js";

export function restoreKnowledgeFocus(root, id) {
  restoreCollectionFocus(root, `.source-edit-button[data-id="${CSS.escape(encodeCollectionId(id))}"]`, "#add-source");
}
import {selectedFeatureStatus} from "./management-feature-status-core.js";
import {knowledgeSourceAvailabilityBadge} from "./knowledge-presentation.js";
import {formatUsageNumber} from "./usage-format.js";
import {requireMutationAcknowledgement, captureConfigurationSubmission, configurationSubmissionIsCurrent, completeConfigurationAcknowledgement} from "./configuration-acknowledgement.js";

const collections = new WeakMap();
const identity = panel => JSON.stringify([panel._selectedAgent?.()?.entry_id, panel._agentId, panel._hass?.user?.id]);
const signature = source => JSON.stringify([source.title, source.description, source.enabled, source.character_count, source.updated_at]);
const matches = (source, query) => `${source.title || ""} ${source.description || ""}`.toLocaleLowerCase().includes(query);
const countText = sources => `${formatUsageNumber(sources.length)} source${sources.length === 1 ? "" : "s"}`;
function knowledgeAccessState(panel) {
  const current = selectedFeatureStatus(panel, "knowledge") || {};
  const enabled = typeof current.enabled === "boolean" ? current.enabled : panel._selectedAgent?.()?.knowledge_enabled !== false;
  if (panel._result?.sources_unavailable) {
    return {
      enabled,
      state: enabled ? "unavailable" : "disabled",
      label: enabled ? "Unavailable" : "Off",
      detail: "The stored source count is unavailable.",
    };
  }
  const sources = panel._result?.sources || [];
  const usable = sources.some((source) => source.enabled !== false);
  const exceptional = ["unknown", "unavailable", "error"].includes(current.state);
  const state = exceptional ? current.state : enabled ? usable ? "available" : "empty" : "disabled";
  const detail = !enabled ? "Stored sources stay in the library, but the assistant cannot use them."
    : ["unknown", "unavailable", "error"].includes(state) ? current.detail || current.summary || "Knowledge availability could not be confirmed."
    : usable ? "The assistant can search these sources when needed."
    : sources.length ? "Enable a source to make Knowledge available to the assistant."
    : "Add a source to make Knowledge available to the assistant.";
  return {
    enabled, state, detail,
    label: exceptional ? current.label || "Unknown" : {available:"Available", empty:sources.length ? "No enabled sources" : "Needs sources", disabled:"Off"}[state],
  };
}
const statusMarkup = panel => {
  const status = knowledgeAccessState(panel);
  const repair = panel._result?.stats?.knowledge_storage_repair_required
    ? '<p class="help">Knowledge storage needs repair. Readable sources are shown, and changes to sources are unavailable until the storage is repaired.</p>' : "";
  return `<div class="compact-status"><small>${panel._e(status.detail)}</small><strong class="status-value ${["enabled", "available"].includes(status.state) ? "on" : ""}">${panel._e(status.label)}</strong></div>${repair}`;
};

function updateAgentKnowledge(panel, status, enabled = status?.enabled) {
  const agent = panel._selectedAgent?.();
  if (!agent) return;
  if (typeof enabled === "boolean") agent.knowledge_enabled = enabled;
  if (!status) return;
  agent.feature_status = {...agent.feature_status, knowledge:status};
  agent.knowledge_source_count = status.source_count;
  agent.knowledge_total_source_count = status.total_source_count;
}

export function applyKnowledgeMutation(panel, response, deletedId = null) {
  if (!panel._result || !Array.isArray(panel._result.sources)) return false;
  if (deletedId && response?.deleted !== 1) return false;
  const summary = response?.summary;
  if (!deletedId && !summary?.source_id) return false;
  const previous = panel._result.sources;
  const sources = previous.filter(source => source.source_id !== (deletedId || summary.source_id));
  if (summary) {
    const unchanged = previous.findIndex(source => source.source_id === summary.source_id && source.updated_at === summary.updated_at);
    sources.splice(Math.max(0, unchanged), 0, summary);
    if (Array.isArray(response.source_order)) {
      const positions = new Map(response.source_order.map((id, index) => [id, index]));
      const fallback = positions.size;
      sources.sort((left, right) => (positions.get(left.source_id) ?? fallback) - (positions.get(right.source_id) ?? fallback));
    }
  }
  panel._result = {
    ...panel._result, sources,
    ...(response.stats ? {stats: response.stats} : {}),
    ...(response.feature_status ? {feature_status: response.feature_status} : {}),
  };
  updateAgentKnowledge(panel, response.feature_status);
  return true;
}


export function knowledgeAvailabilityMarkup(panel) {
  const status = `<div id="knowledge-status" aria-live="polite">${statusMarkup(panel)}</div>`;
  if (panel._data?.is_admin === false) {
    return `<div class="knowledge-availability-setting"><strong>Assistant access</strong>${status}</div>`;
  }
  const enabled = knowledgeAccessState(panel).enabled;
  return `<div class="knowledge-availability-setting">
    <div class="config-toggle setting">
      <div class="setting-copy"><span class="setting-label-row"><label for="knowledge-enabled-toggle"><strong>Allow the assistant to use Knowledge</strong></label></span>${status}<small>Changes here save immediately.</small></div>
      <label class="switch-control" for="knowledge-enabled-toggle"><input id="knowledge-enabled-toggle" type="checkbox" role="switch" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>
    </div>
  </div>`;
}

async function saveKnowledgeAvailability(panel, input) {
  const desired = input.checked;
  const submission = captureConfigurationSubmission(panel);
  const loadToken = panel._loadToken;
  input.disabled = true;
  try {
    const result = await panel._call("knowledge", "set_enabled", {enabled: desired});
    requireMutationAcknowledgement(typeof result?.knowledge_enabled === "boolean", "changing Knowledge availability");
    await completeConfigurationAcknowledgement(panel, {revision:result.revision, config:{knowledge_enabled:result.knowledge_enabled}}, submission, {
      response:result, fields:["knowledge_enabled"], message:`Knowledge ${result.knowledge_enabled ? "enabled" : "disabled"}`,
      refresh:() => {
        updateAgentKnowledge(panel, result.feature_status, result.knowledge_enabled);
        if (panel._viewKey?.() !== "data-memory/knowledge" || panel._loadToken !== loadToken) return;
        if (panel._result) panel._result = {...panel._result, feature_status:result.feature_status};
        panel._render();
      },
    });
  } catch (err) {
    input.checked = !desired;
    input.disabled = false;
    if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to update Knowledge: ${err.message || String(err)}`, true);
  } finally {
    input.disabled = false;
  }
}

export function bindKnowledgeAvailability(panel) {
  const input = panel.shadowRoot?.querySelector("#knowledge-enabled-toggle");
  if (!input || input.__eocKnowledgeBound) return;
  input.__eocKnowledgeBound = true;
  input.addEventListener("change", () => saveKnowledgeAvailability(panel, input));
}

function sourceCard(panel, source) {
  const id = panel._e(encodeCollectionId(source.source_id));
  return `<article class="list-card" data-source-id="${id}" ${matches(source, String(panel._query || "").trim().toLocaleLowerCase()) ? "" : "hidden"}><div class="card-main clickable edit-source" tabindex="0" role="button" data-id="${id}"><h3>${panel._e(source.title)}</h3>${knowledgeSourceAvailabilityBadge(source)}<p class="description">${panel._e(source.description || "No description")}</p><p class="meta">${formatUsageNumber(source.character_count || 0)} characters · Updated ${panel._e(panel._formatDate(source.updated_at))}</p></div><div class="actions"><button type="button" class="secondary source-edit-button" data-id="${id}">Edit</button><button type="button" class="danger delete-source" data-id="${id}">Delete</button></div></article>`;
}

export function renderKnowledge(panel) {
  if (panel._result?.sources_unavailable) {
    return `<section class="content-card"><h2>Sources</h2>${knowledgeAvailabilityMarkup(panel)}<p class="notice warning">Knowledge sources are unavailable. Reload this section after checking Knowledge storage.</p></section>`;
  }
  const sources = panel._result?.sources || [];
  return `<section class="content-card" data-knowledge-collection data-collection-identity="${panel._e(identity(panel))}"><div class="section-heading"><div><h2>Sources</h2><p data-source-count>${countText(sources)}</p></div><button type="button" id="add-source">+ Add source</button></div>${knowledgeAvailabilityMarkup(panel)}<input id="list-search" class="search" type="search" value="${panel._e(panel._query)}" placeholder="Filter by title or description" aria-label="Filter Knowledge sources"><div class="list knowledge-list">${sources.map(source => sourceCard(panel, source)).join("")}<div data-source-empty>${panel._empty("No Knowledge sources yet. Add one to make reference information available on demand.")}</div></div></section>`;
}

export function filterKnowledge(panel) {
  const state = collections.get(panel);
  if (!state || !state.host.isConnected || state.identity !== identity(panel)) return;
  const query = String(panel._query || "").trim().toLocaleLowerCase();
  let visible = 0;
  for (const source of panel._result?.sources || []) {
    const node = state.cards.get(encodeCollectionId(source.source_id))?.node;
    if (!node) continue;
    const show = matches(source, query);
    if (node.hidden === show) node.hidden = !show;
    if (show) visible++;
  }
  state.empty.hidden = visible > 0;
  // Empty copy has the same presentation as the original route's _empty().
  const message = query ? "No sources match this filter." : "No Knowledge sources yet. Add one to make reference information available on demand.";
  setText(state.empty.firstElementChild || state.empty, message);
}

export function reconcileKnowledge(panel) {
  if (panel._result?.sources_unavailable) return false;
  const state = collections.get(panel);
  if (!state || state.host !== panel.shadowRoot.querySelector("[data-knowledge-collection]") || state.identity !== identity(panel)) return false;
  const sources = panel._result?.sources || [];
  reconcileKeyedChildren(state.list, state.cards, sources, source => encodeCollectionId(source.source_id), signature, source => sourceCard(panel, source), [state.empty]);
  setText(state.host.querySelector("[data-source-count]"), countText(sources));
  const status = state.host.querySelector("#knowledge-status"), markup = statusMarkup(panel);
  if (state.status !== markup) { status.innerHTML = markup; state.status = markup; }
  const toggle = panel.shadowRoot.querySelector("#knowledge-enabled-toggle");
  const enabled = selectedFeatureStatus(panel, "knowledge")?.enabled;
  if (toggle && typeof enabled === "boolean" && !toggle.disabled) toggle.checked = enabled;
  filterKnowledge(panel);
  return true;
}

export function bindKnowledge(panel) {
  if (panel._viewKey() !== "data-memory/knowledge") return;
  bindKnowledgeAvailability(panel);
  const host = panel.shadowRoot.querySelector("[data-knowledge-collection]");
  if (!host) return;
  if (collections.get(panel)?.host !== host) {
    const list = host.querySelector(".knowledge-list");
    collections.set(panel, {host, list, identity: identity(panel), cards: adoptKeyedElements(list, "[data-source-id]", "sourceId"), empty: list.querySelector("[data-source-empty]"), status: statusMarkup(panel)});
    // Prime adopted signatures before the first asynchronous data change.
    reconcileKnowledge(panel);
    host.querySelector("#list-search").addEventListener("input", event => { panel._query = event.target.value; filterKnowledge(panel); });
  }
  delegateCollectionActions(host, "#add-source,.edit-source,.source-edit-button,.delete-source", control => {
    const id = decodeCollectionId(control.dataset.id);
    if (control.matches(".delete-source")) void panel._deleteSource(id, false, control);
    else void panel._openKnowledge(id);
  });
}
