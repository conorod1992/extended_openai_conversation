import {prepareRulePackDownload} from "../custom_components/extended_openai_conversation_responses/frontend/rule-pack-download.js";
let input="";
for await(const chunk of process.stdin) input+=chunk;
try {
  const result=JSON.parse(input), download=prepareRulePackDownload(result);
  process.stdout.write(JSON.stringify({json:download.json,downloaded:await download.blob.text(),size:download.blob.size,security:download.security,count:download.count,pretty_size:Buffer.byteLength(JSON.stringify(JSON.parse(result.json),null,2))}));
} catch(error) {
  process.stdout.write(JSON.stringify({error:error.message}));
}
