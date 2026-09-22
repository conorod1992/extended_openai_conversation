import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Guide search filters in place without replacing the route DOM", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  const search = panel.locator("#guide-search");
  await expect(search).toBeVisible();
  await expect(panel.locator(".guide-topic").first()).toBeVisible();

  await panel.evaluate((host) => {
    const root = host.shadowRoot;
    window.guideIdentity = {
      main: root.querySelector("main"),
      search: root.querySelector("#guide-search"),
      firstTopic: root.querySelector(".guide-topic"),
    };
  });

  await search.fill("zzzz-no-guide-topic");
  await expect(panel.locator(".guide-no-results")).toBeVisible();
  await expect(panel.locator(".guide-topic:visible")).toHaveCount(0);
  expect(await panel.evaluate((host) => {
    const root = host.shadowRoot;
    return {
      sameMain: root.querySelector("main") === window.guideIdentity.main,
      sameSearch: root.querySelector("#guide-search") === window.guideIdentity.search,
      sameFirstTopic: root.querySelector(".guide-topic") === window.guideIdentity.firstTopic,
      activeSearch: root.activeElement === root.querySelector("#guide-search"),
      value: root.querySelector("#guide-search")?.value,
    };
  })).toEqual({
    sameMain:true,
    sameSearch:true,
    sameFirstTopic:true,
    activeSearch:true,
    value:"zzzz-no-guide-topic",
  });

  await search.fill("");
  await expect(panel.locator(".guide-no-results")).toBeHidden();
  await expect(panel.locator(".guide-topic").first()).toBeVisible();
  expect(await panel.evaluate((host) =>
    host.shadowRoot.querySelector("main") === window.guideIdentity.main
    && host.shadowRoot.querySelector("#guide-search") === window.guideIdentity.search
    && host.shadowRoot.querySelector(".guide-topic") === window.guideIdentity.firstTopic
  )).toBe(true);
  await expectHarnessClean(page, errors);
});

test("Guide accordion closes only the previously open topic", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  const first = panel.locator("#guide-getting-started");
  const second = panel.locator("#guide-models");
  await expect(first).toBeVisible();
  await expect(second).toBeVisible();

  await first.locator("summary").click();
  await expect(first).toHaveAttribute("open", "");
  await second.locator("summary").click();
  await expect(second).toHaveAttribute("open", "");
  await expect(first).not.toHaveAttribute("open", "");

  expect(await panel.evaluate((host) => host._openGuideTopicElement?.id)).toBe("guide-models");
  await expectHarnessClean(page, errors);
});
