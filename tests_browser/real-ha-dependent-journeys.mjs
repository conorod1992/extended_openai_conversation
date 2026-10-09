import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const dependentToolYaml = version => `spec:
  name: dependent_journey_tool
  description: Dependent sequence tool ${version}
  parameters:
    type: object
    properties: {}
function:
  type: template
  value_template: "${version}"
`;

const fixture = route => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}&bundle=1`;
const open = async (page, route) => {
  await page.goto(fixture(route));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toBeVisible();
  await expect.poll(() => panel.evaluate(host => host._currentRouteUsableForSpeculation())).toBe(true);
  await panel.locator("#agent").selectOption({label: "Dependent Journey Conversation"});
  await expect.poll(() => panel.evaluate(host => host._currentRouteUsableForSpeculation())).toBe(true);
  await expect(panel.locator("#agent")).toHaveValue(await panel.evaluate(host => host._agentId));
  return panel;
};

export function registerDependentJourneys() {
  test("tool creation, group assignment, schema edit and rule reference remain coherent", async ({page}) => {
    const errors = trackPageErrors(page);
    let panel = await open(page, "capabilities/functions");
    await panel.locator("#function-add").click();
    await panel.locator("#add-tool").click();
    await panel.locator("#tool-yaml").fill(dependentToolYaml("V1"));
    await panel.locator("#tool-save").click();
    await expect(panel.locator(".tool-card").filter({hasText:"dependent_journey_tool"})).toContainText("Dependent sequence tool V1");
    await panel.locator("#function-add").click();
    await panel.locator("#add-group").click();
    await panel.locator("#group-name").fill("Dependent group");
    await panel.locator("#group-id").fill("dependent-group");
    await panel.locator("#group-description").fill("Dependent journey function catalogue");
    await panel.locator('#group-functions input[value="dependent_journey_tool"]').check();
    await panel.locator("#group-save").click();
    await expect(panel.locator('.function-group-card[data-group-id="dependent-group"]')).toBeVisible();

    panel = await open(page, "capabilities/request-rules");
    await panel.getByRole("button", {name:"Create rule",exact:true}).first().click();
    await panel.locator("#rule-name").fill("Dependent rule");
    await panel.locator("#rule-phrases").fill("execute dependent");
    await panel.locator("#rule-action-type").selectOption("local_action");
    const actions = [{action: "extended_openai_conversation_responses.call_function",
      data: {function: "dependent_journey_tool", arguments: {}, result_alias: "dependent_result", step_id: "1234567890abcdef1234567890abcdef"}}];
    await panel.locator("#rule-action-sequence-host ha-selector").evaluate((selector, value) => {
      selector.value = value;
      selector.dispatchEvent(new CustomEvent("value-changed", {detail: {value}, bubbles: true, composed: true}));
    }, actions);
    await panel.locator("#rule-success").fill("Dependent tool {dependent_result}");
    await panel.locator("#rule-save").click();
    await expect(panel.locator(".request-rule-card").filter({hasText:"Dependent rule"})).toBeVisible();

    panel = await open(page, "capabilities/functions");
    await panel.locator('.function-group-card[data-group-id="dependent-group"] summary').click();
    const tool = panel.locator(".tool-card").filter({hasText:"dependent_journey_tool"}).first();
    await tool.locator(".edit-tool").click();
    await panel.locator("#tool-yaml").fill(dependentToolYaml("V2"));
    await panel.locator("#tool-save").click();
    await expect(tool).toContainText("Dependent sequence tool V2");
    const updated = await panel.evaluate(async host => ({
      configuration: await host._call("configuration", "get"),
      rules: await host._call("request_rules", "list"),
    }));
    expect(updated.configuration.config.functions.some(item =>
      item.spec.name === "dependent_journey_tool" && item.spec.description === "Dependent sequence tool V2"
    )).toBeTruthy();
    expect(updated.configuration.config.function_groups.find(group => group.id === "dependent-group").functions).toEqual(["dependent_journey_tool"]);
    expect(updated.rules.rules.find(rule => rule.name === "Dependent rule").action.actions).toEqual(actions);
    panel = await open(page, "capabilities/request-rules");
    const rule = panel.locator(".request-rule-card").filter({hasText:"Dependent rule"});
    await expect(rule).toBeVisible();
    await rule.locator(".rule-edit").click();
    await expect(panel.locator("#rule-name")).toHaveValue("Dependent rule");
    expect(await panel.locator("#rule-action-sequence-host ha-selector").evaluate(selector => selector.value)).toEqual(actions);
    await panel.locator("#rule-name").fill("Dependent rule edited");
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    const result = await panel.evaluate(host => host._call("request_rules", "test", {text: "execute dependent", confirm: true}));
    expect(result.successful).toBe(true);
    expect(result.handled_locally).toBe(true);
    expect(result.response).toBe("Dependent tool V2");
    expect(result.intent_response.speech.plain.speech).toBe("Dependent tool V2");
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
    const scope = await panel.locator("#memory-owner").inputValue();
    await panel.locator("#memory-save").click();
    await expect(panel.getByText("Saved while prompt remains unsaved",{exact:true})).toBeVisible();
    const saved = await panel.evaluate(async (host, scope) => ({
      config: await host._call("configuration","get"),
      memories: await host._call("memories","list",{scope_id:scope}),
    }), scope);
    expect(saved.memories.memories.some(memory => memory.content === "Saved while prompt remains unsaved")).toBe(true);
    expect(saved.config.config.prompt).toBe(original);
    expect(saved.config.config.prompt).not.toBe(draft);
    await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue(draft);
    const after = await panel.evaluate(async host => (await host._call("configuration","get")).config.prompt);
    expect(after).toBe(original);
    await panel.locator("#save-config").click();
    await expect(panel.getByText("Unsaved changes", {exact:true})).toHaveCount(0);
    panel = await open(page, "assistant/prompt-context");
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue(draft);
    const final = await panel.evaluate(async (host, scope) => ({
      config: await host._call("configuration", "get"),
      memories: await host._call("memories", "list", {scope_id: scope}),
    }), scope);
    expect(final.config.config.prompt).toBe(draft);
    expect(final.memories.memories.some(memory => memory.content === "Saved while prompt remains unsaved")).toBe(true);
    await expectHarnessClean(page, errors);
  });

  test("browser memory edit detects external backend mutation instead of overwriting it", async ({page}) => {
    const errors = trackPageErrors(page);
    let panel = await open(page, "data-memory/memories");
    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill("Memory origin for external update");
    await panel.locator("#memory-category").fill("cross-feature");
    const scope = await panel.locator("#memory-owner").inputValue();
    await panel.locator("#memory-save").click();
    const card = panel.locator(".list-card").filter({hasText:"Memory origin for external update"});
    await expect(card).toBeVisible();
    await card.locator(".memory-edit-button").click();
    const snapshot = await panel.evaluate((host, scope) => host._call("memories","list",{scope_id:scope}), scope);
    const memory = snapshot.memories.find(row => row.content === "Memory origin for external update");
    expect(memory).toBeTruthy();
    // A second actor writes through the actual HA management backend while editor is open.
    const result = await panel.evaluate(async (host, data) =>
      host._call("memories","update",{scope_id:data.scope,memory_id:data.memory.memory_id,content:"Externally updated memory"}),
    {scope, memory});
    expect(JSON.stringify(result)).toContain("Externally updated memory");
    await panel.locator("#memory-content").fill("Stale browser attempt");
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-error")).toContainText("memory changed since it was loaded; reopen it before saving");
    await expect(panel.locator("#memory-content")).toHaveValue("Stale browser attempt");
    const final = await panel.evaluate((host, scope) =>
      host._call("memories","list",{scope_id:scope}), scope);
    const current = final.memories.find(row => row.memory_id === memory.memory_id);
    expect(current).toBeTruthy();
    // An editor based on an older revision must not silently overwrite newer data.
    expect(current.content).toBe("Externally updated memory");
    // Exactly one rejected stale write is expected; other errors still fail.
    const conflictUrl = `${backendUrl}?client=default`;
    await expect.poll(() => errors.consoleErrors).toEqual([
      `Failed to load resource: the server responded with a status of 400 (Bad Request) (${conflictUrl}:0)`,
    ]);
    expect(errors.badResponses).toEqual([`400 POST ${conflictUrl}`]);
    errors.consoleErrors = [];
    errors.badResponses = [];
    panel = await open(page, "data-memory/memories");
    await expect(panel.getByText("Externally updated memory", {exact:true})).toBeVisible();
    await expect(panel.getByText("Stale browser attempt", {exact:true})).toHaveCount(0);
    await expectHarnessClean(page, errors);
  });

  test("assistant A to B to A retains isolated persisted configuration", async ({page}) => {
    const errors = trackPageErrors(page);
    let panel = await open(page, "assistant/basics");
    const options = await panel.locator("#agent option").evaluateAll(nodes => nodes.map(node => node.value).filter(Boolean));
    expect(options).toHaveLength(2);
    const [a,b] = options;
    await panel.locator("#agent").selectOption(a);
    const originalA = await panel.evaluate(async host => (await host._call("configuration","get")).title);
    await panel.locator("#agent").selectOption(b);
    const originalB = await panel.evaluate(async host => (await host._call("configuration","get")).title);
    await panel.locator('[data-config="__title"]').fill("Dependent B title");
    await panel.getByRole("button",{name:"Save changes",exact:true}).click();
    await expect(panel.getByText("Unsaved changes",{exact:true})).toHaveCount(0);
    await panel.evaluate(host => host._navigate("data-memory","memories"));
    await expect(panel.locator("#add-memory")).toBeVisible();
    await panel.evaluate(host => host._navigate("capabilities","request-rules"));
    await expect(panel.getByRole("button",{name:"Create rule",exact:true}).first()).toBeVisible();
    await panel.locator("#agent").selectOption(a);
    await panel.evaluate(host => host._navigate("assistant","basics"));
    const actualA = await panel.evaluate(async host => (await host._call("configuration","get")).title);
    expect(actualA).toBe(originalA);
    await panel.locator("#agent").selectOption(b);
    const actualB = await panel.evaluate(async host => (await host._call("configuration","get")).title);
    expect(actualB).toBe("Dependent B title");
    expect(originalB).toBeDefined();
    const selectedEntry = await panel.evaluate(host => host._selectedAgent().entry_id);
    const storedOwner = await page.evaluate(() => ({
      agent: localStorage.getItem("extended-openai-agent"),
      entry: localStorage.getItem("extended-openai-agent-entry"),
    }));
    expect(storedOwner).toEqual({agent: b, entry: selectedEntry});
    await page.goto(fixture("assistant/basics"));
    await expect.poll(() => panel.evaluate(host => host._currentRouteUsableForSpeculation())).toBe(true);
    await expect(panel.locator("#agent")).toHaveValue(b);
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Dependent B title");
    expect((await panel.evaluate(host => host._call("configuration", "get"))).title).toBe("Dependent B title");
    await expectHarnessClean(page, errors);
  });
}
