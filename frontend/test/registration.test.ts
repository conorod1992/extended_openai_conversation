import {describe, expect, it} from "vitest";

import {defineCustomElement} from "../src/registration";

describe("custom element registration", () => {
  it("defines an element once and leaves existing registrations untouched", () => {
    const definitions = new Map<string, CustomElementConstructor>();
    const registry = {
      define(name: string, constructor: CustomElementConstructor) {
        definitions.set(name, constructor);
      },
      get(name: string) {
        return definitions.get(name);
      },
    } as unknown as CustomElementRegistry;
    const constructor = class {} as unknown as CustomElementConstructor;

    expect(defineCustomElement("eoc-test", constructor, registry)).toBe(true);
    expect(defineCustomElement("eoc-test", constructor, registry)).toBe(false);
    expect(definitions.get("eoc-test")).toBe(constructor);
  });

  it("does nothing when no custom element registry exists", () => {
    const constructor = class {} as unknown as CustomElementConstructor;
    expect(defineCustomElement("eoc-test", constructor, undefined)).toBe(false);
  });
});
