# Web Search

Web Search lets compatible models retrieve current public information through OpenAI's hosted search tool.

It is useful for questions whose answer may have changed since the model's training data, such as software releases, current events, live product information, or recent documentation.

## Requirements

Web Search in this integration requires:

- the direct OpenAI API
- **Responses** API mode, either selected directly or chosen through Auto
- a model that supports the hosted Web Search tool
- **Web Search** enabled in the conversation agent options

It is not supported through Chat Completions, custom Base URLs, or Azure OpenAI in the integration's current implementation.

## How it behaves

Enabling Web Search does not force a search for every request.

The model decides whether a question needs current web information. A normal Home Assistant request such as:

> Turn off the kitchen light.

should not require a web search, while a request such as:

> What's new in the latest Home Assistant release?

may use one.

## Web search context

The **Web search context** option controls the breadth of information retrieved by the hosted tool.

Available values are **Low**, **Medium**, and **High**.

Higher settings can increase latency and cost. They do not correspond to an exact result count or exact token allowance.

## Cost

OpenAI can charge separately for hosted Web Search in addition to ordinary model usage. Check OpenAI's current API pricing before enabling the feature if cost is important to you.

The integration's Usage sensor reports provider-supplied token/request metadata. It does not estimate a monetary bill or add a guessed Web Search cost.

## Citations

OpenAI can return structured `url_citation` annotations with Web Search results. The integration retains them on native Responses output items for context/replay, but Home Assistant voice responses do not currently expose them as clickable sources. When spoken-response cleanup is enabled, the stateful provider-neutral fallback also removes textual Markdown citations from progressive TTS, including citations split across arbitrary stream boundaries.

## Web Search versus Home Assistant data

Web Search is for public internet information. It is not how the assistant discovers your Home Assistant state.

Use entity exposure and Home Assistant tools for questions such as:

> Is the back door open?

Use Web Search for questions such as:

> Is there a new Home Assistant release today?
