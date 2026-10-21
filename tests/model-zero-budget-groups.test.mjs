import assert from "node:assert/strict";
import {modelFieldPresentation, webSearchControlState} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-model-presentation.js";

for (const budget of [0, "0", 1]) {
  for (const conditional of [false, true]) {
    for (const enabled of [false, true]) {
      const panel = {
        _draft: {chat_model:"gpt-6-astra", reasoning_effort:"medium", api_mode:"chat_completions", max_function_calls_per_conversation:budget, continue_conversation:conditional ? "conditional" : "ha_default", function_groups:[{id:"dormant",name:"Dormant",enabled,loading_mode:"on_demand",functions:["lookup"]}],functions:[{spec:{name:"lookup"}}]},
        _result: {options:{api_mode:[{value:"chat_completions"}]},model_capabilities:{api:{responses:true,chat_completions:true},function_calling:{responses:true,chat_completions:{support:"conditional",allowed_reasoning_efforts:["none"]}}}},
      };
      const result = modelFieldPresentation(panel,"api_mode","chat_completions");
      const expected = Number(budget) > 0 || conditional;
      assert.equal(result.disabledOption("chat_completions"),expected,JSON.stringify({budget,conditional,enabled}));
      assert.equal(Boolean(result.note),expected);
    }
  }
}

const panel = {
  _draft:{chat_model:"custom",api_mode:"auto",reasoning_effort:"medium",max_function_calls_per_conversation:0,continue_conversation:"ha_default",function_groups:[{enabled:true,loading_mode:"on_demand",functions:["lookup"]}]},
  _result:{model_capabilities:{auto_paths:{"medium:0:0":"chat_completions","medium:1:0":"responses","medium:0:1":"chat_completions","medium:1:1":"responses"},reasoning:{supported:true,by_api:{chat_completions:{efforts:["medium"]},responses:{efforts:["medium","high"]}}},evaluations:{chat_completions:{medium:{web_search:true}},responses:{medium:{web_search:false}}}}},
};
assert.deepEqual(modelFieldPresentation(panel,"reasoning_effort","medium").efforts,["medium"]);
assert.equal(webSearchControlState(panel).disabled,false);
console.log("Dormant Groups preserve zero-budget API, effort and web-search choices");
