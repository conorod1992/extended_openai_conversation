import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const toolYaml = description => `spec:\n  name: baseline_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n`;

async function openEditor(panel, group) {
  if (group) await panel.locator(".edit-group").first().click();
  else {
    if (!await panel.locator(".edit-tool").first().isVisible()) await panel.getByText("Show member functions", {exact:true}).click();
    await panel.locator(".edit-tool").first().click();
    await expect(panel.locator("#tool-save")).toBeEnabled();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
  }
}

for (const bundled of [false, true]) for (const group of [false, true]) for (const readAfterCommit of [false, true]) for (const failReread of readAfterCommit ? [false, true] : [false]) {
  const version = bundled ? "bundle" : "source";
  const kind = group ? "Group" : "Tool";
  test(`${kind} acknowledgement respects ${readAfterCommit ? "a newer committed configuration read" : "an older precommit configuration read"}${failReread ? " with one failed reread" : ""} (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    // A supported Function mutation naturally invalidates the full configuration
    // projection. Its next ordinary read can then settle while an editor saves.
    await panel.locator(".tool-enabled").first().uncheck();
    await expect.poll(() => panel.evaluate(host => host._configData.config.functions[0].enabled)).toBe(false);
    await openEditor(panel, group);
    const input = panel.locator(group ? "#group-description" : "#tool-yaml");
    const initialDraft = await input.inputValue();
    const save = panel.locator(group ? "#group-save" : "#tool-save");
    const initialDescription = await panel.evaluate((host, group) => group
      ? host._configData.config.function_groups[0].description
      : host._configData.config.functions[0].spec.description, group);
    await panel.evaluate((host, {group, readAfterCommit}) => {
      const original = host.hass.callWS.bind(host.hass);
      const state = {entered:false, returned:false, writes:[], reads:0, failedReads:0, baseline:host._configData.config};
      window.lateConfigurationAck = state;
      host.hass.callWS = async message => {
        if (message.section === "configuration" && message.action === "get") {
          state.reads++;
          if (state.failNextRead) {
            state.failNextRead = false;
            state.failedReads++;
            throw new Error("Generated post-ack configuration read failure");
          }
        }
        if (message.section === "tools" && message.action === (group ? "save_group" : "save")) {
          state.writes.push(structuredClone(message));
          if (state.writes.length === 1) {
            // Both histories use the actual fixture persistence implementation.
            // Only acknowledgement/delivery order differs.
            const committed = readAfterCommit ? await original(message) : null;
            state.entered = true;
            await new Promise(resolve => {state.release = resolve;});
            state.returned = true;
            return readAfterCommit ? committed : original(message);
          }
        }
        return original(message);
      };
    }, {group, readAfterCommit});
    await input.fill(group ? "Submitted version A" : toolYaml("Submitted version A"));
    await save.click();
    await expect.poll(() => page.evaluate(() => window.lateConfigurationAck.entered)).toBe(true);
    const newerDraft = failReread ? initialDraft : group ? "Unsaved editor B" : toolYaml("Unsaved editor B");
    await input.fill(newerDraft);

    if (readAfterCommit) await panel.evaluate(async (host, group) => {
      // An independent client has no access to this editor's frontend queue.
      // Commit C through the same strict fixture service using its actual saved
      // revision, then let the production route loader accept a fresh read.
      const state = browserHarness.getState().configuration;
      const agent = host._selectedAgent();
      await host.hass.callWS({type:"extended_openai_conversation_responses/management",
        section:"tools", action:group ? "save_group" : "save",
        entry_id:agent.entry_id, subentry_id:agent.subentry_id, revision:state.revision,
        ...(group ? {group:{...state.config.function_groups[0], description:"Newer committed C"}, original_id:state.config.function_groups[0].id}
          : {yaml:"spec:\n  name: baseline_tool\n  description: Newer committed C\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n", original_name:"baseline_tool"}),
      });
    }, group);
    await panel.evaluate(host => host._loadSection(true));
    expect(await page.evaluate(() => window.lateConfigurationAck.reads)).toBe(1);
    expect(await panel.evaluate(host => host._configData.config !== window.lateConfigurationAck.baseline)).toBe(true);
    const description = () => panel.evaluate((host, group) => group
      ? host._configData.config.function_groups[0].description
      : host._configData.config.functions[0].spec.description, group);
    expect(await description()).toBe(readAfterCommit ? "Newer committed C" : initialDescription);
    const acceptedRevision = await panel.evaluate(host => host._configData.revision);

    await page.evaluate(failReread => {
      window.lateConfigurationAck.failNextRead = failReread;
      window.lateConfigurationAck.release();
    }, failReread);
    await expect.poll(() => page.evaluate(() => window.lateConfigurationAck.returned)).toBe(true);
    await expect(save).toBeEnabled();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const expectedDescription = readAfterCommit ? "Newer committed C" : "Submitted version A";
    await expect.poll(description).toBe(expectedDescription);
    const persisted = await page.evaluate(() => browserHarness.getState().configuration);
    expect(await panel.evaluate(host => host._configData.revision)).toBe(persisted.revision);
    if (readAfterCommit) expect(persisted.revision).toBe(acceptedRevision);
    expect(group ? persisted.config.function_groups[0].description : persisted.config.functions[0].spec.description).toBe(expectedDescription);
    await expect(input).toHaveValue(newerDraft);
    await expect(panel.locator(group ? "#group-dialog" : "#tool-dialog")).toHaveJSProperty("open", true);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    expect(await page.evaluate(() => window.lateConfigurationAck.writes.length)).toBe(readAfterCommit ? 2 : 1);
    if (failReread) {
      expect(await page.evaluate(() => window.lateConfigurationAck.failedReads)).toBe(1);
      await expect(panel.locator("#toast")).toContainText("Generated post-ack configuration read failure");
      await panel.evaluate(host => host._loadSection(true));
      await expect(input).toHaveValue(newerDraft);
      expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
      expect(await description()).toBe(expectedDescription);
    }

    // An explicit discard may close B. The current persisted definition must
    // reopen normally, rather than failing a serialize call with A's old revision.
    await panel.locator(group ? "#group-cancel" : "#tool-cancel").click();
    await openEditor(panel, group);
    await expect(panel.locator(group ? "#group-save" : "#tool-save")).toBeEnabled();
    if (group) await expect(panel.locator("#group-description")).toHaveValue(expectedDescription);
    else await expect(panel.locator("#tool-yaml")).toHaveValue(new RegExp(expectedDescription));
    expect(await page.evaluate(() => window.lateConfigurationAck.writes.length)).toBe(readAfterCommit ? 2 : 1);
    await expectHarnessClean(page, diagnostics);
  });
}
