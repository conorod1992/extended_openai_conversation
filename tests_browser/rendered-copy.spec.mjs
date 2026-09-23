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
        return {
          hasHelp: Boolean(root.querySelector("p.help")?.textContent.trim()),
          notices: root.querySelectorAll("section.notice").length,
          escaped: !root.querySelector("escaped"),
        };
      });
      const knowledge = [0, 1, 2].map(count => {
        panel._result = {sources: Array.from({length: count}, (_, i) => ({source_id: String(i), title: "<source>"}))};
        const root = dom(renderKnowledge(panel));
        return {
          hasHeading: Boolean(root.querySelector(".section-heading h2")?.textContent.trim()),
          count: Number.parseInt(root.querySelector("[data-source-count]")?.textContent || "", 10),
          escaped: !root.querySelector("source"),
        };
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
        return {
          hasIntro: Boolean(root.querySelector(".broadcast-heading p")?.textContent.trim()),
          hasState: Boolean(root.querySelector(".broadcast-toggle-row p")?.textContent.trim()),
          hasFootnote: Boolean(root.querySelector(".setup-health-footnote")?.textContent.trim()),
          iconHidden: root.querySelector(".setup-health-footnote ha-icon")?.getAttribute("aria-hidden") === "true",
        };
      }));
      panel._result = original;
      return {
        conversations,
        knowledge,
        overview,
        usage: {
          hasContent: usage.childElementCount > 0,
          hasChartNote: Boolean(usage.querySelector(".chart-note")?.textContent.trim()),
        },
      };
    });
    for (const conversation of result.conversations) {
      expect(conversation.notices).toBe(0);
      expect(conversation.escaped).toBe(true);
      expect(conversation.hasHelp).toBe(admin);
    }
    expect(result.knowledge).toEqual([0, 1, 2].map(count => ({
      hasHeading: true,
      count,
      escaped: true,
    })));
    expect(result.usage.hasContent).toBe(true);
    expect(result.usage.hasChartNote).toBe(admin);
    for (const overview of result.overview) {
      expect(overview.hasIntro).toBe(true);
      expect(overview.hasFootnote).toBe(true);
      expect(overview.iconHidden).toBe(true);
    }
    expect(result.overview.map(o => o.hasState)).toEqual([true, false]);
    await expectHarnessClean(page, errors);
  });
}
