import {beginOperation} from "../../custom_components/extended_openai_conversation_responses/frontend/operation-ownership.js";
import {callMutation, requireMutationAcknowledgement} from "../../custom_components/extended_openai_conversation_responses/frontend/mutation-response.js";
const current: () => boolean = beginOperation({}, "save");
const response = await callMutation(async () => ({revision:"r1"}));
const revision: string = response.revision;
void current; void revision;
// @ts-expect-error A primitive cannot own an operation.
beginOperation("panel", "save");
// @ts-expect-error The typed server revision cannot be assigned a number.
const numeric: number = response.revision;
// @ts-expect-error A response object is not acknowledgement validation.
requireMutationAcknowledgement(response, "save");
void numeric;

import {requireRequestRuleAcknowledgement} from "../../custom_components/extended_openai_conversation_responses/frontend/request-rule-acknowledgement.js";
requireRequestRuleAcknowledgement("update", {revision:2, rule:{id:"rule-1"}}, "rule-1");
// @ts-expect-error Rule identity must not be confused with the numeric revision.
requireRequestRuleAcknowledgement("update", {revision:2, rule:{id:2}}, "rule-1");
requireRequestRuleAcknowledgement("settings", {revision:2, defaults:{word_forms:true}, wording_groups:[]}, undefined);
// @ts-expect-error Matching defaults are an object, not a collection.
requireRequestRuleAcknowledgement("settings", {revision:2, defaults:[], wording_groups:[]}, undefined);
