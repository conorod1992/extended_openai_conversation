// @ts-nocheck
import {describe, expect, it} from "vitest";

import {
  footprintMarkup,
  footprintRegionMarkup,
  loadInputFootprint,
  reconcileInputFootprint,
} from "../../custom_components/extended_openai_conversation_responses/frontend/usage-input-footprint.js";

const panel = (overrides = {}) => ({
  _e: (value) => String(value ?? ""),
  _inputFootprintLoading: false,
  _inputFootprintError: null,
  _inputFootprint: null,
  ...overrides,
});

describe("Usage input footprint", () => {
  it("distinguishes exact local characters, approximate tokens, and provider usage", () => {
    const html = footprintMarkup(panel({
      _inputFootprint: {
        baseline: {
          characters: 12000,
          approx_tokens: 3000,
          without_function_groups_characters: 16000,
          without_function_groups_approx_tokens: 4000,
          function_group_savings: {characters: 4000, approx_tokens: 1000, percent: 25},
        },
        latest: {
          characters: 18000,
          approx_tokens: 4500,
          input_characters: 14000,
          tool_characters: 4000,
        },
        latest_provider_usage: {
          input_tokens: 5100,
          cached_input_tokens: 3200,
          model: "gpt-5.6",
        },
        notice: "Provider-reported usage remains authoritative.",
      },
    }));

    expect(html).toContain("Input footprint");
    expect(html).toContain("Fresh baseline");
    expect(html).toContain("12,000 characters");
    expect(html).toContain("~3,000 tokens");
    expect(html).toContain("Without Function Groups: 16,000 characters");
    expect(html).toContain("Saved by Function Groups");
    expect(html).toContain("Latest conversation request");
    expect(html).toContain("Provider-reported input");
    expect(html).toContain("5,100 tokens");
    expect(html).toContain("Provider-reported usage remains authoritative.");
    expect(html).toContain("attachment payload bytes are not included");
  });

  it("keeps a visible retry path when the footprint request fails", () => {
    const html = footprintMarkup(panel({_inputFootprintError: "failed"}));
    expect(html).toContain("could not be loaded");
    expect(html).toContain("Retry");
    expect(html).toContain('id="retry-input-footprint"');
  });

  it("wraps the footprint in a stable independently reconcilable region", () => {
    expect(footprintRegionMarkup(panel())).toContain("data-eoc-input-footprint");
  });

  it("reconciles only the footprint region rather than requesting a panel render", () => {
    const host = {innerHTML:"", __eocFootprintMarkup:null};
    const root = {querySelector:(selector) => selector === "[data-eoc-input-footprint]" ? host : null};
    const owner = panel({shadowRoot:root, _inputFootprintLoading:true});
    expect(reconcileInputFootprint(owner)).toBe(true);
    expect(host.innerHTML).toContain("Measuring the locally assembled request content");
  });

  it("keeps only the newest footprint refresh for the same agent", async () => {
    let resolveFirst;
    let resolveSecond;
    const first = new Promise((resolve) => { resolveFirst = resolve; });
    const second = new Promise((resolve) => { resolveSecond = resolve; });
    const calls = [first, second];
    const owner = panel({
      _agentId:"agent-a",
      _viewKey:() => "usage-maintenance/usage",
      shadowRoot:{querySelector:() => null},
      _call:() => calls.shift(),
    });

    const older = loadInputFootprint(owner);
    const newer = loadInputFootprint(owner);
    resolveSecond({baseline:{characters:2}});
    await newer;
    resolveFirst({baseline:{characters:1}});
    await older;

    expect(owner._inputFootprint.baseline.characters).toBe(2);
    expect(owner._inputFootprintLoading).toBe(false);
  });
});
