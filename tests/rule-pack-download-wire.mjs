import {prepareRulePackDownload} from "../custom_components/extended_openai_conversation_responses/frontend/rule-pack-download.js";
let input="";
for await(const chunk of process.stdin) input+=chunk;
try {
  const pack=JSON.parse(input), download=prepareRulePackDownload(pack);
  process.stdout.write(JSON.stringify({json:download.json,size:download.blob.size,pretty_size:Buffer.byteLength(JSON.stringify(pack,null,2))}));
} catch(error) {
  process.stdout.write(JSON.stringify({error:error.message}));
}
