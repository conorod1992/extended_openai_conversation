import {css, html, nothing} from "lit";

import {ExtendedOpenAiElement} from "../base/extended-openai-element";

export class EocSection extends ExtendedOpenAiElement {
  static properties = {
    heading: {type: String},
    description: {type: String},
    icon: {type: String},
    dense: {type: Boolean, reflect: true},
  };

  static styles = [
    ExtendedOpenAiElement.styles,
    css`
      :host {
        display: block;
      }

      section {
        display: grid;
        gap: 18px;
        padding: 20px;
        border: 1px solid var(--divider-color);
        border-radius: 12px;
        background: var(--card-background-color);
      }

      :host([dense]) section {
        gap: 12px;
        padding: 16px;
      }

      header {
        display: flex;
        gap: 12px;
        align-items: flex-start;
      }

      ha-icon {
        flex: 0 0 auto;
        color: var(--primary-color);
        --mdc-icon-size: 22px;
      }

      h2 {
        margin: 0;
        font-size: 18px;
        line-height: 1.35;
      }

      p {
        margin: 4px 0 0;
        color: var(--secondary-text-color);
        line-height: 1.45;
      }
    `,
  ];

  heading = "";
  description = "";
  icon = "";
  dense = false;

  protected render() {
    return html`
      <section part="section">
        ${this.heading || this.description
          ? html`<header part="header">
              ${this.icon ? html`<ha-icon icon=${this.icon}></ha-icon>` : nothing}
              <div>
                ${this.heading ? html`<h2>${this.heading}</h2>` : nothing}
                ${this.description ? html`<p>${this.description}</p>` : nothing}
              </div>
            </header>`
          : nothing}
        <div part="content"><slot></slot></div>
      </section>
    `;
  }
}
