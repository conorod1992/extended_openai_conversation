import {expect,test} from "@playwright/test";
import {fixtureUrl,trackPageErrors,expectHarnessClean} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

for(const bundled of [false,true]) for(const kind of ["memory","knowledge"]) for(const later of ["Newer B",""]) {
  test(`${kind} acknowledges A while retaining ${later ? "later B" : "an original-value reversion"} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl(`data-memory/${kind === "memory" ? "memories" : "knowledge"}`,bundled ? "&bundle=1" : ""));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator(kind === "memory" ? "#add-memory" : "#add-source")).toBeVisible();
    await panel.evaluate((host,kind)=>{
      host.hass={...host.hass,user:{id:"admin",is_admin:true}};
      const call=host.hass.callWS.bind(host.hass);
      window.baselineWrites=0;
      host.hass={...host.hass,callWS:async request=>{
        if(request.section !== (kind === "memory" ? "memories" : "knowledge") || request.action !== (kind === "memory" ? "add" : "create"))return call(request);
        baselineWrites++;return new Promise(resolve=>{window.finishBaselineSave=async()=>resolve(await call(request));});
      }};
    },kind);
    await panel.locator(kind === "memory" ? "#add-memory" : "#add-source").click();
    if(kind === "knowledge")await panel.locator("#knowledge-title").fill("Acknowledged baseline");
    const content=panel.locator(`#${kind}-content`),dialog=panel.locator(`#${kind}-dialog`);
    await content.fill("Submitted A");await panel.locator(`#${kind}-save`).click();
    await expect.poll(()=>page.evaluate(()=>typeof finishBaselineSave)).toBe("function");
    await content.fill(later);await page.evaluate(()=>finishBaselineSave());
    await expect(panel.locator(`#${kind}-save`)).toBeEnabled();
    await expect(content).toHaveValue(later);
    await dialog.locator(".dialog-header .close-editor").click();
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open",true);
    await panel.locator("#confirm-cancel").click();await expect(content).toHaveValue(later);
    await content.fill("Submitted A");await dialog.locator(".dialog-header .close-editor").click();
    await expect(dialog).toHaveJSProperty("open",false);
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open",false);
    expect(await page.evaluate(()=>baselineWrites)).toBe(1);
    await expectHarnessClean(page,errors);
  });
}

for (const [kind, limit] of [["persistent", 1000], ["temporary", 500]]) {
  test(`${kind} Memory retains pasted Unicode text through validation and save`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, kind, 1, true);
    const temporary = kind === "temporary";
    const editor = temporary ? "temporary-memory" : "memory";
    await panel.evaluate(async (host, {temporary, limit}) => {
      if (temporary) {
        dataCollectionBackend.state.temporary[0].expires_at = new Date(Date.now() + 86400000).toISOString();
        await host._loadSection(true);
      }
      const send = host.hass.callWS.bind(host.hass);
      window.submittedMemory = [];
      host.hass.callWS = async request => {
        if (request.section === "memories" && request.action === (temporary ? "temporary_update" : "add")) {
          submittedMemory.push({content:request.content, category:request.category});
          if ([...request.content].length > limit) {
            throw {code:"eoai_mutation_rejected", message:`Memory exceeds ${limit} characters`};
          }
        }
        return send(request);
      };
    }, {temporary, limit});
    await panel.locator(temporary ? "button.edit-temporary-memory" : "#add-memory").first().click();
    const content = panel.locator(`#${editor}-content`);
    const category = panel.locator(`#${editor}-category`);
    const fact = `📍${"x".repeat(limit - 6)} TAIL`;
    const label = `📍${"a".repeat(62)}z`;
    await content.fill("");
    await content.focus();
    await page.keyboard.insertText(fact + "!");
    await category.fill("");
    await category.focus();
    await page.keyboard.insertText(label);
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-error`)).toContainText("characters");
    await expect(content).toHaveValue(fact + "!");
    await expect(category).toHaveValue(label);
    await content.focus();
    await page.keyboard.press("End");
    await page.keyboard.press("Backspace");
    await expect(content).toHaveValue(fact);
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-dialog`)).toHaveJSProperty("open", false);
    expect(await page.evaluate(() => submittedMemory)).toEqual([
      {content:fact + "!", category:label}, {content:fact, category:label},
    ]);
    await expectHarnessClean(page, errors);
  });
}
