// SPDX-License-Identifier: GPL-3.0-only
import {StalwartAccount, MailHostError, serverOrigin, permissionOrigin} from "./jmap.mjs";

const byId = id => document.getElementById(id);
const el = Object.fromEntries(["connect-form", "server", "username", "password", "owner-confirmed",
  "connect", "disconnect", "account", "load-mailboxes", "mailboxes", "mailbox", "message-file",
  "import-confirmed", "import", "size-limit", "status"].map(id => [id, byId(id)]));
let connection = null, busy = false, writable = false, generation = 0;

const messages = Object.freeze({
  invalid_server: "Enter a server origin only, without a path, credentials, query or fragment.",
  tls_required: "A remote server requires HTTPS. Unencrypted HTTP is limited to literal loopback addresses.",
  invalid_credentials: "Enter a username and user app password. Usernames cannot contain a colon.",
  authentication_failed: "Authentication was refused. Check your user account and app password.",
  permission_denied: "Host access was not granted. No account connection was opened.",
  invalid_session: "The session did not match this Stalwart connector's same-origin mail contract.",
  redirect_rejected: "A redirect or alternate endpoint was refused. Use the server's actual HTTPS origin.",
  request_failed: "The request failed. Check the server, its TLS certificate and connectivity. Nothing was retried.",
  request_cancelled: "The request was cancelled or timed out. Nothing was retried.",
  invalid_response: "The server response failed validation. Nothing was retried.",
  response_too_large: "The server response exceeded the connector's size limit.",
  disconnected: "Disconnected. Connect again to continue.",
  account_not_confirmed: "Choose an account and explicitly load its mailboxes first.",
  import_not_confirmed: "Select a file and destination, then confirm this specific import.",
  read_only: "This account or mailbox does not permit importing messages.",
  message_size: "Select one nonempty .eml message within the displayed upload limit.",
  invalid_upload: "The uploaded blob response failed validation. No import was requested.",
  jmap_rejected: "The mail server refused this operation. No automatic retry was made.",
  import_rejected: "The server rejected the message import. The temporary upload may remain until the server expires it.",
  import_uncertain: "Import completion is uncertain; the message may already exist. Inspect the mailbox before trying again.",
  import_readback_failed: "The server reported an import, but its metadata could not be confirmed. Inspect the mailbox before trying again.",
  session_limit: "This session reached its operation limit. Disconnect and reconnect to continue.",
  busy: "An operation is already in progress.",
});
const status = message => { el.status.textContent = message; };
const explain = error => status(messages[error instanceof MailHostError ? error.code : "request_failed"] || messages.request_failed);

function placeholder(select, label) {
  const option = document.createElement("option");
  option.value = ""; option.textContent = label;
  select.replaceChildren(option);
}

function controls() {
  const connected = connection !== null;
  for (const id of ["server", "username", "password", "owner-confirmed", "connect"]) el[id].disabled = connected || busy;
  el.disconnect.disabled = !connected;
  el.account.disabled = !connected || busy || !connection.accounts.length;
  el["load-mailboxes"].disabled = !connected || busy || !el.account.value;
  const confirmedAccount = connected && connection.selectedAccountId === el.account.value;
  el.mailbox.disabled = !confirmedAccount || busy;
  el["message-file"].disabled = !confirmedAccount || !writable || busy;
  const file = el["message-file"].files?.[0];
  const ready = confirmedAccount && writable && el.mailbox.value && file && file.size > 0 && file.size <= connection.maxMessageBytes;
  el["import-confirmed"].disabled = !ready || busy;
  el.import.disabled = !ready || !el["import-confirmed"].checked || busy;
}

function resetImport() {
  writable = false;
  el["import-confirmed"].checked = false;
  el["message-file"].value = "";
  placeholder(el.mailbox, "Load mailboxes first");
  el.mailboxes.replaceChildren();
}

function disconnect() {
  generation++;
  connection?.disconnect(); connection = null; busy = false;
  el.password.value = ""; el.username.value = ""; el["owner-confirmed"].checked = false;
  placeholder(el.account, "Connect first"); resetImport(); controls();
  status("Disconnected. This tab no longer holds account credentials. Existing host permission may remain in Thunderbird.");
}

el["connect-form"].addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || connection || !el["owner-confirmed"].checked || !el["connect-form"].reportValidity()) return;
  let candidate = null;
  const run = ++generation;
  try {
    const origin = serverOrigin(el.server.value);
    candidate = new StalwartAccount({server: origin, username: el.username.value, password: el.password.value});
    el.password.value = "";
    // Called directly within the explicit user gesture; no ambient host permission.
    const permission = browser.permissions.request({origins: [permissionOrigin(origin)]});
    busy = true; controls(); status("Waiting for the explicit host-access decision…");
    if (!await permission) throw new MailHostError("permission_denied");
    if (run !== generation) { candidate.disconnect(); return; }
    connection = candidate;
    controls(); status("Connecting to the selected account server…");
    const accounts = await connection.connect();
    if (run !== generation) return;
    placeholder(el.account, "Choose an account explicitly");
    for (const account of accounts) {
      const option = document.createElement("option");
      option.value = account.id; option.textContent = account.name + (account.readOnly ? " (read only)" : "");
      el.account.append(option);
    }
    el["size-limit"].textContent = `Upload limit for this connection: ${connection.maxMessageBytes.toLocaleString()} bytes.`;
    status("Connected. Choose an account and confirm before requesting its mailbox names and counts.");
  } catch (error) {
    candidate?.disconnect();
    if (run === generation) { connection = null; explain(error); }
  } finally {
    el.password.value = "";
    if (run === generation) { busy = false; controls(); }
  }
});

el.disconnect.addEventListener("click", disconnect);
window.addEventListener("pagehide", disconnect);
el.account.addEventListener("change", () => { resetImport(); controls(); });

el["load-mailboxes"].addEventListener("click", async () => {
  if (!connection || busy || !el.account.value) return;
  const run = generation, accountId = el.account.value;
  busy = true; resetImport(); controls(); status("Loading the explicitly selected account's mailboxes…");
  try {
    const boxes = await connection.mailboxes(accountId, true);
    if (run !== generation) return;
    placeholder(el.mailbox, "Choose a destination mailbox");
    const account = connection.accounts.find(item => item.id === accountId);
    for (const box of boxes) {
      const item = document.createElement("li");
      item.textContent = `${box.name} — ${box.total} messages; ${box.unread} unread`;
      el.mailboxes.append(item);
      if (box.mayAdd && !account.readOnly) {
        const option = document.createElement("option");
        option.value = box.id; option.textContent = box.name; el.mailbox.append(option); writable = true;
      }
    }
    status(`${boxes.length} mailboxes loaded. Message bodies were not fetched.`);
  } catch (error) { if (run === generation) explain(error); }
  finally { if (run === generation) { busy = false; controls(); } }
});

for (const id of ["mailbox", "message-file"]) el[id].addEventListener("change", () => {
  el["import-confirmed"].checked = false;
  controls();
});
el["import-confirmed"].addEventListener("change", controls);
el.import.addEventListener("click", async () => {
  if (!connection || busy || el.import.disabled || !el["import-confirmed"].checked) return;
  const run = generation, accountId = el.account.value, mailboxId = el.mailbox.value;
  const file = el["message-file"].files?.[0];
  if (!file || !file.size || file.size > connection.maxMessageBytes) { explain(new MailHostError("message_size")); return; }
  busy = true; el["import-confirmed"].checked = false; controls();
  status("Importing this selected file once, then checking the stored message's metadata…");
  let bytes;
  try {
    bytes = new Uint8Array(await file.arrayBuffer());
    if (run !== generation) return;
    const result = await connection.importMessage({accountId, mailboxId, bytes, confirmed: true});
    if (run !== generation) return;
    status(`Imported into the selected mailbox; metadata confirmed (${result.size.toLocaleString()} stored bytes). No mail was sent. Raw message bytes were not downloaded or compared.`);
  } catch (error) { if (run === generation) explain(error); }
  finally {
    bytes?.fill(0);
    if (run === generation) { busy = false; el["message-file"].value = ""; controls(); }
  }
});
controls();
