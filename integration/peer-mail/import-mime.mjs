// SPDX-License-Identifier: GPL-3.0-only
// Called under the owner's delivery lock. Credential and addresses stay in the owner process.
import {createHash} from "node:crypto";
import {constants as fsConstants} from "node:fs";
import {open, rename} from "node:fs/promises";
import {dirname} from "node:path";
import {LIMITS, MailHostError, StalwartAccount} from "../../extensions/volparossa-mail-host/jmap.mjs";

async function privateBytes(path, maximum) {
  const file = await open(path, fsConstants.O_RDONLY | fsConstants.O_NONBLOCK | fsConstants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.nlink !== 1 || info.size < 1 || info.size > maximum) throw Error("invalid_private_file");
    const data = Buffer.alloc(info.size + 1);
    let used = 0;
    while (used < data.length) {
      const {bytesRead} = await file.read(data, used, data.length - used, null);
      if (!bytesRead) break;
      used += bytesRead;
    }
    if (used !== info.size) throw Error("changed_private_file");
    return data.subarray(0, used);
  } finally { await file.close(); }
}

async function previousState(path, binding) {
  try {
    const receipt = JSON.parse(await privateBytes(path, 4096));
    if (receipt.binding !== binding || typeof receipt.state !== "string") throw Error("delivery_binding_changed");
    return receipt.state;
  } catch (error) { if (error.code !== "ENOENT") throw error; }
  return null;
}

async function retain(path, binding, observed) {
  const next = path + ".next";
  const file = await open(next, fsConstants.O_NOFOLLOW | fsConstants.O_NONBLOCK |
    fsConstants.O_CREAT | fsConstants.O_WRONLY, 0o600);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.uid !== process.getuid() || (info.mode & 0o7777) !== 0o600 ||
        info.nlink !== 1) throw Error("invalid_state_file");
    await file.truncate(0);
    await file.writeFile(JSON.stringify({binding, state: observed}));
    await file.sync();
  } finally { await file.close(); }
  await rename(next, path);
  const parent = await open(dirname(path), fsConstants.O_DIRECTORY);
  try { await parent.sync(); } finally { await parent.close(); }
  return observed;
}

async function run() {
  const [configPath, mimePath, receiptPath, deliveryId] = process.argv.slice(2);
  if (process.argv.length !== 6 || !/^[0-9a-f]{64}$/.test(deliveryId)) throw Error("invalid_delivery");
  const config = JSON.parse(await privateBytes(configPath, 8192));
  const message = await privateBytes(mimePath, LIMITS.message);
  const parts = [];
  let count = 0;
  for await (const chunk of process.stdin) {
    count += chunk.length;
    if (count > 4097) throw Error("invalid_credentials");
    parts.push(chunk);
  }
  const password = new TextDecoder("utf-8", {fatal: true}).decode(Buffer.concat(parts)).replace(/\n$/, "");
  const client = new StalwartAccount({...config, password});
  try {
    const {accountId, mailboxId} = config;
    const messageSha256 = createHash("sha256").update(message).digest("hex");
    const binding = createHash("sha256").update(JSON.stringify(
      [client.origin, config.username, accountId, mailboxId, deliveryId, messageSha256]
    )).digest("hex");
    const existingState = await previousState(receiptPath, binding);
    await client.connect();
    await client.mailboxes(accountId, true);
    const result = await client.importPeerMessage({
      accountId, mailboxId, deliveryId, bytes: message, confirmed: true,
      retainState: observed => existingState ?? retain(receiptPath, binding, observed),
    });
    process.stdout.write(JSON.stringify({...result, messageSha256}) + "\n");
  } finally { client.disconnect(); message.fill(0); }
}

run().catch(error => {
  process.stderr.write((error instanceof MailHostError ? error.code : "peer_mail_import_incomplete") + "\n");
  process.exitCode = 1;
});
