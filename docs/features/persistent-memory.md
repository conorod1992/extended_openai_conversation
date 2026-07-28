# Persistent memory

Persistent memory lets a conversation agent retain selected facts after chat history ends. It stores concise memories rather than complete conversation transcripts and retrieves only a bounded relevant subset for a later request.

!!! warning
    Persistent memory is **Off** by default and must be enabled separately for each conversation agent.

## Memory modes

### Off

Persistent-memory tools and automatic retrieval are disabled for the agent. Existing stored memories are not deleted when you switch memory off.

### Manual

The assistant stores a memory only when you explicitly ask it to remember something.

For example:

> Remember that I prefer temperatures in Celsius.

Relevant memories can still be retrieved automatically in later conversations.

### Automatic

The assistant can store stable, useful facts proactively in addition to explicit remember requests. It is instructed not to save ordinary chat, transient details, one-off events, duplicates, full transcript excerpts, or unsuitable sensitive information.

## Automatically retrieved memories

Under Advanced options, **Automatically retrieved memories** controls how many locally ranked memories can be inserted automatically into a request.

- Range: 0 to 10
- Default: 3
- Set to `0` for tool-only retrieval

The assistant can also use structured memory tools to search, list, add, update, and delete memories.

## Using memory

Store something explicitly:

> Remember that I prefer temperatures in Celsius.

In a later conversation:

> What temperature units do I normally use?

Remove it:

> Forget my preference about temperature units.

The assistant searches for the relevant stored record before deleting it.

## Manage memories in Home Assistant

Open **OpenAI memories** in the Home Assistant sidebar.

After selecting a conversation agent, you can manage memories belonging to the signed-in Home Assistant user:

- view content and category
- add a memory
- edit content or category
- delete one memory
- clear one category
- clear all memories for that user and agent

Category and full clears require confirmation. The backend also enforces confirmation for clear operations.

Home Assistant actions for listing, deleting, and clearing memories remain available for automation/compatibility use.

## What gets remembered?

Manual mode is designed around explicit requests. Automatic mode may also store stable information such as:

- durable preferences
- household or device context
- recurring routines
- long-lived project constraints

Stored facts should be concise and self-contained. When a fact changes, the assistant is encouraged to find and update the related memory rather than creating contradictory records.

The storage layer rejects equal normalized content and records with very high token overlap, but semantic duplicate/contradiction detection still depends partly on model behavior.

## Sensitive information

Persistent memory is **not a secrets manager**.

The storage layer rejects items such as passwords, access tokens, API keys, PINs, security codes, usable payment-card data, and certain bank-account identifiers. Automatic storage also applies stricter rules to sensitive personal categories.

Do not use memory as a place to keep credentials or other secrets.

## Storage and isolation

Memory uses Home Assistant's versioned `.storage` Store API.

- Each conversation-agent subentry has its own store.
- Authenticated Home Assistant users have separate memory scopes.
- Memory is independent of Recorder and its database backend.
- Memories survive Home Assistant restarts and integration reloads.
- The integration does not retain an OpenAI response/thread as the memory store.

Requests without a Home Assistant user ID use an anonymous scope. Anonymous requests to the same agent share that anonymous scope.

## Retrieval and OpenAI

The backend builds a local token index and ranks candidate memories locally.

The full memory database is never inserted into every model request. Instead:

1. a configured number of top local matches may be included automatically
2. the model can call `memory_search` for additional context
3. structured tools handle add, update, list, search, and delete operations

A stored memory remains local until its content is selected for inclusion in a model request or returned by a memory tool during a conversation. At that point, that selected content is sent to the configured model provider as conversation context.

## Limits

Current limitations include:

- memories are isolated between conversation agents
- users do not share a household memory pool
- keyword ranking may miss some paraphrases
- anonymous conversation sources share one anonymous scope per agent
- the storage design is intended for thousands of small records, with a limit of 10,000 per agent

Diagnostics report memory mode, record counts, user-scope counts, backend name, and storage version without exposing memory content.
