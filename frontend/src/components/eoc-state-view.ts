import {css, html, nothing} from "lit";

import {ExtendedOpenAiElement} from "../base/extended-openai-element";
import type {StateKind} from "../types";

export class EocStateView extends ExtendedOpenAiElement {
  static properties = {
    kind: {type: String},
    heading: {type: String},
    message: {type: String},
    icon: {type: String},
  };

  static styles = [
    ExtendedOpenAiElement.styles,
    css`
      :host {
        display: block;
      }

      .state {
        display: grid;
        justify-items: center;
        gap: 8px;
        padding: 28px 18px;
        text-align: center;
        color: var(--secondary-text-color);
      }

      ha-icon {
        color: var(--secondary-text-color);
        --mdc-icon-size: 28px;
      }

      .error ha-icon,
      .error strong {
        color: var(--error-color, #db4437);
      }

      strong {
        color: var(--primary-text-color);
        font-size: 16px;
      }

      p {
        max-width: 560px;
        margin: 0;
        line-height: 1.45;
      }
    `,
  ];

  kind: StateKind = "empty";
  heading = "";
  message = "";
  icon = "";

  protected render() {
    const role = this.kind === "error" ? "alert" : "status";
    return html`
      <div class="state ${this.kind}" part="state" role=${role} aria-live="polite">
        ${this.icon ? html`<ha-icon icon=${this.icon}></ha-icon>` : nothing}
        ${this.heading ? html`<strong>${this.heading}</strong>` : nothing}
        ${this.message ? html`<p>${this.message}</p>` : nothing}
        <slot></slot>
      </div>
    `;
  }
}
