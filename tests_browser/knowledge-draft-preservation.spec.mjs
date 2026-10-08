import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Knowledge immediate save preserves prompt and name drafts through navigation, full save and reload", async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="prompt"]').fill("Preserved unsaved prompt");
  await panel.evaluate(host=>{host._draftTitle="Preserved name";host._syncConfigDirty();});
  await panel.evaluate(host=>host._navigate("data-memory","knowledge"));
  await expect(panel.locator("#knowledge-enabled-toggle")).toBeChecked();
  await panel.locator("#knowledge-enabled-toggle").uncheck();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.knowledge_enabled)).toBe(false);
  expect(await panel.evaluate(host=>({prompt:host._draft?.prompt,title:host._draftTitle,dirty:host._configDirty}))).toEqual({prompt:"Preserved unsaved prompt",title:"Preserved name",dirty:true});
  await panel.evaluate(host=>host._navigate("assistant","prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Preserved unsaved prompt");
  await panel.locator("#save-config").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.prompt)).toBe("Preserved unsaved prompt");
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.knowledge_enabled)).toBe(false);
  await page.goto(fixtureUrl("assistant/prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Preserved unsaved prompt");
  await expectHarnessClean(page,errors);
});
