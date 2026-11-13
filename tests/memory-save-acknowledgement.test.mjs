import assert from "node:assert/strict";
import {persistentMemorySaveAcknowledgement as acknowledge} from "../custom_components/extended_openai_conversation_responses/frontend/management-memory-feature.js";

const values = {type:"persistent", content:"Tea", category:"home", target_scope_id:"user:alice", subject:"", key:"", valid_from:""};
const memory = {memory_id:"existing", content:"Tea", category:"home", revision:"revision"};
const response = {memory, scope_id:"user:alice"};
for (const [status, message] of [["created", "Memory added"], ["updated", "Existing memory updated"], ["duplicate", "Memory already exists; no changes made"]]) {
  assert.equal(acknowledge({...response, status}, values, null), message);
}
assert.equal(acknowledge({...response, status:"updated"}, values, memory), "Memory updated");
assert.match(acknowledge({...response, status:"duplicate"}, {...values, category:"pets", subject:"Oscar", valid_from:"2026-01-01"}, null), /Existing metadata differs: category, subject, valid from/);
for (const bad of [null, {}, {...response}, {...response, status:"unknown"}, {...response, status:"duplicate", scope_id:"user:bob"},
  {...response, status:"created", memory:{...memory, revision:null}}, {...response, status:"created", memory:[]},
  {...response, status:"created", memory:{...memory, memory_id:42}}, {...response, status:"created", memory:{...memory, content:null}}]) {
  assert.equal(acknowledge(bad, values, null), null);
}
for (const status of ["created", "duplicate"]) assert.equal(acknowledge({...response, status}, values, memory), null);
assert.equal(acknowledge({...response, status:"updated", memory:{...memory, memory_id:"other"}}, values, memory), null);
