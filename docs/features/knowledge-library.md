# Knowledge Library

The **Knowledge Library** gives each conversation agent a locally stored collection of deliberately maintained reference documents. The model searches and retrieves them only when a question needs that information, so large household documents do not consume prompt tokens on every request.

Knowledge is different from persistent memory:

| Knowledge Library | Persistent memory |
| --- | --- |
| Longer maintained reference sources | Short individual facts |
| Scoped to a conversation agent | Scoped to an agent and Home Assistant user |
| Created and edited by an administrator in **Data & Memory → Knowledge Library** | Can be managed by the user and, depending on memory mode, through model memory tools |
| Never learned automatically from conversations | Automatic mode can save selected stable facts |
| Read-only to the model | Memory tools can add, update, and delete facts when allowed |

The two systems use independent storage and settings.

## Enable the feature

1. Open **Extended OpenAI** from the Home Assistant sidebar.
2. Select the conversation agent you want to configure.
3. Open **Data & Memory → Knowledge Library**.
4. Turn on **Allow the assistant to use Knowledge**. This switch saves immediately.

The setting defaults to Off. Stored sources are retained while it is Off, which lets you prepare or temporarily disable a library without losing it.

Enabling Knowledge checks that the assistant's default model and enabled AI routing rules can use the additional tools with the current provider. You can still turn off Knowledge while repairing an incompatible configuration.

## Manage Knowledge sources

Open **Extended OpenAI** from the Home Assistant sidebar, then **Data & Memory → Knowledge Library** (`/extended-openai/data-memory/knowledge`). The integration's **Configure → Manage knowledge** form also displays this path. Conversation-agent reconfiguration points to the sidebar panel rather than offering a separate settings editor. Knowledge management requires an administrator.

Select a conversation agent, then use the panel to:

- filter sources by title or description;
- create a source with a title, description, and multiline content;
- mark an individual source **Available to assistant** or disabled;
- edit large source text in the resizable editor;
- review availability, character count and last-updated time;
- delete a source after confirmation.

If a create reply is lost, saving again in the same editor retries the original
submission. Any newer edits remain in the editor. Adding a source or changing its
content prompts if another source already has the same text, even under a different
title or description. Select **Save** again with the same draft to keep both sources.
You can still change the title, description, or availability of an existing copy
without another warning.

New and existing sources are available by default. Disabling one source does **not** delete it. The source remains stored, visible, editable, included in backups, and can be re-enabled later. While disabled, it is excluded from the model-facing source catalogue, local search index and retrieval. `knowledge_get` cannot bypass the disabled state even if an exact source ID is supplied.

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

When the feature is On and the selected agent has at least one **available** source, the integration automatically supplies three built-in tools. They do not belong in custom-functions YAML.

- `knowledge_search` searches titles, descriptions, and indexed content chunks of available sources. It returns bounded excerpts, source IDs, and `start_character`, never whole large documents. The cursor is a zero-based character offset into the stored source at the selected chunk's start, directly usable with `knowledge_get`. Displayed chunk whitespace is trimmed, so this is not necessarily the excerpt's first character or the query-match location. Short subject keywords generally work better than full natural-language questions.
- `knowledge_list` returns a bounded, pageable catalogue of available source IDs, titles, descriptions, character counts, and update times. Its optional query filters titles and descriptions, preserving identifiers such as `C++` and `C#`. Longer queries support partial-text matching; single-character queries match whole tokens. It never returns source content. The model can use it when a search fails or it does not know the terminology used by the library.
- `knowledge_get` reads one available source by exact ID. It returns at most 20,000 characters and includes pagination fields so the model can request another section.

The tools work in both Responses and Chat Completions modes and use the existing maximum-function-call protection. No source catalogue or full source content is added to normal prompts. If the last available source is disabled or deleted, the tools and Knowledge Library prompt instructions disappear on the next request.

For the model-facing `knowledge_search` tool, omitting `source_ids` or passing
`[]` searches all sources permitted for that request. An explicit `null` is
rejected by the tool's array schema. A nonempty list searches only its valid,
permitted IDs. Guest Mode source restrictions apply in every case, including a
policy permitting no sources. The underlying library API treats
`source_ids=[]` as matching no sources.

## Local storage and limits

Each assistant's Knowledge sources are stored separately in Home Assistant. Indexing and search run locally. When the model uses a Knowledge tool, its result can send source titles, descriptions, excerpts or retrieved source text to the configured provider. A short source can fit in one result.

Server-side limits are:

- 500 sources per agent;
- 120 characters per title;
- 500 characters per description;
- 100,000 characters of content per source;
- 10 search results per call;
- 50 source summaries per `knowledge_list` call;
- 20,000 retrieved characters per `knowledge_get` call.

## Search behaviour and limitations

This version uses deterministic lexical retrieval. It normalizes words, indexes overlapping chunks, weights title and description matches, and boosts exact phrases. It works well when the question and source use related wording, but it does not understand synonyms as reliably as semantic embedding search.

If expected information is not found:

- add likely search terms or aliases to the title or description;
- use explicit headings and terminology in the content;
- split unrelated large material into focused sources;
- verify that **Knowledge Library** is On for the same agent selected in the panel;
- refresh the management panel and confirm the source is marked **Available**.

The assistant is instructed to start with short keyword searches, broaden an empty search once, and then use `knowledge_list` without a filter to inspect source metadata before concluding that the library lacks an answer. It must use only exact source IDs returned by Knowledge Library tools. A nonempty source filter containing only empty, unknown, disabled, or forbidden IDs returns no matches. Mixed filters search only the valid, permitted IDs. If discovery still finds nothing relevant, the assistant should report that outcome.

Source IDs are compared exactly across search, direct reads, and Guest Mode
allow-lists. Existing stored or imported IDs keep their original whitespace.

## Guest Mode

Guest Mode is an additional restriction on top of source availability. Its Knowledge policy can be **Off**, **On**, or **Custom**; Custom selects which sources guests may use. It cannot make a disabled source available. To expose a source to a guest, the source itself must first be **Available to assistant** and then permitted by the applicable Guest Mode Knowledge policy. Existing legacy Guest policies retain their saved allow-list behavior until an administrator explicitly adopts the current policy.

## Backup and restore

The enabled/disabled state of each Knowledge source is included in agent backups and restored with the source. Backups created before per-source availability existed are compatible: sources without an availability field are restored as enabled.

## Trust and privacy

Extended OpenAI tells the model to treat Knowledge sources as untrusted reference material. Source text grants no permission to run actions. The integration checks Home Assistant permissions and its own policies when a tool runs.

The integration never creates or updates Knowledge sources from conversation wording such as “remember this.” Use persistent memory for short remembered facts, or edit the Knowledge Library explicitly through its authenticated panel.
