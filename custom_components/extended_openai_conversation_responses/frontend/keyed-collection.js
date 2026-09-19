// Small DOM bookkeeping shared by route-owned persistent collections. Signatures
// describe rendered output, never editable state; callers always read panel data.
export function elementFromMarkup(markup) {
  const template = document.createElement("template");
  template.innerHTML = markup;
  return template.content.firstElementChild;
}

export function adoptKeyedElements(root, selector, key) {
  return new Map([...root.querySelectorAll(selector)].map(node => [node.dataset[key], {node}]));
}

export function keyedElement(records, key, signature, render) {
  let record = records.get(key);
  // An adopted node was just rendered from the same authoritative state.
  if (!record || (record.signature !== undefined && record.signature !== signature)) {
    const rendered = render();
    record = {node: typeof rendered === "string" ? elementFromMarkup(rendered) : rendered};
    records.set(key, record);
  }
  record.signature = signature;
  return record;
}

export function placeChildren(parent, nodes) {
  const wanted = new Set(nodes);
  for (const child of [...parent.children]) if (!wanted.has(child)) child.remove();
  let next = parent.firstElementChild;
  for (const node of nodes) {
    if (node === next) next = next.nextElementSibling;
    else parent.insertBefore(node, next);
  }
}

export function pruneKeys(records, keys) {
  for (const key of records.keys()) if (!keys.has(key)) records.delete(key);
}

export function setText(node, text) {
  if (node && node.textContent !== text) node.textContent = text;
}

export function setAttribute(node, name, value) {
  if (node.getAttribute(name) !== String(value)) node.setAttribute(name, value);
}

// Callers retain their own data/identity boundaries. This only places keyed DOM.
export function reconcileKeyedChildren(parent, records, items, key, signature, render, tail = []) {
  const keys = new Set();
  const nodes = items.map(item => {
    const id = String(key(item));
    keys.add(id);
    return keyedElement(records, id, signature(item), () => render(item)).node;
  });
  pruneKeys(records, keys);
  placeChildren(parent, [...nodes, ...tail]);
  return nodes;
}

const delegatedHosts = new WeakSet();
export function delegateCollectionActions(host, selector, action) {
  if (!host || delegatedHosts.has(host)) return;
  delegatedHosts.add(host);
  const dispatch = event => {
    const control = event.target.closest?.(selector);
    if (!control || !host.contains(control) || control.disabled) return;
    if (event.type === "keydown") {
      // Native buttons already synthesize click for keyboard activation.
      if (control.tagName === "BUTTON" || !["Enter", " "].includes(event.key)) return;
      event.preventDefault();
    }
    action(control, event);
  };
  host.addEventListener("click", dispatch);
  host.addEventListener("keydown", dispatch);
}
