const visits = new WeakMap();

export function settleSecondaryRead(pending) {
  return pending.then(
    (value) => ({status: "fulfilled", value}),
    (reason) => ({status: "rejected", reason}),
  );
}

// Keep only the pending reads for the mounted visit. Restart a read against
// the new cache generation without reloading the route or accepting old data.
export function trackSecondaryRead(panel, ownsVisit, pending, read, publish) {
  const token = panel._loadToken;
  let visit = visits.get(panel);
  if (!visit || visit.token !== token) {
    visit = {token, reads: new Set()};
    visits.set(panel, visit);
  }
  let generation;
  const attach = (request) => {
    const currentGeneration = generation = panel._cacheGeneration;
    const current = () => generation === currentGeneration
      && panel._cacheGeneration === currentGeneration && ownsVisit();
    void request.then(async (settled) => {
      if (!current()) return;
      await publish(settled, current);
      if (current()) visit.reads.delete(restart);
    });
  };
  const restart = () => {
    if (ownsVisit() && generation !== panel._cacheGeneration) attach(settleSecondaryRead(read()));
  };
  visit.reads.add(restart);
  attach(pending);
}

export function resumeSecondaryReads(panel) {
  const visit = visits.get(panel);
  if (visit && visit.token === panel._loadToken) visit.reads.forEach(restart => restart());
}

export function publishSecondaryResult(panel, key, label, settled, merge = false) {
  const result = panel._result || {};
  const load_errors = (result.load_errors || []).filter(issue => issue.key !== key);
  if (settled.status === "rejected") {
    load_errors.push({key, label, message: settled.reason?.message || String(settled.reason || "Unknown error")});
  }
  const patch = settled.status === "fulfilled" ? (merge ? settled.value : {[key]: settled.value}) : {};
  panel._result = {...result, ...patch, load_errors, loading: {...result.loading, [key]: false}};
}
