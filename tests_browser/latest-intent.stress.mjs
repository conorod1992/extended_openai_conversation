import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";
import {installIntentGate, waitForIntents, settleIntent, assertLatestIntent} from "./latest-intent-contract.mjs";

for (const section of ["memories", "knowledge", "conversations"]) {
  for (const change of section === "knowledge" ? ["refresh", "agent", "navigation"] : ["refresh", "scope", "agent", "navigation"]) {
    for (const oldFailure of [false, true]) {
      for (const newFailure of [false, true]) {
        test(`${section} latest ${change} owns list after old ${oldFailure ? "failure" : "success"} and new ${newFailure ? "failure" : "success"}`, async ({page}, testInfo) => {
          const errors = trackPageErrors(page);
          const route = `data-memory/${section}`;
          await page.goto(fixtureUrl(route, change === "agent" ? "&agents=2" : ""));
          const panel = page.locator("extended-openai-management-panel");
          try {
          await expect(panel.locator(section === "memories" ? "#add-memory" : section === "knowledge" ? "#add-source" : "#archive-query")).toBeVisible();
          } catch (error) {
            await testInfo.attach("initial-route-diagnostics", {body:JSON.stringify(await page.evaluate(() => ({harness:browserHarness.windowErrors,rejections:browserHarness.rejections,route:location.href,error:browserHarness.panel?._error,busy:browserHarness.panel?._busy,content:browserHarness.panel?.shadowRoot?.textContent}))),contentType:"application/json"});
            throw error;
          }
          if (change === "scope") await panel.evaluate(async host => {
            const saved = await host._call("backup", "create");
            const document = JSON.parse(saved.json);
            const shared = {scope_id:"shared:household",scope_type:"shared",display_name:"Shared household",memory_count:0,conversation_count:0};
            document.state.scopes.push(shared);
            await host._call("backup", "restore", {document:JSON.stringify(document)});
            host._data.scopes.push(shared);
            host._scopeCatalogCache.clear();
            host._eocScopeCatalogTimes.clear();
          });
          await installIntentGate(page, section, ["list"]);
          await page.evaluate(() => {
            const host = browserHarness.panel;
            host._sectionCache.clear();
            window.intentOperations = [host._loadSection(true)];
          });
          await waitForIntents(page, 1);
          await page.evaluate(async ({change, section}) => {
            const host = browserHarness.panel;
            if (change === "scope") host._scopeId = "shared:household";
            if (change === "agent") {
              const current = host._selectedAgent();
              const alternate = host._data.agents.find(row => row.subentry_id !== current.subentry_id);
              host._agentId = alternate.subentry_id;
            }
            if (change === "navigation") await host._navigate("guide", "");
            host._sectionCache.clear();
            intentOperations.push(change === "navigation" ? host._navigate("data-memory", section) : host._loadSection(true));
          }, {change, section});
          await waitForIntents(page, 2);
          await settleIntent(page, 1, newFailure, "New intent");
          await page.evaluate(() => intentOperations[1]);
          const observe = () => panel.evaluate(host => { const data = host._contentData || host._result; return host._error ? {error: host._error} : {marker: data?.intentMarker || data?.sessions?.intentMarker}; });
          await expect.poll(observe).toEqual(newFailure ? {error: "New intent"} : {marker: "New intent"});
          await settleIntent(page, 0, oldFailure, "Old intent");
          await page.evaluate(() => Promise.all(intentOperations));
          const schedule = await assertLatestIntent(page, newFailure, "New intent", observe);
          await expect(page).toHaveURL(new RegExp(`/extended-openai/data-memory/${section}$|route=data-memory/${section}`));
          if (change === "scope") {
            await expect(panel.locator("#scope")).toHaveValue("shared:household");
            expect(schedule.filter(row => row.event === "requested").map(row => row.request.scope_id)).toEqual(["user:test-user", "shared:household"]);
          }
          if (change === "agent") await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
          await testInfo.attach("realised-intent-schedule", {body:JSON.stringify(schedule), contentType:"application/json"});
          await expectHarnessClean(page, errors);
        });
      }
    }
  }
}


for (const intent of ["read", "failure", "draft", "save"]) {
  for (const oldFailure of [false, true]) {
    test(`Knowledge latest ${intent} owns detail after old ${oldFailure ? "failure" : "success"}`, async ({page}, testInfo) => {
      const errors = trackPageErrors(page);
      await page.goto(fixtureUrl("data-memory/knowledge"));
      const panel = page.locator("extended-openai-management-panel");
      await expect(panel.locator("#add-source")).toBeVisible();
      const ids = await panel.evaluate(async host => {
        const rows = [];
        for (const marker of ["Old intent", "New intent"]) {
          const response = await host._call("knowledge", "create", {title: marker, description: marker, content: marker, enabled:true});
          rows.push(response.summary.source_id);
        }
        return rows;
      });
      await installIntentGate(page, "knowledge", ["get"]);
      await panel.evaluate((host, id) => { window.intentOperations = [host._openKnowledge(id)]; }, ids[0]);
      await waitForIntents(page, 1);
      await page.keyboard.press("Escape");
      await panel.evaluate((host, id) => { intentOperations.push(host._openKnowledge(id)); }, ids[1]);
      await waitForIntents(page, 2);
      await settleIntent(page, 1, intent === "failure", "New intent");
      await page.evaluate(() => intentOperations[1]);
      const content = panel.locator("#knowledge-content");
      let expected = "New intent";
      if (intent === "draft" || intent === "save") {
        await content.fill("Unsaved latest draft");
        expected = "Unsaved latest draft";
      }
      if (intent === "save") {
        await panel.locator("#knowledge-save").click();
        await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
        await panel.locator("#add-source").click();
        await content.fill("Next unsaved draft");
        expected = "Next unsaved draft";
      }
      await settleIntent(page, 0, oldFailure, "Old intent");
      await page.evaluate(() => Promise.all(intentOperations));
      const observe = () => panel.evaluate(host => ({marker:host.shadowRoot.querySelector("#knowledge-content").value}));
      const schedule = await assertLatestIntent(page, false, intent === "failure" ? "" : expected, observe);
      await expect(panel.locator("#knowledge-dialog")).toBeVisible();
      if (intent === "failure") await expect(panel.locator("#knowledge-error")).toContainText("New intent");
      else await expect(panel.locator("#knowledge-error")).toBeEmpty();
      if (intent === "save") {
        const stored = await panel.evaluate((host, id) => { intentGate.restore(); return host._call("knowledge", "get", {source_id:id}); }, ids[1]);
        expect(stored.source.content).toBe("Unsaved latest draft");
      }
      await testInfo.attach("realised-detail-schedule", {body:JSON.stringify(schedule),contentType:"application/json"});
      await expectHarnessClean(page, errors);
    });
  }
}


for (const flow of ["search", "pagination", "conversation detail"]) {
  for (const oldFailure of [false, true]) {
    test(`Archive latest ${flow} owns page after old ${oldFailure ? "failure" : "success"}`, async ({page}, testInfo) => {
      const errors = trackPageErrors(page);
      await page.goto(fixtureUrl("data-memory/conversations", "&seed_conversations=1"));
      const panel = page.locator("extended-openai-management-panel");
      await expect(panel.locator("#archive-query")).toBeVisible();
      const pageResult = marker => ({results:[{session_id:"intent-session",title:marker,timestamp:"2026-10-25T00:15:00Z"}],offset:0,limit:20,returned:1,total:60,has_more:true,next_offset:20});
      if (flow === "conversation detail") {
        await expect(panel.locator(".view-session").first()).toBeVisible();
        await installIntentGate(page, "conversations", ["get"]);
        await panel.locator(".view-session").first().click();
      } else {
        await panel.locator("#archive-query").fill("Old query");
        await installIntentGate(page, "conversations", ["search"]);
        await panel.locator("#archive-search").click();
        if (flow === "pagination") {
          await waitForIntents(page, 1);
          await settleIntent(page, 0, false, "Baseline page", pageResult("Baseline page"));
          await expect(panel.getByRole("heading", {name:"Baseline page",exact:true})).toBeVisible();
          await page.evaluate(() => { intentGate.pending = []; intentGate.schedule = []; });
          await panel.locator(".eoc-history-pager").getByRole("button", {name:"Next",exact:true}).click();
        }
      }
      await waitForIntents(page, 1);
      if (flow === "conversation detail") {
        await page.keyboard.press("Escape");
        await panel.locator(".view-session").last().click();
      } else {
        await panel.locator("#archive-query").fill("New query");
        await panel.locator("#archive-search").click();
      }
      await waitForIntents(page, 2);
      const response = marker => flow === "conversation detail" ? {session:{title:marker},turns:[{user_text:marker,assistant_text:"Visible native answer"}],offset:0,limit:20,returned:1,total:1,has_more:false} : pageResult(marker);
      await settleIntent(page, 1, false, "New intent", response("New intent"));
      const target = flow === "conversation detail" ? panel.locator("#session-title") : panel.getByRole("heading", {name:"New intent",exact:true});
      await expect(target).toHaveText("New intent");
      await settleIntent(page, 0, oldFailure, "Old intent", response("Old intent"));
      await expect(target).toHaveText("New intent");
      await expect(panel.getByText("Old intent", {exact:true})).toHaveCount(0);
      const observe = () => target.textContent().then(marker => ({marker}));
      const schedule = await assertLatestIntent(page, false, "New intent", observe);
      if (flow !== "conversation detail") await expect(panel.locator("#archive-query")).toHaveValue("New query");
      else await expect(panel.locator("#session-body")).toContainText("Visible native answer");
      await testInfo.attach("realised-archive-schedule", {body:JSON.stringify(schedule),contentType:"application/json"});
      await expectHarnessClean(page, errors);
    });
  }
}
