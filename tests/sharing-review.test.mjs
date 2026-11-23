import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {SHARING_CONTRACT, restrictedSharingSecurity, sharingSummary} from "../custom_components/extended_openai_conversation_responses/frontend/sharing-review.js";
import {renderBackupTransferPanel} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-shell.js";

assert.match(SHARING_CONTRACT, /supported public settings/);
assert.match(SHARING_CONTRACT, /omit provider credentials, private data and custom content/);
assert.match(renderBackupTransferPanel(), /not encrypted/);
assert.doesNotMatch(renderBackupTransferPanel(), /transfer-reviewed-custom/);
assert.match(sharingSummary({omitted:[{count:2},{count:3}]}), /5 configuration/);
assert.match(sharingSummary({review_required:true}), /This older export can include custom content/);
const source=await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/sharing-review.js",import.meta.url),"utf8");
assert.match(source, /data-artifact.*\.value = result\.json/);
assert.throws(() => restrictedSharingSecurity({}), /did not return a restricted export/);
assert.throws(() => restrictedSharingSecurity({security:{mode:"reviewed_custom"}}), /did not return a restricted export/);
assert.equal(restrictedSharingSecurity({document:{security:{mode:"restricted",review_required:false}}}).mode, "restricted");
assert.doesNotMatch(source, /innerHTML = result/);
