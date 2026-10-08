import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";
import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";
import {auditResponsive} from "./responsive-layout-audit.mjs";

const cases = [
  {route:"assistant/basics", controls:["#agent"]},
  {route:"data-memory/knowledge", controls:["#agent", "#add-source"]},
  {route:"data-memory/memories", controls:["#agent", "#add-memory"]},
];

for (const {route, controls} of cases) {
  test(`desktop-visible actions survive responsive widths: ${route}`, async ({page}, testInfo) => {
    test.setTimeout(90000);
    await page.setViewportSize({width:1280,height:900});
    await page.goto(fixtureUrl(route));
    await waitForManagementRouteReady(page, {name:route,path:route}, 30000);
    const desktop = await auditResponsive(page, controls);
    const baseline = desktop.controls.filter(item => item.rendered);
    expect(baseline.map(item => item.selector)).toEqual(controls);

    const reports = [desktop];
    for (const width of [1024, 768, 480, 390, 320]) {
      await page.setViewportSize({width,height:900});
      await expect(page.locator("extended-openai-management-panel #agent")).toBeAttached();
      reports.push(await auditResponsive(page, controls, {baseline}));
    }
    await testInfo.attach("responsive-layout-audit", {
      body:JSON.stringify({route,reports},null,2),contentType:"application/json"
    });
    expect(reports.flatMap(report => report.failures), JSON.stringify(reports,null,2)).toEqual([]);
  });
}

test("detector rejects lost actions and colliding independent controls", async ({page}) => {
  await page.setViewportSize({width:1280,height:800});
  await page.setContent(`<button id="save">Save</button>
    <div id="siblings"><button id="a">One</button><button id="b">Two</button></div>`);
  const selectors=["#save"];
  const desktop=await auditResponsive(page,selectors);
  expect(desktop.controls[0].rendered).toBe(true);
  await page.setViewportSize({width:390,height:800});
  await page.addStyleTag({content:`
    #save {visibility:hidden}
    #siblings {position:relative}
    #a, #b {position:absolute;top:0;left:0;width:100px;height:40px}
  `});
  const observed=await auditResponsive(page,selectors,{baseline:desktop.controls});
  expect(observed.failures.map(f => f.kind)).toContain("lost-visible-control");
  expect(observed.failures.map(f => f.kind)).toContain("sibling-control-overlap");
});
