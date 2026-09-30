// SPDX-License-Identifier: GPL-3.0-only
// Native Thunderbird adapter. Never reads accounts, messages, drafts or attachments.

import { createVolparossaCooperativePanel } from "./VolparossaCooperativePanel.sys.mjs";

/** An explicit UI action may prefill text; only the panel can collect sharing consent. */
export function createVolparossaMailAI(document, container) {
  const panel = createVolparossaCooperativePanel(document, container);
  return Object.freeze({
    element: panel.element,
    async reviewPublicText({ question, text }) {
      if (typeof question !== "string" || typeof text !== "string") {
        throw new TypeError("Public text review requires strings");
      }
      // Ask only fills fields and clears consent. It does not connect to the daemon.
      await panel.ask(question, text);
    },
    destroy() {
      panel.destroy();
    },
  });
}
