// SPDX-License-Identifier: GPL-3.0-only
const status = document.getElementById("status");
const send = document.getElementById("send");
const maxStoredBytes = 4 * 1024 * 1024 + 65536;
let original;

async function showDraft() {
  const tab = new URL(location.href).searchParams.get("tab");
  if (!/^\d+$/.test(tab)) return;
  const list = await browser.mailTabs.getSelectedMessages(Number(tab));
  if (list.id || list.messages.length !== 1) return;
  const [draft] = list.messages;
  if (!draft.folder?.specialUse?.includes("drafts") || !draft.folder.accountId) return;
  const account = await browser.accounts.get(draft.folder.accountId, false);
  if (account.type !== "none") return;
  const recipients = [...draft.recipients, ...draft.ccList, ...draft.bccList];
  if (recipients.length !== 1) {
    status.textContent = "Private delivery requires one recipient.";
    return;
  }
  const file = await browser.messages.getRaw(draft.id, {data_format: "File", decrypt: false});
  if (!file.size || file.size > maxStoredBytes) {
    status.textContent = "The draft exceeds the private delivery size limit.";
    return;
  }
  original = file;
  document.getElementById("from").textContent = draft.author;
  document.getElementById("to").textContent = recipients[0];
  document.getElementById("subject").textContent = draft.subject || "(No subject)";
  document.getElementById("details").hidden = false;
  status.textContent = "The paired recipient will receive this saved version, including its attachments.";
  send.disabled = false;
}

function base64(bytes) {
  const parts = [];
  for (let offset = 0; offset < bytes.length; offset += 16384) {
    parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 16384)));
  }
  return btoa(parts.join(""));
}

send.addEventListener("click", async () => {
  if (!original || send.disabled) return;
  send.disabled = true;
  status.textContent = "Requesting custody from both providers…";
  try {
    const message = base64(new Uint8Array(await original.arrayBuffer()));
    const reply = await browser.runtime.sendNativeMessage("org.volparossa.peer_mail",
      {version: 1, operation: "send_selected_draft", message});
    if (reply.status === "in_custody" && reply.confirmed_providers === 2 && /^[0-9a-f]{64}$/.test(reply.message_id)) {
      status.textContent = "Both providers have the message for the recipient.";
      return;
    }
    if (reply.status === "ordinary_mail_required") {
      status.textContent = "This draft cannot use a paired address. You can choose ordinary mail in Thunderbird.";
      return;
    }
    if (reply.status === "attachment_unavailable") {
      status.textContent = "Attach the original file in Thunderbird, save the draft, and select it again.";
      return;
    }
    status.textContent = "Custody is unconfirmed. Retry this same saved draft to check the original delivery.";
  } catch {
    status.textContent = "The local mail service did not confirm custody. Check the service and retry this draft.";
  }
  send.disabled = false;
});

status.textContent = "Select one saved draft in the Drafts folder under Local Folders.";
showDraft().catch(() => { send.disabled = true; });
