import {expect,test} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

for(const [width,height] of [[390,844],[375,667]]) {
  test(`Function Group footer stays visible while its body scrolls on ${width}x${height}`,async({page})=>{
    for(const bundled of [false,true]) {
    const errors=trackPageErrors(page);await page.setViewportSize({width,height});
    await page.goto(fixtureUrl("capabilities/functions",bundled ? "&bundle=1" : ""));
    const panel=page.locator("extended-openai-management-panel");
    await panel.locator('.function-group-card[data-group-id="baseline-group"] .edit-group').click();
    const dialog=panel.locator("#group-dialog"),body=dialog.locator(".group-dialog-body");
    await expect(dialog).toHaveJSProperty("open",true);
    const check=async()=>{
      const bounds=await dialog.evaluate(dialog=>{
        const d=dialog.getBoundingClientRect(),save=dialog.querySelector("#group-save").getBoundingClientRect(),cancel=dialog.querySelector("#group-cancel").getBoundingClientRect(),body=dialog.querySelector(".group-dialog-body");
        return {top:d.top,bottom:d.bottom,save:{top:save.top,bottom:save.bottom},cancel:{top:cancel.top,bottom:cancel.bottom},scroll:body.scrollHeight>body.clientHeight};
      });
      expect(bounds.top).toBeGreaterThanOrEqual(0);expect(bounds.bottom).toBeLessThanOrEqual(height);
      for(const b of [bounds.save,bounds.cancel]) {expect(b.top).toBeGreaterThanOrEqual(bounds.top);expect(b.bottom).toBeLessThanOrEqual(bounds.bottom);}
      expect(bounds.scroll).toBe(true);
    };
    await check();
    const b=await body.boundingBox();await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.mouse.wheel(0,600);
    await expect.poll(()=>body.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);await check();
    await panel.locator("#group-name").focus();await page.keyboard.press("PageDown");await check();
    await panel.locator("#group-name").fill("Mobile edited group");
    await panel.locator("#group-save").click();await expect(dialog).toHaveJSProperty("open",false);
    await expect(panel.locator('.function-group-card[data-group-id="baseline-group"]')).toContainText("Mobile edited group");
    await expectHarnessClean(page,errors);
    }
  });
}
