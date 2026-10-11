import assert from "node:assert/strict";
import {memorySearchProjection} from "../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js";
const record = {content:"Ordinary fact", category:"home", subject:"UniqueSubject", key:"pet.oscar.breed"};
assert.ok(memorySearchProjection(record).includes("uniquesubject"));
assert.ok(memorySearchProjection(record).includes("pet.oscar.breed"));
assert.equal(memorySearchProjection({}).trim(), "");
