import {describe, expect, it} from "vitest";

import {errorMessage, formatDateTime, formatNumber, formatRelativeTime} from "../src/format";

describe("frontend formatting helpers", () => {
  it("formats finite numbers and hides invalid values", () => {
    expect(formatNumber(1234, "en-US")).toBe("1,234");
    expect(formatNumber(Number.NaN, "en-US")).toBe("—");
    expect(formatNumber(undefined, "en-US")).toBe("—");
  });

  it("formats relative timestamps deterministically", () => {
    const now = Date.UTC(2026, 8, 2, 12, 0, 0);
    expect(formatRelativeTime(now - 5 * 60_000, now, "en")).toBe("5 minutes ago");
    expect(formatRelativeTime(now + 2 * 60 * 60_000, now, "en")).toBe("in 2 hours");
    expect(formatRelativeTime(now - 10_000, now, "en")).toBe("now");
  });

  it("uses an em dash for invalid timestamps", () => {
    expect(formatDateTime("not-a-date", "en")).toBe("—");
  });

  it("extracts useful error text without assuming an Error instance", () => {
    expect(errorMessage(new Error("Provider unavailable"))).toBe("Provider unavailable");
    expect(errorMessage("Socket closed")).toBe("Socket closed");
    expect(errorMessage({code: 500}, "Fallback")).toBe("Fallback");
  });
});
