import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  categorizeFunctionTools,
  deleteFunctionGroup,
  functionGroupIdFromName,
  matchesFunctionSearch,
} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";

const tool = (name) => ({spec: {name, description: name}, function: {type: "native"}});
const config = {
  functions: [tool("general"), tool("remind"), tool("calendar")],
  function_groups: [
    {id: "reminders", name: "Reminders", description: "Manage reminders", loading_mode: "on_demand", functions: ["remind"]},
    {id: "calendar", name: "Calendar", description: "Manage calendars", loading_mode: "always", functions: ["calendar"]},
  ],
};

assert.equal(functionGroupIdFromName("  Conditional Notifications  "), "conditional-notifications");
assert.equal(functionGroupIdFromName("123 Reminders"), "reminders");
assert.equal(matchesFunctionSearch("Reminder", "Reminders Manage reminders"), true);
assert.equal(matchesFunctionSearch("Reminder", "remind family native"), true);
assert.equal(matchesFunctionSearch("manage calendar", "Calendar Manage calendars"), true);
assert.equal(matchesFunctionSearch("weather", "Reminders Manage reminders"), false);
const managementPanelSource = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/management-panel.js",
    import.meta.url,
  ),
  "utf8",
);
assert.ok(
  managementPanelSource.includes("[hidden]{display:none!important}"),
  "dialog search results marked hidden must actually be removed from layout",
);

const categorized = categorizeFunctionTools(config);
assert.deepEqual(categorized.alwaysAvailable.map((item) => item.spec.name), ["general"]);
assert.deepEqual(categorized.groups.map((group) => group.tools.map((item) => item.spec.name)), [["remind"], ["calendar"]]);

const deleted = deleteFunctionGroup(config, "reminders");
assert.equal(deleted.function_groups.length, 1);
assert.deepEqual(deleted.functions, config.functions, "deleting a group must retain its functions");
assert.deepEqual(
  categorizeFunctionTools(deleted).alwaysAvailable.map((item) => item.spec.name),
  ["general", "remind"],
);
assert.equal(config.function_groups.length, 2, "group helpers must not mutate the saved draft");

const many = {
  functions: Array.from({length: 100}, (_, index) => tool(`tool_${index}`)),
  function_groups: [],
};
assert.equal(categorizeFunctionTools(many).alwaysAvailable.length, 100);
