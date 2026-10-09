import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires genuine HA management backend");
const fixture = route => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}&bundle=1`;
const open = async (page, route) => {
  await page.goto(fixture(route));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toBeVisible();
  return panel;
};

test("tool creation, group assignment, schema edit and rule reference remain coherent", async ({page}) => {
  const errors = trackPageErrors(page);
  let panel = await open(page, "capabilities/functions");
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await panel.locator("#tool-yaml").fill(browserToolYaml("Dependent sequence tool V1"));
  await panel.locator("#tool-save").click();
  await expect(panel.locator(".tool-card").filter({hasText:"browser_tool"})).toContainText("Dependent sequence tool V1");
  await panel.locator("#function-add").click();
  await panel.locator("#add-group").click();
  await panel.locator("#group-name").fill("Dependent group");
  await panel.locator("#group-id").fill("dependent-group");
  await panel.locator('#group-functions input[value="browser_tool"]').check();
  await panel.locator("#group-save").click();
  await expect(panel.locator('.function-group-card[data-group-id="dependent-group"]')).toBeVisible();

  panel = await open(page, "capabilities/request-rules");
  await panel.getByRole("button", {name:"Create rule",exact:true}).first().click();
  await panel.locator("#rule-name").fill("Dependent rule");
  await panel.locator("#rule-phrases").fill("execute dependent");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-save").click();
  await expect(panel.locator(".request-rule-card").filter({hasText:"Dependent rule"})).toBeVisible();

  panel = await open(page, "capabilities/functions");
  const tool = panel.locator(".tool-card").filter({hasText:"browser_tool"}).first();
  await tool.locator(".edit-tool").click();
  await panel.locator("#tool-yaml").fill(browserToolYaml("Dependent sequence tool V2"));
  await panel.locator("#tool-save").click();
  await expect(tool).toContainText("Dependent sequence tool V2");
  const updated = await panel.evaluate(async host => ({
    configuration: await host._call("configuration", "get"),
    groups: await host._call("function_groups", "list"),
  }));
  expect(updated.configuration.config.functions.some(item =>
    item.spec.name === "browser_tool" && item.spec.description === "Dependent sequence tool V2"
  )).toBeTruthy();
  expect(JSON.stringify(updated.groups)).toContain("dependent-group");
  panel = await open(page, "capabilities/request-rules");
  const rule = panel.locator(".request-rule-card").filter({hasText:"Dependent rule"});
  await expect(rule).toBeVisible();
  await rule.locator(".rule-edit").click();
  await expect(panel.locator("#rule-name")).toHaveValue("Dependent rule");
  await panel.locator("#rule-dialog").evaluate(dialog => dialog.close());
  await expectHarnessClean(page, errors);
});

test("unsaved prompt draft cannot overwrite saved independent memory mutation", async ({page}) => {
  const errors = trackPageErrors(page);
  let panel = await open(page, "assistant/prompt-context");
  const original = await panel.evaluate(async host => (await host._call("configuration", "get")).config.prompt);
  const draft = "Uncommitted prompt from dependent journey";
  await panel.locator('[data-config="prompt"]').fill(draft);
  // Navigate in the still-mounted panel so the draft and committed data coexist.
  await panel.evaluate(host => host._navigate("data-memory", "memories"));
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Saved while prompt remains unsaved");
  await panel.locator("#memory-category").fill("cross-feature");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Saved while prompt remains unsaved",{exact:true})).toBeVisible();
  const saved = await panel.evaluate(async host => ({
    config: await host._call("configuration","get"),
    memories: await host._call("memories","list",{scope_id:"__anonymous__"}),
  }));
  expect(saved.config.config.prompt).toBe(original);
  expect(saved.config.config.prompt).not.toBe(draft);
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue(draft);
  const after = await panel.evaluate(async host => (await host._call("configuration","get")).config.prompt);
  expect(after).toBe(original);
  await expectHarnessClean(page, errors);
});

test("browser memory edit detects external backend mutation instead of overwriting it", async ({page}) => {
  const errors = trackPageErrors(page);
  let panel = await open(page, "data-memory/memories");
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Memory origin for external update");
  await panel.locator("#memory-category").fill("cross-feature");
  await panel.locator("#memory-save").click();
  const card = panel.locator(".list-card").filter({hasText:"Memory origin for external update"});
  await expect(card).toBeVisible();
  await card.locator(".memory-edit-button").click();
  const snapshot = await panel.evaluate(async host => await host._call("memories","list",{scope_id:"__anonymous__"}));
  const memory = snapshot.memories.find(row => row.content === "Memory origin for external update");
  expect(memory).toBeTruthy();
  // A second actor writes through the actual HA management backend while editor is open.
  const result = await panel.evaluate(async (host, data) =>
    host._call("memories","update",{scope_id:"__anonymous__",memory_id:data.memory_id,content:"Externally updated memory"}),
  memory);
  expect(JSON.stringify(result)).toContain("Externally updated memory");
  await panel.locator("#memory-content").fill("Stale browser attempt");
  await panel.locator("#memory-save").click();
  const final = await panel.evaluate(async host =>
    host._call("memories","list",{scope_id:"__anonymous__"}));
  const current = final.memories.find(row => row.memory_id === memory.memory_id);
  expect(current).toBeTruthy();
  // An editor based on an older revision must not silently overwrite newer data.
  expect(current.content).toBe("Externally updated memory");
  await expectHarnessClean(page, errors);
});

test("assistant A to B to A retains isolated persisted configuration", async ({page}) => {
  const errors = trackPageErrors(page);
  let panel = await open(page, "assistant/basics");
  const options = await panel.locator("#agent option").evaluateAll(nodes => nodes.map(node => node.value).filter(Boolean));
  test.skip(options.length < 2, "backend fixture has only one assistant; multi-agent shell lane required");
  const [a,b] = options;
  await panel.locator("#agent").selectOption(a);
  const originalA = await panel.evaluate(async host => (await host._call("configuration","get")).config.__title);
  await panel.locator("#agent").selectOption(b);
  const originalB = await panel.evaluate(async host => (await host._call("configuration","get")).config.__title);
  await panel.locator('[data-config="__title"]').fill("Dependent B title");
  await panel.getByRole("button",{name:"Save changes",exact:true}).click();
  await expect(panel.getByText("Unsaved changes",{exact:true})).toHaveCount(0);
  await panel.evaluate(host => host._navigate("data-memory","memories"));
  await expect(panel.locator("#add-memory")).toBeVisible();
  await panel.evaluate(host => host._navigate("capabilities","request-rules"));
  await expect(panel.getByRole("button",{name:"Create rule",exact:true}).first()).toBeVisible();
  await panel.locator("#agent").selectOption(a);
  await panel.evaluate(host => host._navigate("assistant","basics"));
  const actualA = await panel.evaluate(async host => (await host._call("configuration","get")).config.__title);
  expect(actualA).toBe(originalA);
  await panel.locator("#agent").selectOption(b);
  const actualB = await panel.evaluate(async host => (await host._call("configuration","get")).config.__title);
  expect(actualB).toBe("Dependent B title");
  expect(originalB).toBeDefined();
  await expectHarnessClean(page, errors);
});
