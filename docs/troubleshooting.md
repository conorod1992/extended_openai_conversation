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

First check Assist exposure:

1. open **Settings > Voice assistants > Expose**
2. confirm the entity is exposed
3. retry a simple request using that entity's normal friendly name

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

If **Automatically retrieved memories** is `0`, the model must use memory tools to retrieve stored information rather than receiving local matches automatically.

You can inspect stored records from the **OpenAI memories** sidebar panel.

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

Check **Context Threshold** and **Context truncation strategy**.

If the conversation exceeded the threshold, older turns may have been removed, summarized, or cleared according to the configured strategy.

Persistent memory is separate from conversation history: a remembered fact can survive context truncation, but ordinary chat history does not become persistent memory automatically unless Automatic memory chooses to store an appropriate fact.

## The assistant does not use a Knowledge source

Confirm that **Knowledge Library** is On for the same conversation agent selected in **Manage knowledge**, and that at least one source exists. The tools are intentionally absent when the feature is Off or the library is empty.

Search is lexical. Add important alternative terms to the source title or description, use clear headings, and split unrelated material into focused sources. The assistant is prompted to use short keyword searches, retry once with broader terms, and browse unfiltered metadata with `knowledge_list` when needed. It is also instructed never to invent source IDs. Blank or unknown IDs are ignored and an all-invalid filter falls back to searching the full agent library. A source is re-indexed immediately after editing; no Home Assistant restart is required. Knowledge is not learned automatically from conversations, and “remember this” continues to use persistent memory rather than the Knowledge Library.

Do not put instructions or action authorization in a source. Source text is untrusted reference content and cannot override the assistant's higher-priority instructions.

## Usage values look incomplete

The Usage sensor relies on metadata returned by the provider.

Some OpenAI-compatible providers omit token fields. The integration can count requests and success/failure while leaving unavailable token counters unchanged.

Do not compare the Usage sensor directly with a monetary bill; it intentionally does not estimate pricing.

## Use Test agent

Under **Settings > Devices & services**, open the integration and choose **Configure > Test agent**.

The test checks local configuration plus at most one minimal model request. It does not execute Home Assistant device or service actions.

## Enable debug logging

See [Logging](reference/logging.md) for the recommended logger configuration and privacy precautions.

When reporting an issue, include the smallest relevant log excerpt and redact credentials, private URLs, entity data, and conversation text as necessary.
