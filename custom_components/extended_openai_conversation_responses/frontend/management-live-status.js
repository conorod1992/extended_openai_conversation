import {captureAuthentication} from "./authentication-presentation.js";
import {capturePageRead, acceptPageRead, completePolicyResponse} from "./page-save-acknowledgement.js";
import {initializePageDraft} from "./management-page-drafts.js";
import {same} from "./unsaved-state.js";

const watches = new WeakMap();
const paused = new WeakMap();
const deferred = new WeakMap();
const identityFor = (panel, view) => `${panel._data?.entry_id || ""}|${panel._agentId || ""}|${view}`;

export function stopLiveStatus(panel, key = null) {
  if (!key) paused.delete(panel);
  const entries = watches.get(panel);
  if (!entries) return;
  for (const [name, watch] of entries) {
    if (key && name !== key) continue;
    clearTimeout(watch.timer);
    watch.stopped = true;
    entries.delete(name);
  }
}

export function pauseLiveStatus(panel) {
  const entries = watches.get(panel);
  if (!entries?.size) return;
  const remaining = [...entries].map(([key, watch]) => ({key, userId:watch.userId, identity:watch.identity,
    options:{...watch.options, maxRefreshes:watch.options.maxRefreshes - watch.attempts}}));
  stopLiveStatus(panel);
  paused.set(panel, remaining);
}

export function resumeLiveStatus(panel) {
  if (panel._eocPresentationBlocked) return;
  const remaining = paused.get(panel);
  paused.delete(panel);
  for (const watch of remaining || []) {
    if (watch.userId === panel.hass?.user?.id && watch.identity === identityFor(panel, panel._viewKey?.())
        && watch.options.maxRefreshes > 0) watchLiveStatus(panel, watch.key, watch.options);
  }
}

export function watchLiveStatus(panel, key, {view, delay, refresh, maxRefreshes = Infinity}) {
  let entries = watches.get(panel);
  if (!entries) watches.set(panel, entries = new Map());
  const identity = identityFor(panel, view);
  const existing = entries.get(key);
  if (existing?.identity === identity && !existing.stopped && existing.authCurrent()) return;
  stopLiveStatus(panel, key);
  const watch = {identity, userId:panel.hass?.user?.id, options:{view, delay, refresh, maxRefreshes},
    stopped:false, timer:null, attempts:0, authCurrent:captureAuthentication(panel)};
  entries.set(key, watch);
  const stop = () => {
    if (entries.get(key) === watch) stopLiveStatus(panel, key);
    else {clearTimeout(watch.timer);watch.stopped = true;}
  };
  const current = () => !watch.stopped && watch.authCurrent() && panel.isConnected !== false
    && panel._viewKey?.() === view
    && identityFor(panel, view) === identity;
  const schedule = () => {
    if (!current() || watch.attempts >= maxRefreshes) return stop();
    const milliseconds = typeof delay === "function" ? delay() : delay;
    if (milliseconds == null) return stop();
    watch.timer = setTimeout(async () => {
      if (!current()) return stop();
      if (globalThis.document?.hidden) return schedule();
      watch.attempts++;
      try { await refresh(current); }
      catch (_err) { /* A transient read failure must not invalidate the user's draft. */ }
      schedule();
    }, Math.max(200, milliseconds));
  };
  schedule();
}

function boundaryDelay(value) {
  const boundary = Date.parse(value || "");
  return Number.isFinite(boundary) && boundary > Date.now()
    ? Math.min(60000, boundary - Date.now() + 200) : 60000;
}

function refreshEditor(panel, current) {
  const root = panel.shadowRoot;
  const editing = () => root.activeElement?.matches("input,select,textarea,ha-selector,ha-entity-picker");
  if (!editing()) return panel._render();
  if (deferred.get(panel)?.()) return;
  deferred.set(panel, current);
  const end = () => requestAnimationFrame(() => {
    if (current() && editing()) return;
    root.removeEventListener("focusout", end);
    if (deferred.get(panel) !== current) return;
    deferred.delete(panel);
    if (current()) panel._render();
  });
  root.addEventListener("focusout", end);
}

export function watchTimedFeatureStatus(panel, feature) {
  const view = panel._viewKey?.();
  if (!["capabilities/guest-mode", "capabilities/quiet-hours"].includes(view)) return;
  const guest = view === "capabilities/guest-mode";
  watchLiveStatus(panel, "timed-feature", {
    view,
    delay: () => guest
      ? boundaryDelay(panel._result?.status?.scheduled ? panel._result.status.active_from : panel._result?.status?.active_until)
      : boundaryDelay(panel._result?.period_ends_at),
    refresh: async (current) => {
      const previous = panel._result, read = capturePageRead(panel, view);
      const pending = () => panel._guestOperation || panel._unsavedState?.scopes.get(view)?.pending;
      if (pending()) return;
      const fresh = await panel._call(guest ? "guest_mode" : "quiet_hours", "get");
      if (!current() || pending() || panel._result !== previous) return;
      if (guest && !completePolicyResponse(fresh, true)) return;
      const result = guest ? {...previous, ...fresh} : fresh;
      if (!acceptPageRead(panel, result, read, {publish:true})) return;
      if (guest) {
        const agent = panel._selectedAgent?.();
        if (agent) agent.guest_mode = result.status;
      }
      const draft = guest ? "_guestDraft" : "_quietHoursDraft";
      const before = panel[draft];
      initializePageDraft(panel);
      feature?.refreshTimedStatus(panel);
      if (!same(before, panel[draft]) || previous?.legacy_policy !== result.legacy_policy
          || !same(previous?.satellites, result.satellites)) {
        refreshEditor(panel, () => current() && panel._loadToken === read.visit
          && panel._cacheGeneration === read.generation);
      }
    },
  });
}
