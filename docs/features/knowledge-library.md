# Knowledge Library

The **Knowledge Library** gives each conversation agent a locally stored collection of deliberately maintained reference documents. The model searches and retrieves them only when a question needs that information, so large household documents do not consume prompt tokens on every request.

Knowledge is different from persistent memory:

| Knowledge Library | Persistent memory |
| --- | --- |
| Longer maintained reference sources | Short individual facts |
| Scoped to a conversation agent | Scoped to an agent and Home Assistant user |
| Created and edited only in **Manage knowledge** | Can be managed by the user and, depending on memory mode, through model memory tools |
| Never learned automatically from conversations | Automatic mode can save selected stable facts |
| Read-only to the model | Memory tools can add, update, and delete facts when allowed |

The two systems use independent storage and settings.

## Enable the feature

1. Open **Settings > Devices & services**.
2. Open **Extended OpenAI Conversation (Responses)**.
3. Reconfigure the desired conversation agent.
4. Turn on **Knowledge Library** and save.

The setting defaults to Off. Stored sources are retained while it is Off, which lets you prepare or temporarily disable a library without losing it.

## Manage Knowledge sources

Open the integration's **Configure** menu and choose **Manage knowledge**, then open the displayed `/extended-openai-knowledge` path. The authenticated Home Assistant panel is also available from the sidebar.

Select a conversation agent, then use the panel to:

- filter sources by title or description;
- create a source with a title, description, and multiline content;
- edit large source text in the resizable editor;
- review character count and last-updated time;
- delete a source after confirmation.

Use a clear, specific title and a short description. Structure long content with headings, paragraphs, and one fact or procedure per line where practical. This improves lexical matching.

### Example: kitchen layout

```text
Title: Kitchen layout
Description: Locations of utensils, food, appliances and guest-use items.
Content:
The cutlery drawer is immediately to the right of the dishwasher.
Spare tea towels are in the lowest drawer beside the oven.
The first-aid kit is in the top cupboard above the fridge.
```

### Example: tools inventory

```text
Title: Home tools and DIY equipment
Description: Inventory of hand tools, power tools, drill bits, fixings and adhesives.
Content:
The SDS drill is on the lower garage shelf.
Masonry bits from 5 mm to 12 mm are in the blue drill case.
Safety glasses and ear defenders are in the PPE drawer.
```

## On-demand model tools

When the feature is On and the selected agent has at least one source, the integration automatically supplies three built-in tools. They do not belong in custom-functions YAML.

- `knowledge_search` searches titles, descriptions, and indexed content chunks. It returns bounded excerpts and source IDs, never whole large documents. Short subject keywords generally work better than full natural-language questions.
- `knowledge_list` returns a bounded, pageable catalogue of source IDs, titles, descriptions, character counts, and update times. It never returns source content. The model can use it when a search fails or it does not know the terminology used by the library.
- `knowledge_get` reads one source by exact ID. It returns at most 20,000 characters and includes pagination fields so the model can request another section.

The tools work in both Responses and Chat Completions modes and use the existing maximum-function-call protection. No source catalogue or full source content is added to normal prompts. If the last source is deleted, the tools and Knowledge Library prompt instructions disappear on the next request.

## Local storage and limits

Sources are saved privately under Home Assistant's versioned `.storage` system using atomic asynchronous writes. Each conversation-agent subentry has an isolated store. Nothing is sent to an external search or embedding service; only excerpts retrieved during an active model request are sent to the configured model provider.

Server-side limits are:

- 500 sources per agent;
- 120 characters per title;
- 500 characters per description;
- 100,000 characters of content per source;
- 10 search results per call;
- 50 source summaries per `knowledge_list` call;
- 20,000 retrieved characters per `knowledge_get` call.

## Search behaviour and limitations

This first version uses deterministic lexical retrieval. It normalizes words, indexes overlapping chunks, weights title and description matches, and boosts exact phrases. It works well when the question and source use related wording, but it does not understand synonyms as reliably as semantic embedding search.

If expected information is not found:

- add likely search terms or aliases to the title or description;
- use explicit headings and terminology in the content;
- split unrelated large material into focused sources;
- verify that **Knowledge Library** is On for the same agent selected in the panel;
- refresh the management panel and confirm the source is present.

The assistant is instructed to start with short keyword searches, broaden an empty search once, and then use `knowledge_list` to inspect source metadata before concluding that the library lacks an answer. An empty `source_ids` value is treated as no filter, so compatible providers that emit optional arrays as `[]` still search the entire library. If discovery still finds nothing relevant, the assistant should say so rather than inventing a household-specific detail.

## Trust and privacy

Knowledge source text is treated as untrusted reference data. It cannot override system or developer instructions, authorize Home Assistant actions, or become a tool command merely because a source says so.

The integration never creates or updates Knowledge sources from conversation wording such as “remember this.” Use persistent memory for short remembered facts, or edit the Knowledge Library explicitly through its authenticated panel.
