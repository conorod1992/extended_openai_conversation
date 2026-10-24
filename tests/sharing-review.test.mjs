import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {SHARING_CONTRACT, sharingSummary} from "../custom_components/extended_openai_conversation_responses/frontend/sharing-review.js";
import {renderBackupTransferPanel} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-shell.js";

assert.match(SHARING_CONTRACT, /unclassified custom content by default/);
assert.match(SHARING_CONTRACT, /may still contain secrets/);
assert.match(renderBackupTransferPanel(), /not encrypted/);
assert.match(renderBackupTransferPanel(), /transfer-reviewed-custom/);
assert.doesNotMatch(renderBackupTransferPanel(), /excludes.*real secret values/);
assert.match(sharingSummary({omitted:[{count:2},{count:3}],review_required:true}), /5 configuration/);
assert.match(sharingSummary({review_required:true}), /arbitrary secrets may remain/);
const source=await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/sharing-review.js",import.meta.url),"utf8");
assert.match(source, /data-artifact.*\.value = result\.json/);
assert.match(source, /download\.disabled = Boolean\(security\.review_required\)/);
assert.doesNotMatch(source, /innerHTML = result/);
