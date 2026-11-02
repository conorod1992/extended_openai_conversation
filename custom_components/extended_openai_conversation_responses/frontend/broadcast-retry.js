// An attempt belongs to its submitted payload and authentication lifetime, not
// the current DOM or a later snapshot's retry window. No browser-clock policy.
export function broadcastAttempt(panel, owner, payload, retryToken) {
  const previous = panel._broadcastAttempt;
  if (previous && previous.userId === owner.userId && previous.authenticationGeneration === owner.authenticationGeneration) return previous;
  const random = new Uint8Array(16);
  globalThis.crypto.getRandomValues(random);
  const suffix = [...random].map(value => value.toString(16).padStart(2, "0")).join("");
  const attempt = {userId:owner.userId, authenticationGeneration:owner.authenticationGeneration,
    payload, operationId:retryToken ? `${retryToken}:${suffix}` : null,
    // Authentication changes revoke retry authority, but cannot certify that
    // the older send never happened. Preserve only a confirmation barrier.
    blocked:Boolean(previous?.uncertain || previous?.blocked), uncertain:false};
  panel._broadcastAttempt = attempt;
  return attempt;
}

export function sameBroadcastPayload(left, right) {
  return left.message === right.message && left.whole_home === right.whole_home
    && JSON.stringify([...left.entity_ids].sort()) === JSON.stringify([...right.entity_ids].sort());
}

export function retireBroadcastAttempt(panel, attempt) {
  if (panel._broadcastAttempt === attempt) panel._broadcastAttempt = null;
}
