// Each owner supplies only its dependencies. DOM replacement invalidates all
// owners; keyed collection reconciliation deliberately does not.
const states = new WeakMap();

export function enhancementChanged(panel, owner, dependencies = []) {
  let owners = states.get(panel);
  if (!owners) states.set(panel, owners = new Map());
  const next = [panel.shadowRoot, panel._eocShellRevision, panel._eocMainRevision, ...dependencies];
  const previous = owners.get(owner);
  if (previous && previous.length === next.length && next.every((value, index) => Object.is(value, previous[index]))) return false;
  owners.set(owner, next);
  return true;
}
