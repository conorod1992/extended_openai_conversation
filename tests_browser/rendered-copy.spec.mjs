import {expect, test} from "@playwright/test";
import {fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

for (const admin of [true, false]) {
  test(`owners render final copy synchronously (admin=${admin})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("data-memory/conversations", `&admin=${admin ? 1 : 0}`));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByRole("heading", {name: "Retained conversations"})).toBeVisible();
    const result = await page.evaluate(async () => {
      const panel = browserHarness.panel;
      const base = "/custom_components/extended_openai_conversation_responses/frontend/";
      const {renderOverview} = await import(`${base}overview-page-impl.js`);
      const {bindBroadcast} = await import(`${base}overview-broadcast.js`);
      const {renderKnowledge} = await import(`${base}management-knowledge-feature.js`);
      const {renderUsagePage} = await import(`${base}usage-chart.js`);
      const dom = markup => { const node = document.createElement("div"); node.innerHTML = markup; return node; };
      const original = panel._result;
      const conversations = [false, true].map(enabled => {
        panel._contentData = null;
        panel._result = {settings: {archive_enabled: enabled}, sessions: {sessions: []}, active: {active: [{label: "<escaped>", key: "one"}]}};
        const root = dom(panel._conversations());
        return {help: root.querySelector("p.help")?.textContent, first: root.firstElementChild?.tagName,
          notices: [...root.querySelectorAll("section.notice")].filter(n => /Conversation archive/.test(n.textContent)).length,
          escaped: !root.querySelector("escaped")};
      });
      const knowledge = [0, 1, 2].map(count => {
        panel._result = {sources: Array.from({length: count}, (_, i) => ({source_id: String(i), title: "<source>"}))};
        const root = dom(renderKnowledge(panel));
        return {heading: root.querySelector(".section-heading h2").textContent, count: root.querySelector("[data-source-count]").textContent, escaped: !root.querySelector("source")};
      });
      panel._result = original;
      const usage = dom(renderUsagePage(panel));
      const overview = await Promise.all([false, true].map(async enabled => {
        const root = dom(renderOverview(panel, panel._selectedAgent()));
        const snapshot = {enabled, can_manage: panel._data.is_admin, catalog: {}, history: []};
        await bindBroadcast(
          {shadowRoot: root, _e: panel._e, _titleCase: panel._titleCase.bind(panel), _viewKey: () => "overview", _hass: {callWS: async () => snapshot}},
          Promise.resolve(snapshot),
        );
        return {intro: root.querySelector(".broadcast-heading p")?.textContent,
          state: root.querySelector(".broadcast-toggle-row p")?.textContent,
          footnote: root.querySelector(".setup-health-footnote")?.textContent.trim(),
          icon: root.querySelector(".setup-health-footnote ha-icon")?.getAttribute("aria-hidden")};
      }));
      panel._result = original;
      return {conversations, knowledge, overview, usage: {title: usage.textContent.includes("Manage usage history"),
        retentionNotice: usage.textContent.includes("Retention is available"),
        cached: usage.querySelector(".chart-note").textContent, bold: usage.querySelector(".chart-note strong").textContent,
        totals: usage.textContent.includes("Daily, monthly, selected-period, and lifetime aggregates are never removed")}};
    });
    for (const conversation of result.conversations) {
      expect(conversation).toMatchObject({notices: 0, escaped: true, first: admin ? "P" : "SECTION"});
      expect(conversation.help).toBe(admin ? "Recent context lets conversations continue; saved history is the archive you can review or search." : undefined);
    }
    expect(result.knowledge).toEqual([0, 1, 2].map(count => ({heading: "Sources", count: `${count} source${count === 1 ? "" : "s"}`, escaped: true})));
    expect(result.usage).toMatchObject({title: admin, totals: admin, retentionNotice: false, bold: "Cached input"});
    expect(result.usage.cached).toBe("Cached input is input recognised as cached by the provider. It is included in total tokens and may be billed at a lower rate.");
    for (const overview of result.overview) {
      expect(overview.intro).toBe("Send a spoken message to selected Assist satellites or the whole home. Busy satellites wait until they are free.");
      expect(overview.footnote).toBe("Connection tests only run when you start one from Diagnostics.");
      expect(overview.icon).toBe("true");
    }
    expect(result.overview.map(o => o.state)).toEqual(["Broadcast is currently off.", undefined]);
    await expectHarnessClean(page, errors);
  });
}
