// A mounted editor belongs to its original HA user. Keep its authoring nodes
// out of the document during a temporary identity change, rather than letting
// another account adopt them. A different account opens a fresh panel by reload.
const presentations = new WeakMap();
const reads = new WeakMap();

export function updateAuthenticationPresentation(panel, previousUser = null) {
  const userId = panel.hass?.user?.id;
  let state = presentations.get(panel);
  const owner = previousUser ?? userId;
  if (!state && owner != null) presentations.set(panel, state = {userId:owner, nodes:null, notice:null});
  panel._eocPresentationOwnerId = state?.userId;
  const blocked = Boolean(state && state.userId !== userId);
  panel._eocPresentationBlocked = blocked;
  const root = panel.shadowRoot;
  if (!state || !root?.childNodes || typeof document === "undefined") return;
  if (blocked) {
    if (!state.nodes) {
      state.nodes = [...root.childNodes].filter(node => !node.matches?.("style,link[rel=stylesheet]"));
      state.nodes.forEach(node => node.remove());
    }
    if (!state.notice) {
      const notice = document.createElement("section");
      notice.setAttribute("role", "status");
      notice.dataset.eocAuthenticationNotice = "";
      const message = document.createElement("p");
      message.textContent = "Your Home Assistant account changed. Reload to open settings for this account. Unsaved work remains with its original account.";
      const reload = document.createElement("button");
      reload.type = "button";
      reload.textContent = "Reload settings";
      reload.addEventListener("click", () => window.location.reload());
      notice.append(message, reload);
      state.notice = notice;
      root.append(notice);
    }
  } else if (state.nodes) {
    state.notice?.remove();
    root.append(...state.nodes);
    state.nodes = state.notice = null;
  }
}

export function captureAuthentication(panel) {
  const userId = panel.hass?.user?.id, generation = panel._eocAuthenticationGeneration || 0;
  return () => !panel._eocPresentationBlocked && panel.hass?.user?.id === userId
    && (panel._eocAuthenticationGeneration || 0) === generation;
}

export function ownAuthenticationRead(panel, promise) {
  reads.set(promise, captureAuthentication(panel));
  // The route can retire before its lazy binder attaches. Observe rejection
  // now without changing the original promise's eventual success/error result.
  promise.catch(() => {});
  return promise;
}

export function authenticationReadIsCurrent(promise) {
  return reads.get(promise)?.() ?? true;
}
