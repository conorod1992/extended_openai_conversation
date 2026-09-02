import {css, html} from "lit";

import {ExtendedOpenAiElement} from "../base/extended-openai-element";
import type {StatusTone} from "../types";

const TONES = new Set<StatusTone>(["neutral", "positive", "warning", "negative", "info"]);

export class EocStatusBadge extends ExtendedOpenAiElement {
  static properties = {
    label: {type: String},
    tone: {type: String},
  };

  static styles = [
    ExtendedOpenAiElement.styles,
    css`
      :host {
        display: inline-block;
      }

      span {
        display: inline-flex;
        align-items: center;
        min-height: 24px;
        padding: 3px 9px;
        border-radius: 999px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
        font-size: 12px;
        font-weight: 600;
        line-height: 1.25;
      }

      .positive {
        color: var(--success-color, #0f9d58);
        background: color-mix(in srgb, var(--success-color, #0f9d58) 12%, var(--card-background-color));
      }

      .warning {
        color: var(--warning-color, #b26a00);
        background: color-mix(in srgb, var(--warning-color, #f9ab00) 14%, var(--card-background-color));
      }

      .negative {
        color: var(--error-color, #db4437);
        background: color-mix(in srgb, var(--error-color, #db4437) 12%, var(--card-background-color));
      }

      .info {
        color: var(--primary-color);
        background: color-mix(in srgb, var(--primary-color) 10%, var(--card-background-color));
      }
    `,
  ];

  label = "";
  tone: StatusTone = "neutral";

  protected render() {
    const tone = TONES.has(this.tone) ? this.tone : "neutral";
    return html`<span part="badge" class=${tone}>${this.label}<slot></slot></span>`;
  }
}
