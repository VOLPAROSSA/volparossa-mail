// SPDX-License-Identifier: GPL-3.0-only
browser.browserAction.onClicked.addListener(tab => {
  if (Number.isSafeInteger(tab.id)) {
    const page = new URL(browser.runtime.getURL("draft.html"));
    page.searchParams.set("tab", String(tab.id));
    return browser.tabs.create({url: page.href});
  }
});
