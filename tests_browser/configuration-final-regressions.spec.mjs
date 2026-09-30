import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for(const width of [360,390])for(const route of ["usage-maintenance/usage","capabilities/quiet-hours"]){
  test(`${route} confines content to ${width}px viewport`,async({page})=>{
    await page.setViewportSize({width,height:844});
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl(route));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator(route.includes("usage")?"#usage-window":"#qh-enabled")).toBeVisible();
    if(route.includes("quiet")){
      await panel.evaluate(host=>{
        host._hass.states={"media_player.kitchen":{state:"idle",attributes:{volume_level:0.5,friendly_name:"Kitchen speaker"}}};
        host._result.satellites=[{satellite_entity_id:"assist_satellite.kitchen",name:"Kitchen speaker",media_player_entity_id:"media_player.kitchen",media_player_source:"auto",media_player_candidates:["media_player.kitchen"],wake_sound_candidates:["switch.kitchen_sound"]}];
        host._eocMainMarkup=null;host._render();
      });
      await panel.locator(".qh-satellite-config summary").first().click();
    }
    const bounds=await page.evaluate(()=>{
      const host=document.querySelector("extended-openai-management-panel");
      return {document:document.documentElement.scrollWidth,host:host.scrollWidth,viewport:innerWidth,overflow:[...host.shadowRoot.querySelectorAll("*")].map(el=>({tag:el.tagName,class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right})).filter(el=>el.right>innerWidth+1).slice(-12)};
    });
    expect(bounds.document,JSON.stringify(bounds)).toBeLessThanOrEqual(width+1);
    expect(bounds.host).toBeLessThanOrEqual(width+1);
    await expectHarnessClean(page,errors);
  });
}

test("speech replacements have named fields and correct reorder boundaries",async({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/speech"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator("#config-speech_processing_enabled").check();
  await panel.locator("#add-regex").click();
  await expect(panel.getByRole("textbox",{name:"Speech replacement 1 Pattern",exact:true})).toBeVisible();
  await expect(panel.getByRole("textbox",{name:"Speech replacement 1 Replacement",exact:true})).toBeVisible();
  await expect(panel.getByRole("button",{name:"Move rule up",exact:true})).toBeDisabled();
  await expect(panel.getByRole("button",{name:"Move rule down",exact:true})).toBeDisabled();
  await panel.locator(".regex-pattern").fill("first");
  await panel.locator("#add-regex").click();
  await panel.getByRole("textbox",{name:"Speech replacement 2 Pattern",exact:true}).fill("second");
  await expect(panel.getByRole("button",{name:"Move rule down",exact:true}).last()).toBeDisabled();
  await expect(panel.getByRole("button",{name:"Move rule up",exact:true}).first()).toBeDisabled();
  await panel.getByRole("button",{name:"Move rule up",exact:true}).last().click();
  await expect(panel.locator(".regex-pattern").first()).toHaveValue("second");
  await expect(panel.getByRole("button",{name:"Move rule down",exact:true}).last()).toBeDisabled();
  await expect(panel.getByRole("button",{name:"Move rule up",exact:true}).first()).toBeDisabled();
  await expectHarnessClean(page,errors);
});

test("effective preview failure settles its footprint",async({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#preview-request")).toBeVisible();
  await panel.evaluate(host=>{
    const original=host._call.bind(host);
    host._call=(section,action,...args)=>action==="request_preview"?Promise.reject(new Error("Invalid template")):original(section,action,...args);
  });
  await panel.locator("#preview-request").click();
  await expect(panel.locator("#request-footprint")).toHaveText("Preview unavailable");
  await expect(panel.locator("#prompt-preview-status")).toHaveText("Invalid template");
  await expect(panel.locator("#copy-prompt-preview")).toBeDisabled();
  await expectHarnessClean(page,errors);
});

test("retention explains immediate detail deletion before Save",async({page})=>{
  await page.goto(fixtureUrl("usage-maintenance/retention"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator(".card-heading")).toContainText("Saving Disabled immediately deletes existing detailed history");
  await expect(panel.locator(".card-heading")).toContainText("Aggregate usage counters remain");
});

test("group assignment no-match search can clear without changing selections",async({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-group")).toBeVisible();
  await expect(panel.locator(".function-groups-help")).toContainText("Always available groups send full tool schemas");
  await panel.locator("#add-group").click();
  const first=panel.locator("#group-functions input").first();
  await first.check();
  const selected=await first.getAttribute("value");
  await panel.getByRole("searchbox",{name:"Search functions to assign"}).fill("no-such-function-xyz");
  await expect(panel.locator("#group-functions-empty")).toBeVisible();
  await panel.locator("#group-function-clear").click();
  await expect(panel.locator("#group-functions-empty")).toBeHidden();
  await expect(first).toBeChecked();
  expect(await first.getAttribute("value")).toBe(selected);
  await expectHarnessClean(page,errors);
});

test("timeout preset and request-local tool budget have accurate names",async({page})=>{
  await page.goto(fixtureUrl("assistant/conversation"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("combobox",{name:"Conversation timeout preset",exact:true})).toBeVisible();
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(panel.getByRole("spinbutton",{name:"Tool-call limit per request",exact:true})).toBeVisible();
});
