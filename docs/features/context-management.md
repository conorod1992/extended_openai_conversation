# Context management

Long conversations eventually become too large to keep sending unchanged. Context management controls what happens when a conversation crosses the configured **Context Threshold**.

## Context threshold

The threshold is based on input-token usage reported by the configured provider.

The default threshold is `40000`.

When the threshold is crossed, the selected strategy is applied before continuing the conversation.

## Keep recent messages

This is the default for new agents.

The integration removes the oldest complete user turns while preserving recent conversation context. Tool calls are kept together with their corresponding results so truncation does not leave malformed or misleading function history.

This is generally the best balance between continuity and predictable cost/latency.

## Clear all messages

Clears previous conversation history when the threshold is exceeded.

This preserves legacy behavior and can be useful when you prefer a hard reset rather than summarization or partial history retention.

Existing agents that predate context-strategy storage can retain this legacy fallback until you explicitly change the option.

## Summarize older messages

Makes one bounded model request to summarize older context while retaining recent raw turns.

If summarization fails, the integration falls back to **Keep recent messages** rather than breaking the conversation.

Usage from the summary request is included in the agent's usage statistics.

## Which strategy should I use?

For most users, start with **Keep recent messages**.

Choose **Summarize older messages** when preserving the broad thread of a long conversation matters more than retaining every old message verbatim.

Choose **Clear all messages** when you prefer deterministic resets and do not need continuity once a conversation becomes large.

## Persistent memory is different

Context management applies to the current conversation history. Persistent memory stores selected facts separately and can make them available across different conversation IDs.

Clearing or truncating chat history does not delete persistent-memory records.
