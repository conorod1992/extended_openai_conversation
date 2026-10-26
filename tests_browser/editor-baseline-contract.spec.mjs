import {expect,test} from "@playwright/test";
import {fixtureUrl,trackPageErrors,expectHarnessClean} from "./browser-helpers.mjs";

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
