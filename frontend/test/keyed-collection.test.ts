// @ts-nocheck
import {describe, expect, it} from "vitest";
import {keyedElement} from "../../custom_components/extended_openai_conversation_responses/frontend/keyed-collection.js";

describe("keyed collection identity", () => {
  it("reuses an unchanged node and rerenders when its signature changes", () => {
    const records = new Map();
    const initialNode = {};
    const first = keyedElement(records, "one", "a", () => initialNode);
    expect(first.node).toBe(initialNode);

    const unchanged = keyedElement(records, "one", "a", () => {
      throw new Error("unchanged card rendered");
    });
    expect(unchanged.node).toBe(initialNode);

    const replacementNode = {};
    const changed = keyedElement(records, "one", "b", () => replacementNode);
    expect(changed.node).toBe(replacementNode);
    expect(changed.node).not.toBe(initialNode);
  });
});
