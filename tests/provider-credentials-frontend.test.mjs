import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  diagnosticsAuthenticationRejected,
  providerCredentialScope,
  updateProviderApiKey,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-provider-credentials.js";

assert.equal(
  providerCredentialScope({entry_title:"OpenAI Home"}),
  "This credential belongs to “OpenAI Home” and is shared by its Conversation and AI Task agents.",
);
assert.match(providerCredentialScope({}), /parent provider connection/);

assert.equal(diagnosticsAuthenticationRejected({authentication_rejected:true}), true);
assert.equal(diagnosticsAuthenticationRejected({
  authentication_rejected:false,
  checks:[{name:"Model access", status:"Failed", message:"Authentication rejected"}],
}), false);
assert.equal(diagnosticsAuthenticationRejected({
  checks:[
    {name:"Authentication", status:"Failed", message:"Incorrect API key"},
    {name:"Model access", status:"Failed", message:"Authentication rejected"},
  ],
}), true);
assert.equal(diagnosticsAuthenticationRejected({
  checks:[{name:"Authentication", status:"Failed", message:"API client is unavailable"}],
}), false);
assert.equal(diagnosticsAuthenticationRejected({
  checks:[{name:"Model access", status:"Failed", message:"Rate limited"}],
}), false);

let sentCredentialRequest = null;
const credentialResult = await updateProviderApiKey(
  {
    _hass: {
      callWS: async (message) => {
        sentCredentialRequest = message;
        return {updated:true, validation_performed:true, reload_requested:true};
      },
    },
  },
  {entry_id:"entry-1"},
  "candidate-secret",
);
assert.deepEqual(sentCredentialRequest, {
  type:"extended_openai_conversation_responses/management/update_api_key",
  entry_id:"entry-1",
  api_key:"candidate-secret",
});
assert.equal(credentialResult.updated, true);
await assert.rejects(
  () => updateProviderApiKey({}, {entry_id:"entry-1"}, "candidate-secret"),
  /Home Assistant connection is unavailable/,
);
await assert.rejects(
  () => updateProviderApiKey({_hass:{callWS:async () => ({})}}, {}, "candidate-secret"),
  /Provider connection is unavailable/,
);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-provider-credentials.js", import.meta.url),
  "utf8",
);
assert.match(source, /Provider credentials/);
assert.match(source, /typeof result\?\.authentication_rejected === "boolean"/);
assert.match(source, /type="password"/);
assert.match(source, /autocomplete="off"/);
assert.match(source, /Validate & replace/);
assert.match(source, /extended_openai_conversation_responses\/management\/update_api_key/);
assert.match(source, /const connection = panel\?\._hass/);
assert.match(source, /updateProviderApiKey\(panel, agent, apiKey\)/);
assert.match(source, /saved key is never displayed/i);
assert.match(source, /Home Assistant's reauthentication flow if it is offered/);
assert.match(source, /#eoc-dialog-host/);
assert.match(source, /setDialogBusy\(dialog, true\)/);
assert.match(source, /stopDiagnosticsWatch\(this\)/);
assert.match(source, /panel\._data\?\.is_admin === false/);
assert.match(source, /reload_requested === false/);
assert.match(source, /syncAuthenticationRecovery\(panel, \{authentication_rejected: false\}\)/);
assert.doesNotMatch(source, /panel\.hass\.callWS/);
assert.doesNotMatch(source, /subentry_id: agent\.subentry_id/);
assert.doesNotMatch(source, /root\.append\(dialog\)/);
assert.doesNotMatch(source, /slice\([^)]*apiKey|substring\([^)]*apiKey/);

const bootstrap = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
assert.match(bootstrap, /management-provider-credentials\.js/);
