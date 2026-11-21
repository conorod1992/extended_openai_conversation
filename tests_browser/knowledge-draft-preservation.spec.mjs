import {expect, test} from "@playwright/test";
import {exerciseConfigurationDraftTransition, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Knowledge accepts a full-size Unicode paste without dropping text", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-source").click();
  const title = "a".repeat(120) + "\u0085";
  const description = "b".repeat(500) + "\u001f";
  await panel.locator("#knowledge-title").fill(title);
  await panel.locator("#knowledge-description").fill(description);
  const content = panel.locator("#knowledge-content");
  await content.focus();
  const pasted = "a" + " ".repeat(39999) + "a".repeat(59997) + "😀Z!";
  await page.keyboard.insertText(pasted);
  expect(await content.inputValue()).toBe(pasted);

  await page.keyboard.insertText("b");
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-error")).toContainText("Content must be at most");
  await content.press("End");
  await content.press("Backspace");
  await page.keyboard.insertText("\u0085");
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
  const saved = await page.evaluate(() => browserHarness.calls.find(
    call => call.section === "knowledge" && call.action === "create"
  ));
  expect(saved).toMatchObject({title, description, content:pasted + "\u0085"});
  await expectHarnessClean(page, errors);
});

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


for (const seed of [7319, 20261008]) {
  test(`display, draft and persistence agree through failed save and reload seed=${seed}`, async ({page}, testInfo) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/prompt-context"));
    try {
      await exerciseConfigurationDraftTransition(page, `Draft transition ${seed}`);
      await expectHarnessClean(page, errors);
    } finally {
      await testInfo.attach("configuration-transition", {
        body: JSON.stringify({seed, state:await page.evaluate(() => window.browserHarness?.getState() || {url:location.href})}),
        contentType:"application/json",
      });
    }
  });
}
