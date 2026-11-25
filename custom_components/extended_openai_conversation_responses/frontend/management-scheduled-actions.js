import {
  captureConfigurationOwner,
  configurationSubmissionIsCurrent,
} from "./configuration-acknowledgement.js";

const VIEW = "capabilities/scheduled-actions";
const writes = new WeakMap();

function writeKey({agentId, entryId, userId, authenticationGeneration}) {
  return JSON.stringify([agentId, entryId, userId, authenticationGeneration]);
}

export function renderScheduledActions(panel) {
  const result = panel._result;
  const escape = value => panel._e(String(value));
  const busy = writes.get(panel)?.has(writeKey(captureConfigurationOwner(panel)));
  let content;
  if (result?.status === "unavailable") {
    content = '<p class="notice warning" role="status">The scheduled action queue is unavailable.</p>';
  } else if (result?.status !== "ok" || !Array.isArray(result.actions)) {
    content = '<p class="notice warning" role="alert">Unable to read scheduled actions. Refresh to try again.</p>';
  } else if (!result.actions.length) {
    content = panel._empty("No pending actions for this assistant.");
  } else {
    content = '<div class="list">' + result.actions.map(action => {
      const date = escape(panel._formatDate(action.scheduled_at));
      const description = escape(action.description);
      return '<article class="content-card"><div class="section-heading"><div><h2>' + description
        + '</h2><p><time datetime="' + escape(action.scheduled_at) + '">' + date
        + '</time></p></div><button type="button" class="secondary" data-cancel-action="' + escape(action.action_id)
        + '" aria-label="Cancel ' + description + ' at ' + date
        + '"' + (busy ? " disabled" : "") + '>Cancel</button></div></article>';
    }).join("") + '</div>';
  }
  return '<section class="page-intro"><div class="section-heading"><div><h1>Scheduled Actions</h1>'
    + '<p>Pending EOAI Function Tools for this assistant. Home Assistant timers and delayed conversation commands are managed separately.</p>'
    + '</div><button type="button" class="secondary" id="refresh-scheduled-actions">Refresh</button></div></section>'
    + content;
}

export function bindScheduledActions(panel) {
  panel.shadowRoot.querySelector("#refresh-scheduled-actions")?.addEventListener("click", () => panel._loadSection());
  for (const button of panel.shadowRoot.querySelectorAll("[data-cancel-action]")) {
    button.addEventListener("click", () => cancelAction(panel, button.dataset.cancelAction));
  }
}

export async function cancelAction(panel, actionId) {
  const owner = captureConfigurationOwner(panel);
  const inView = () => panel.isConnected !== false && panel._viewKey() === VIEW && panel._data?.is_admin === true;
  const isCurrent = () => inView() && configurationSubmissionIsCurrent(panel, owner);
  const key = writeKey(owner);
  let pending = writes.get(panel);
  if (!pending) writes.set(panel, pending = new Set());
  if (!isCurrent() || pending.has(key)) return;
  pending.add(key);
  for (const button of panel.shadowRoot.querySelectorAll("[data-cancel-action]")) button.disabled = true;
  try {
    const result = await panel._call("scheduled_actions", "cancel", {action_id: actionId});
    if (!isCurrent()) return;
    if (result?.status === "cancelled" && result.action_id === actionId) {
      panel._toast("Scheduled action cancelled");
    } else if (result?.status === "not_pending") {
      panel._toast("This action is no longer pending.", true);
    } else if (result?.status === "unavailable") {
      panel._toast("The scheduled action queue is unavailable.", true);
    } else {
      panel._toast("Cancellation could not be confirmed.", true);
    }
  } catch (error) {
    if (isCurrent()) {
      const rejected = error?.mutationOutcome === "rejected" || error?.code === "eoai_mutation_rejected";
      panel._toast(rejected ? "The scheduled action could not be cancelled." : "Cancellation could not be confirmed.", true);
    }
  } finally {
    pending.delete(key);
    // The normal route loader owns incoming data after switching assistants.
    if (inView() && writeKey(captureConfigurationOwner(panel)) === key) await panel._loadSection();
  }
}
