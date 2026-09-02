export interface HomeAssistantLike {
  callWS<T>(message: Record<string, unknown>): Promise<T>;
  localize?: (key: string, ...args: unknown[]) => string;
  states?: Record<string, unknown>;
}

export type StatusTone = "neutral" | "positive" | "warning" | "negative" | "info";

export type StateKind = "loading" | "empty" | "error";
