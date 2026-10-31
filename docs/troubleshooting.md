# Troubleshooting

Start with the simplest possible conversation-agent configuration, confirm it works, then enable advanced features one at a time.

## The integration does not appear after installation

Check that the folder is exactly:

```text
<config>/custom_components/extended_openai_conversation_responses/
```

and contains the integration's `manifest.json`.

Then restart Home Assistant and check the logs for component-loading errors.

## The assistant cannot see or control an entity

First check Assist exposure and the requesting user's access:

1. open **Settings > Voice assistants > Expose**
2. confirm the entity is exposed
3. retry a simple request using that entity's normal friendly name

Then check the agent's Home Assistant access settings, enabled Function Tools, and Guest Mode. Voice Identity selects retained-data scope; mapping a satellite to an administrator does not grant that administrator's action permissions. See [Home Assistant access](features/home-assistant-access.md).

You do not need a custom function for ordinary exposed-entity control.

## API authentication fails

Confirm that:

- the API key belongs to the provider configured in the integration
- the key has not been revoked
- the account/project has the required API access
- **Base URL** is correct

A ChatGPT subscription does not itself provide an OpenAI API key or API credit.

## A model works in Chat Completions but not Responses

Your provider or selected model may not implement a compatible `/v1/responses` endpoint.

Set **API mode** to **Chat Completions** and test again.

For direct OpenAI, confirm the selected model supports the feature you are trying to use. For custom providers, consult the provider's API documentation rather than assuming that “OpenAI-compatible” includes Responses.

## Web Search does not work

Web Search in this integration requires direct OpenAI Responses usage.

Check that:

- the Base URL is the direct OpenAI endpoint
- API mode resolves to Responses
- Web Search is enabled
- the selected model supports the hosted Web Search tool

It is not available through Chat Completions, custom Base URLs, or Azure OpenAI in the current implementation.

## Voice follow-ups do not happen

Check the **Continue conversation** option and the voice client.

Set the mode temporarily to **Always**:

- if Always works but Conditional does not, investigate the model/response behavior
- if Always also does not work, the voice client may not support Home Assistant's `continue_conversation` signal

## Memory does not remember something

Check that memory is not Off.

In **Manual** mode, explicitly say something like:

> Remember that I prefer temperatures in Celsius.

Then start a separate conversation and ask for the fact.

If **Automatically include memories** is `0`, the model must use memory tools to retrieve stored information rather than receiving local matches automatically.

You can inspect stored records from **Extended OpenAI > Data & memory > Memories**.

## A custom function fails

Reduce the function to the smallest failing example.

Check:

- the `spec` name and parameter schema
- required fields
- Home Assistant service/action names
- entity IDs
- Jinja templates
- whether the underlying Home Assistant action works outside the model

Where possible, test the Home Assistant script/action independently first. Then expose a thin model-facing wrapper around known-working logic.

## The conversation loses old context

Check **Assistant → Conversation → Trim history after** and the selected history strategy.

If the conversation exceeded the threshold, older turns may have been removed, summarized, or cleared according to the configured strategy.

Persistent memory is separate from conversation history: a remembered fact can survive context truncation, but ordinary chat history does not become persistent memory automatically unless Automatic memory chooses to store an appropriate fact.

Automatic persistent-memory ranking runs only when a logical conversation starts. If the topic changes, ask the assistant to use `memory_search`; the initial bundle is intentionally stable. In hybrid semantic mode, an unsupported embedding endpoint/model or request failure is logged and retrieval continues lexically. Verify the configured OpenAI-compatible provider supports the selected embedding model. Hybrid mode adds no LLM/classifier call, exports no raw embeddings, and does not guarantee perfect semantic matches.

## The assistant does not use a Knowledge source

Open **Extended OpenAI → Data & Memory → Knowledge Library** and select the conversation agent you are using. Check **Allow the assistant to use Knowledge** and confirm that at least one source is marked **Available to assistant**. The tools are absent when the feature is off or no enabled sources remain. Guest Mode can restrict the permitted sources further.

Search is lexical. Add important alternative terms to the source title or description, use clear headings, and split unrelated material into focused sources. The assistant is prompted to use short keyword searches, retry once with broader terms, and browse metadata with `knowledge_list` when needed. It is also instructed never to invent source IDs. A filter containing only blank or unknown source IDs returns no results; it does not broaden the search to the full library. Use IDs returned by `knowledge_list`, or omit the filter to search the agent's full permitted library. A source is re-indexed immediately after editing; no Home Assistant restart is required. Knowledge is not learned automatically from conversations, and “remember this” continues to use persistent memory rather than the Knowledge Library.

Do not put instructions or action authorization in a source. Source text is untrusted reference content and cannot override the assistant's higher-priority instructions.

## Usage values look incomplete

The Usage sensor relies on metadata returned by the provider.

Some OpenAI-compatible providers omit token fields. The integration can count requests and success/failure while leaving unavailable token counters unchanged.

Do not compare the Usage sensor directly with a monetary bill; it intentionally does not estimate pricing.

## Use Test agent

Select the conversation agent under **Extended OpenAI → Usage & Maintenance → Diagnostics** and use **Test agent**. Conversation-agent reconfiguration in **Settings → Devices & services** directs you to this panel.

The test checks local configuration plus at most one minimal model request. It does not execute Home Assistant device or service actions.

The separate **Test request** feature runs the normal request pipeline and can perform real actions, write memories, or consume API usage. Use harmless wording and test devices when checking Request Rules or tools.

## A save reports a conflict or its result is uncertain

The management UI checks that an editor is still based on the current saved configuration. If another tab or automation changes it, load the latest saved version and review your edits before saving again. **Save** applies the shared settings draft; Function Tool, Group, Rule, Knowledge, and policy editors have their own save actions.

If a connection drops while saving, the change may already have been committed. Let the panel reconnect and read the current saved state before retrying. Recovered unsaved edits are a browser convenience, not a backup; restarting Home Assistant or changing browsers is not a reliable way to preserve a draft.

## Retained data or settings cannot be loaded

Damaged retained-data stores can be blocked for repair rather than silently replaced with empty data. Check Home Assistant Repairs and the integration's diagnostic message. Keep a Home Assistant backup and any existing private agent backup before attempting recovery; do not delete `.storage` files to make the message disappear.

For recovery into a clean assistant when the affected assistant cannot initialize, see [Backup & Restore](features/backup-restore.md). An agent backup restores only the sections it contains; it cannot replace a full Home Assistant backup.

## Enable debug logging

Use **Settings → Devices & services → Extended OpenAI Conversation → Enable debug logging**, reproduce the issue, then disable debug logging to download the log. Review it before sharing; Home Assistant does not guarantee automatic redaction.

This captures operational logs. EOAI's separate **Request debugging** feature captures deeper per-request details; neither switch enables the other. See [Logging](reference/logging.md) for details and privacy precautions.

When reporting an issue, include the smallest relevant log excerpt and redact credentials, private URLs, entity data, and conversation text as necessary.
