import {test,expect} from "@playwright/test";
import {fixtureUrl,trackPageErrors,expectHarnessClean} from "./browser-helpers.mjs";
for(const bundle of [false,true]) for(const succeeds of [false,true]) test(`modal clipboard fallback (${bundle}, ${succeeds})`,async({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/prompt-context",bundle?"&bundle=1":""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#preview-request")).toBeVisible();
  await panel.evaluate((host,succeeds)=>{
    const call=host._call.bind(host);
    host._call=(section,action,...args)=>action==="request_preview"?Promise.resolve({sections:[{label:"System prompt",content:"Clipboard regression",character_count:20}],total_character_count:20}):call(section,action,...args);
    Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:async()=>{throw Error("denied");}}});
    window.copied=[];
    document.execCommand=command=>{
      const textarea=host.shadowRoot.activeElement;
      window.clipboardTemporary=textarea;
      if(command!=="copy"||textarea?.closest("dialog")?.id!=="prompt-preview-dialog")throw Error("fallback outside modal");
      copied.push(textarea.value);return succeeds;
    };
  },succeeds);
  await panel.locator("#preview-request").click();
  const copy=panel.locator("#copy-prompt-preview");
  await copy.click();
  await expect(panel.locator("#toast")).toContainText(succeeds?"Effective request copied":"Unable to copy request");
  expect(await page.evaluate(()=>copied)).toEqual(["## System prompt\nClipboard regression"]);
  await expect(panel.locator("#prompt-preview-dialog")).toHaveJSProperty("open",true);
  expect(await page.evaluate(()=>clipboardTemporary.isConnected)).toBe(false);
  await expect(copy).toBeFocused();
  await expectHarnessClean(page,errors);
});
for(const bundle of [false,true]) test(`debug uses shared clipboard (${bundle})`,async({page})=>{
  await page.goto(fixtureUrl("usage-maintenance/request-debug",bundle?"&bundle=1":""));
  const debug=page.locator("extended-openai-debug-panel");
  await expect(debug.locator("#enabled")).toBeVisible();
  expect(await debug.evaluate(async host=>{
    Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:async()=>{throw Error("denied");}}});
    let value;
    document.execCommand=()=>{value=document.querySelector("textarea[readonly]")?.value;return true;};
    await host._copyText("Debug regression");
    if(document.querySelector("textarea[readonly]"))throw Error("clipboard leaked");
    return value;
  })).toBe("Debug regression");
});
