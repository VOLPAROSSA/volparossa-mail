// SPDX-License-Identifier: GPL-3.0-only
// The background has no account credentials and does no network work.
browser.browserAction.onClicked.addListener(() => browser.runtime.openOptionsPage());
