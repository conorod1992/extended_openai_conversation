import assert from "node:assert/strict";

import {
  categorizeFunctionTools,
  deleteFunctionGroup,
  functionGroupIdFromName,
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
