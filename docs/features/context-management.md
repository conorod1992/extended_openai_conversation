# Context management

Long conversations eventually become too large to keep sending unchanged. Configure **Trim history after** and the history strategy under **Extended OpenAI → Assistant → Conversation**.

## Context threshold

The threshold uses provider-reported input usage when available and a conservative local estimate when the provider omits it. The integration also measures prepared input before sending oversized history. Local estimates help manage context; they are never added to the provider-reported Usage token totals.

The default threshold is `40000`.

When the threshold is crossed, the selected strategy reduces older context. This is not a hard cap on the provider's context window: the current user turn is preserved even if it alone exceeds the threshold. Very large prompts, attachments, or tool definitions can still exceed a model's limits.

## Keep recent messages

This is the default for new agents.

The integration removes the oldest complete user turns while preserving recent conversation context. Tool calls are kept together with their corresponding results so truncation does not leave malformed or misleading function history.

Selection measures the prepared provider input, including attachment budgets. Encrypted reasoning from earlier turns stays available for replay. When a supported OpenAI model uses only the current turn's reasoning, those earlier encrypted bytes do not count toward the estimate. Selection finds the largest complete recent suffix that fits. If the current turn alone exceeds the threshold, older turns are removed and the current turn is preserved. Measurement, retention selection, summary planning and history fingerprints run in workers, with the resulting history published on Home Assistant's event loop.

This is generally the best balance between continuity and predictable cost/latency.

## Clear all messages

Clears previous conversation history when the threshold is exceeded.

This preserves legacy behavior and can be useful when you prefer a hard reset rather than summarization or partial history retention.

Existing agents that predate context-strategy storage can retain this legacy fallback until you explicitly change the option.

## Summarize older messages

Makes a bounded model request to summarize older context while retaining recent raw turns. It uses the effective model and reasoning effort for that request, including Request Rule overrides. The instruction targets at most 256 visible summary tokens. The total output allowance is 512 tokens without reasoning, or 8,192 with reasoning, including implicit/fixed reasoning such as o3-pro, because hidden reasoning shares that allowance. Both are capped by the model's supported output limit.

Ordinary summary maintenance runs after the current reply is complete. Oversized prepared input can also require summarization before sending a provider request. Deferred summaries have 15 seconds from scheduling, including provider retries. If another turn arrives sooner, the integration waits within that deadline and applies the summary when available. If the deadline expires, the next request uses the recent turns and keeps its new messages.

If the provider reports output-token exhaustion, the integration retries once with twice the output allowance, still capped by the model's limit. Other failures are not retried. If summarization still fails, the integration falls back to **Keep recent messages** rather than breaking the conversation.

Usage from every summary attempt is included in the agent's aggregate usage statistics. Deferred summaries are treated as detached maintenance rather than modifying an already-finalized per-turn usage run.

## Which strategy should I use?

For most users, start with **Keep recent messages**.

Choose **Summarize older messages** when preserving the broad thread of a long conversation matters more than retaining every old message verbatim.

Choose **Clear all messages** when you prefer deterministic resets and do not need continuity once a conversation becomes large.

## Persistent memory is different

Context management applies to the current conversation history. Persistent memory stores selected facts separately and can make them available across different conversation IDs.

Clearing or truncating chat history does not delete persistent-memory records.
