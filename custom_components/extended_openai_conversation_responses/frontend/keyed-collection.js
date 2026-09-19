// Small DOM bookkeeping shared only by Functions and Request Rules. Signatures
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
    record = {node: elementFromMarkup(render())};
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
