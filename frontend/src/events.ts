export const EOC_EVENTS = {
  navigate: "extended-openai:navigate",
  notify: "extended-openai:notify",
} as const;

export interface NavigationDetail {
  page: string;
  section?: string | null;
  target?: string | null;
}

export interface NotificationDetail {
  message: string;
  error?: boolean;
}

export function fireEocEvent<T>(target: EventTarget, type: string, detail: T): boolean {
  return target.dispatchEvent(
    new CustomEvent<T>(type, {
      detail,
      bubbles: true,
      composed: true,
    }),
  );
}
