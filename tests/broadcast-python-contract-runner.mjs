import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const url = new URL("../custom_components/extended_openai_conversation_responses/frontend/broadcast-acknowledgement.js", import.meta.url);
let moduleUrl = url.href;
if (process.argv[2] === "legacy") {
  const source = (await readFile(url, "utf8")).replaceAll("\r\n", "\n");
  const start = source.indexOf('value !== null && "id" in value');
  const end = source.indexOf(', "queuing Broadcast")', start);
  assert.ok(start > 0 && end > start);
  const mutated = (source.slice(0, start) + 'value !== null && "status" in value && typeof value.status === "string"' + source.slice(end))
    .replace(/from "(\.\/[^\"]+)"/g, (_, path) => `from "${new URL(path, url)}"`);
  moduleUrl = `data:text/javascript;base64,${Buffer.from(mutated).toString("base64")}`;
}
const {acknowledgeBroadcast} = await import(moduleUrl);
const {mutationResponseError} = await import("../custom_components/extended_openai_conversation_responses/frontend/mutation-response.js");
let input = "";
for await (const chunk of process.stdin) input += chunk;
const envelope = JSON.parse(input);
assert.equal(envelope.success, true);
const response = envelope.result;
assert.equal(Object.hasOwn(response, "status"), false);
const composer = {value:"Dinner"};
const panel = {hass:{user:{id:"owner"}}, _broadcastMessage:"Dinner", shadowRoot:{querySelector:() => composer}};
const owner = {userId:"owner", authenticationGeneration:0};
assert.strictEqual(acknowledgeBroadcast(panel, owner, "Dinner", 0, response), response);
assert.equal(panel._broadcastMessage, "");
assert.equal(composer.value, "");
// Fallible presentation must stay attached to the same producer response.
panel._broadcastMessage = "Dinner";
const failure = Error("composer unavailable");
panel.shadowRoot.querySelector = () => { throw failure; };
assert.strictEqual(acknowledgeBroadcast(panel, owner, "Dinner", 0, response), response);
assert.strictEqual(mutationResponseError(response), failure);
assert.equal(panel._broadcastMessage, "");
