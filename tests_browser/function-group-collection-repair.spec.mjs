import {expect,test} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

const repaired=[{id:"repaired",name:"Repaired Group",description:"Retained baseline function",loading_mode:"on_demand",functions:["baseline_tool"],enabled:true}];
async function setup(page,shape) {
  await page.goto(fixtureUrl("overview"));
  await page.waitForFunction(()=>browserHarness?.panel?._selectedAgent?.());
  await page.evaluate(async shape=>{
    const h=browserHarness,send=h.hass.callWS.bind(h.hass),initial=h.getState().configuration;
    const raw=shape==="non-list" ? 42 : shape==="missing-id" ? [{name:"Missing identifier",functions:["baseline_tool"]}]
      : shape==="numeric-id" ? [{id:42,name:"Numeric identifier",functions:["baseline_tool"]}]
      : [{id:"duplicate",name:"First",functions:["baseline_tool"]},{id:"duplicate",name:"Second",functions:[]}];
    let groups=raw,revision=initial.revision,guests=["retired"],malformed=true;
    h.groupCollectionRequests=[];h.holdGroupCollection=false;
    const metadata=()=>malformed ? {group_collection_repair:true,groups_yaml:JSON.stringify(groups,null,2),guest_allowed_group_ids:guests,
      persisted_groups:structuredClone(groups),invalid_tools:[],invalid_count:0,isolatable:false,tools_valid:true,validation_error:"Saved Group identifiers cannot be addressed individually"} : null;
    const snapshot=()=>({...structuredClone(initial),revision,config:{...structuredClone(initial.config),function_groups:malformed?[]:structuredClone(groups),guest_allowed_group_ids:structuredClone(guests)},function_repair:metadata()});
    h.groupCollectionState=()=>({groups:structuredClone(groups),functions:structuredClone(initial.config.functions),guests:structuredClone(guests)});
    h.hass.callWS=async request=>{
      if((request.section==="configuration"&&request.action==="get")||(request.section==="function_repair"&&request.action==="configuration_get")) return snapshot();
      if(request.section==="tools"&&request.action==="validate_current") return {valid:!malformed,errors:malformed?{function_groups:"Groups need repair"}:{},function_repair:metadata()};
      if(request.section==="function_repair"&&request.action==="save_groups") {
        h.groupCollectionRequests.push(structuredClone(request));
        if(request.revision!==revision) throw {code:"invalid_request",message:"Saved collection revision changed"};
        const candidate=JSON.parse(request.groups),ids=candidate.map(group=>group.id);
        if(!Array.isArray(candidate)||ids.some(id=>typeof id!=="string"||!id)||new Set(ids).size!==ids.length) throw {code:"invalid_request",message:"Group IDs must be unique strings"};
        if(request.guest_allowed_group_ids.some(id=>!ids.includes(id))) throw {code:"invalid_request",message:"Guest Group references contain an unknown Function Group"};
        groups=structuredClone(candidate);guests=structuredClone(request.guest_allowed_group_ids);malformed=false;revision+="x";
        const reply={...snapshot(),valid:true,errors:{},function_groups:structuredClone(groups)};
        if(h.holdGroupCollection) await new Promise(resolve=>{h.releaseGroupCollection=resolve;});
        return reply;
      }
      return send(request);
    };
    await h.panel._navigate("capabilities","functions");
  },shape);
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#group-collection-editor")).toBeVisible();
  await expect(panel.locator("#function-repair-editor")).toHaveCount(0);
  await expect(panel.locator(".tool-card").filter({hasText:"baseline_tool"})).toBeVisible();
  return panel;
}

for(const shape of ["non-list","missing-id","numeric-id","duplicate-id"]) {
  test(`complete Group collection repair recovers ${shape} identifiers and preserves Functions`,async({page})=>{
    const errors=trackPageErrors(page),panel=await setup(page,shape);
    const original=await panel.locator("#group-collection-editor").inputValue();
    await panel.locator("#group-collection-editor").fill("Temporary unsaved repair");
    await panel.locator("#group-collection-guests").fill("[]");
    await panel.locator("#group-collection-discard").click();
    await expect(panel.locator("#group-collection-editor")).toHaveValue(original);
    await expect(panel.locator("#group-collection-guests")).toHaveValue('[\n  "retired"\n]');
    await panel.locator("#group-collection-editor").fill(JSON.stringify(repaired,null,2));
    // Retained Guest references must be reviewed explicitly before reassignment.
    await panel.locator("#group-collection-save").click();
    await expect(panel.locator("#group-collection-error")).toContainText("unknown Function Group");
    await expect(panel.locator("#group-collection-editor")).toHaveValue(JSON.stringify(repaired,null,2));
    await panel.locator("#group-collection-guests").fill('["repaired"]');
    await panel.locator("#group-collection-save").click();
    await expect(panel.locator("#group-collection-editor")).toHaveCount(0);
    await expect(panel.locator('.function-group-card[data-group-id="repaired"]')).toContainText("Repaired Group");
    const saved=await page.evaluate(()=>browserHarness.groupCollectionState());
    expect(saved.groups).toEqual(repaired);expect(saved.guests).toEqual(["repaired"]);
    expect(saved.functions.map(tool=>tool.spec.name)).toEqual(["baseline_tool"]);
    await panel.locator('.function-group-card[data-group-id="repaired"] .edit-group').click();
    await expect(panel.locator("#group-functions input[type=checkbox]")).toBeChecked();
    await panel.locator("#group-cancel").click();
    await expectHarnessClean(page,errors);
  });
}

test("Group collection edits made during an acknowledgement remain enabled and save after initial repair",async({page})=>{
  const errors=trackPageErrors(page),panel=await setup(page,"duplicate-id");
  await panel.locator("#group-collection-editor").fill(JSON.stringify(repaired));
  await panel.locator("#group-collection-guests").fill('["repaired"]');
  await panel.evaluate(p=>p._navigate("assistant","basics"));
  await panel.evaluate(p=>p._navigate("capabilities","functions"));
  await expect(panel.locator("#group-collection-editor")).toHaveValue(JSON.stringify(repaired));
  await expect(panel.locator("#group-collection-guests")).toHaveValue('["repaired"]');
  await page.evaluate(()=>{browserHarness.holdGroupCollection=true;});
  await panel.locator("#group-collection-save").click();
  await page.waitForFunction(()=>browserHarness.releaseGroupCollection);
  const later=[{...repaired[0],name:"Authored while saving"}];
  await panel.locator("#group-collection-editor").fill(JSON.stringify(later));
  await page.evaluate(()=>{browserHarness.holdGroupCollection=false;browserHarness.releaseGroupCollection();});
  await expect(panel.locator("#group-collection-save")).toBeEnabled();
  await expect(panel.locator("#group-collection-editor")).toHaveValue(JSON.stringify(later));
  await panel.locator("#group-collection-save").click();
  await expect(panel.locator("#group-collection-editor")).toHaveCount(0);
  expect(await page.evaluate(()=>browserHarness.groupCollectionRequests.length)).toBe(2);
  expect((await page.evaluate(()=>browserHarness.groupCollectionState())).groups).toEqual(later);
  await expectHarnessClean(page,errors);
});
