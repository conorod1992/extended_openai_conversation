import {LitElement, css} from "lit";

import {fireEocEvent} from "../events";
import {errorMessage} from "../format";
import type {HomeAssistantLike} from "../types";

export abstract class ExtendedOpenAiElement extends LitElement {
  static styles = css`
    :host {
      box-sizing: border-box;
      color: var(--primary-text-color);
      font-family: var(--paper-font-body1_-_font-family, inherit);
    }

    *,
    *::before,
    *::after {
      box-sizing: inherit;
    }
  `;

  private _hass?: HomeAssistantLike;

  get hass(): HomeAssistantLike | undefined {
    return this._hass;
  }

  set hass(value: HomeAssistantLike | undefined) {
    const previous = this._hass;
    this._hass = value;
    this.requestUpdate("hass", previous);
  }

  protected async callWs<T>(message: Record<string, unknown>): Promise<T> {
    if (!this._hass) throw new Error("Home Assistant is not available");
    return this._hass.callWS<T>(message);
  }

  protected emit<T>(type: string, detail: T): boolean {
    return fireEocEvent(this, type, detail);
  }

  protected describeError(error: unknown, fallback?: string): string {
    return errorMessage(error, fallback);
  }
}
