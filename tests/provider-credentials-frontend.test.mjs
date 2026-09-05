import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  diagnosticsAuthenticationRejected,
  providerCredentialScope,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-provider-credentials.js";

assert.equal(
  providerCredentialScope({entry_title:"OpenAI Home"}),
  "This credential belongs to “OpenAI Home” and is shared by its Conversation and AI Task agents.",
);
assert.match(providerCredentialScope({}), /parent provider connection/);

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

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-provider-credentials.js", import.meta.url),
  "utf8",
);
assert.match(source, /Provider credentials/);
assert.match(source, /type="password"/);
assert.match(source, /autocomplete="new-password"/);
assert.match(source, /Validate & replace/);
assert.match(source, /panel\._call\("diagnostics", "update_api_key", \{api_key: apiKey\}\)/);
assert.match(source, /saved key is never displayed/i);
assert.match(source, /Home Assistant's reauthentication flow if it is offered/);
assert.doesNotMatch(source, /slice\([^)]*apiKey|substring\([^)]*apiKey/);

const bootstrap = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
assert.match(bootstrap, /management-provider-credentials\.js/);
