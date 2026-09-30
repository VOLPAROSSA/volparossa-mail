// SPDX-License-Identifier: GPL-3.0-only
import { createVolparossaMailAI } from "resource:///modules/VolparossaMailAI.sys.mjs";

const integration = createVolparossaMailAI(document, document.getElementById("volparossa-mail-ai"));
document.getElementById("volparossa-mail-close").addEventListener("click", () => window.close());
window.addEventListener("unload", () => integration.destroy(), { once: true });
// No selection, message URI, account or window arguments are harvested on opening.
