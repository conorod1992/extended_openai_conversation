import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const source = await readFile(frontend("management-initial-bootstrap.js"), "utf8");
const health = await readFile(frontend("management-overview-health-clarity.js"), "utf8");

assert.match(source, /panel\?\._data !== null/);
assert.match(source, /main\.dataset\.eocInitialLoading/);
assert.match(source, /panel\._loading\?\.\(\)/);
assert.match(health, /import "\.\/management-initial-bootstrap\.js"/);

const module = await import(frontend("management-initial-bootstrap.js"));

const loadingMain = {
  innerHTML: "<div class=\"empty\">No conversation agents configured.</div>",
  dataset: {},
  attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
};
const loadingPanel = {
  _data: null,
  _loading: () => '<div class="loading">Loading…</div>',
  shadowRoot: {querySelector: (selector) => selector === "main" ? loadingMain : null},
};
assert.equal(module.showInitialLoading(loadingPanel), true);
assert.match(loadingMain.innerHTML, /Loading/);
assert.equal(loadingMain.attributes["aria-busy"], "true");
assert.equal("eocInitialLoading" in loadingMain.dataset, true);

const emptyMain = {
  innerHTML: '<div class="empty">No conversation agents configured.</div>',
  dataset: {},
  setAttribute() {},
};
const emptyPanel = {
  _data: {agents: []},
  _loading: () => '<div class="loading">Loading…</div>',
  shadowRoot: {querySelector: () => emptyMain},
};
assert.equal(module.showInitialLoading(emptyPanel), false);
assert.match(emptyMain.innerHTML, /No conversation agents configured/);
