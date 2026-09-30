// SPDX-License-Identifier: GPL-3.0-only
// Independent JMAP interoperability adapter, not vendored Stalwart server code.

export const CORE = "urn:ietf:params:jmap:core";
export const MAIL = "urn:ietf:params:jmap:mail";
export const LIMITS = Object.freeze({response: 2 * 1024 * 1024, message: 8 * 1024 * 1024,
  accounts: 128, mailboxes: 512, requestMs: 20000});

export class MailHostError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const fail = code => { throw new MailHostError(code); };
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value, maximum = 256) => typeof value === "string" && value.length > 0 &&
  value.length <= maximum && !/[\u0000-\u001f\u007f]/.test(value);
const identifier = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,256}$/.test(value);

export function serverOrigin(value) {
  if (typeof value !== "string" || value.length > 2048 || value !== value.trim() || /[\u0000-\u0020\u007f]/.test(value)) fail("invalid_server");
  let url;
  try { url = new URL(value); } catch { fail("invalid_server"); }
  if (url.username || url.password || url.search || url.hash || url.pathname !== "/" ||
      !["https:", "http:"].includes(url.protocol)) fail("invalid_server");
  if (url.protocol === "http:" && !["127.0.0.1", "[::1]"].includes(url.hostname)) fail("tls_required");
  return url.origin;
}

export function permissionOrigin(origin) {
  const url = new URL(serverOrigin(origin));
  // MailExtension host grants do not isolate ports; every actual request below does.
  return `${url.protocol}//${url.hostname}/*`;
}

function endpoint(origin, value, expectedPath) {
  if (!text(value, 2048)) fail("invalid_session");
  let url;
  try { url = new URL(value, origin); } catch { fail("invalid_session"); }
  if (url.origin !== origin || url.username || url.password || url.search || url.hash ||
      !expectedPath(url.pathname) || (value !== url.href && value !== url.pathname)) fail("invalid_session");
  return url.href;
}

function sessionRecord(origin, value) {
  if (!object(value) || !object(value.capabilities) || !object(value.capabilities[CORE]) ||
      !object(value.capabilities[MAIL]) || !object(value.accounts) || !object(value.primaryAccounts)) fail("invalid_session");
  const entries = Object.entries(value.accounts);
  if (!entries.length || entries.length > LIMITS.accounts) fail("invalid_session");
  const accounts = entries.filter(([, account]) => object(account) && object(account.accountCapabilities?.[MAIL]))
    .map(([id, account]) => {
      if (!identifier(id) || !text(account.name) || typeof account.isReadOnly !== "boolean") fail("invalid_session");
      return Object.freeze({id, name: account.name, readOnly: account.isReadOnly});
    });
  if (!accounts.length || !accounts.some(account => account.id === value.primaryAccounts[MAIL])) fail("invalid_session");
  const api = endpoint(origin, value.apiUrl, path => path === "/jmap/" || path === "/jmap");
  // Validate the template without granting the server another origin or arbitrary route.
  const uploadPath = "/jmap/upload/{accountId}/";
  if (value.uploadUrl !== uploadPath && value.uploadUrl !== `${origin}${uploadPath}`) fail("invalid_session");
  const maxUpload = value.capabilities[CORE].maxSizeUpload;
  if (!Number.isSafeInteger(maxUpload) || maxUpload <= 0) fail("invalid_session");
  return Object.freeze({accounts: Object.freeze(accounts), api, upload: `${origin}${uploadPath}`,
    primary: value.primaryAccounts[MAIL], maxUpload: Math.min(maxUpload, LIMITS.message)});
}

async function boundedJson(response) {
  if (!response.ok) fail(response.status === 401 || response.status === 403 ? "authentication_failed" : "request_failed");
  if ((response.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase() !== "application/json") fail("invalid_response");
  const length = response.headers.get("content-length");
  if (length !== null && (!/^\d+$/.test(length) || Number(length) > LIMITS.response)) fail("response_too_large");
  if (!response.body) fail("invalid_response");
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > LIMITS.response) fail("response_too_large");
      chunks.push(part.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
  const data = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) { data.set(chunk, at); at += chunk.length; }
  try { return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(data)); }
  catch { fail("invalid_response"); }
}

export class StalwartAccount {
  #origin;
  #authorization;
  #session = null;
  #fetch;
  #controllers = new Set();
  #sequence = 0;
  #busy = false;
  #account = null;
  #mailboxes = new Map();

  constructor({server, username, password}, fetcher = globalThis.fetch) {
    this.#origin = serverOrigin(server);
    if (!text(username, 320) || username.includes(":") || !text(password, 4096) || typeof fetcher !== "function") fail("invalid_credentials");
    const bytes = new TextEncoder().encode(`${username}:${password}`);
    this.#authorization = "Basic " + btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(""));
    bytes.fill(0);
    this.#fetch = fetcher;
  }

  get origin() { return this.#origin; }
  get accounts() { return this.#session?.accounts ?? []; }
  get maxMessageBytes() { return this.#session?.maxUpload ?? LIMITS.message; }
  get selectedAccountId() { return this.#account; }

  disconnect() {
    for (const controller of this.#controllers) controller.abort();
    this.#authorization = "";
    this.#session = null;
    this.#account = null;
    this.#mailboxes.clear();
  }

  async #request(url, method, body, type = "application/json") {
    if (!this.#authorization) fail("disconnected");
    endpoint(this.#origin, url, path => path === "/jmap/session" || path === "/jmap/" ||
      path === "/jmap" || /^\/jmap\/upload\/[A-Za-z0-9_-]{1,256}\/$/.test(path));
    const controller = new AbortController();
    this.#controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), LIMITS.requestMs);
    try {
      const response = await this.#fetch(url, {method, headers: {Authorization: this.#authorization,
        Accept: "application/json", ...(body === undefined ? {} : {"Content-Type": type})}, body,
        signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer"});
      if (response.redirected || response.url && response.url !== url) fail("redirect_rejected");
      const value = await boundedJson(response);
      if (!this.#authorization) fail("disconnected");
      return value;
    } catch (error) {
      if (error instanceof MailHostError) throw error;
      fail(controller.signal.aborted ? "request_cancelled" : "request_failed");
    } finally { clearTimeout(timer); controller.abort(); this.#controllers.delete(controller); }
  }

  async #exclusive(action) {
    if (this.#busy) fail("busy");
    this.#busy = true;
    try { return await action(); } finally { this.#busy = false; }
  }

  async connect() {
    return this.#exclusive(async () => {
      this.#session = sessionRecord(this.#origin,
        await this.#request(`${this.#origin}/jmap/session`, "GET"));
      this.#account = null; this.#mailboxes.clear();
      return this.accounts;
    });
  }

  #requireAccount(accountId) {
    if (!this.#session || !identifier(accountId) || !this.accounts.some(account => account.id === accountId)) fail("account_not_confirmed");
  }

  async #call(name, arguments_) {
    const id = `v${++this.#sequence}`;
    if (this.#sequence > 4096) fail("session_limit");
    const response = await this.#request(this.#session.api, "POST", JSON.stringify({
      using: [CORE, MAIL], methodCalls: [[name, arguments_, id]],
    }));
    if (!object(response) || !Array.isArray(response.methodResponses) || response.methodResponses.length !== 1) fail("invalid_response");
    const tuple = response.methodResponses[0];
    if (!Array.isArray(tuple) || tuple.length !== 3 || tuple[2] !== id || !object(tuple[1])) fail("invalid_response");
    if (tuple[0] === "error") fail("jmap_rejected");
    if (tuple[0] !== name || tuple[1].accountId !== arguments_.accountId) fail("invalid_response");
    return tuple[1];
  }

  async mailboxes(accountId, confirmed = false) {
    return this.#exclusive(async () => {
      this.#requireAccount(accountId);
      if (confirmed !== true) fail("account_not_confirmed");
      this.#account = null; this.#mailboxes.clear();
      const result = await this.#call("Mailbox/get", {accountId, ids: null,
        properties: ["id", "name", "role", "parentId", "totalEmails", "unreadEmails", "myRights"]});
      if (!Array.isArray(result.list) || result.list.length > LIMITS.mailboxes ||
          !Array.isArray(result.notFound) || result.notFound.length) fail("invalid_response");
      const seen = new Set();
      const boxes = result.list.map(box => {
        if (!object(box) || !identifier(box.id) || seen.has(box.id) || !text(box.name, 1024) ||
            ![box.totalEmails, box.unreadEmails].every(n => Number.isSafeInteger(n) && n >= 0) ||
            !object(box.myRights) || typeof box.myRights.mayAddItems !== "boolean") fail("invalid_response");
        seen.add(box.id);
        return Object.freeze({id: box.id, name: box.name, total: box.totalEmails, unread: box.unreadEmails,
          mayAdd: box.myRights.mayAddItems});
      });
      this.#account = accountId;
      this.#mailboxes = new Map(boxes.map(box => [box.id, box]));
      return boxes;
    });
  }

  async importMessage({accountId, mailboxId, bytes, confirmed = false}) {
    return this.#exclusive(async () => {
      this.#requireAccount(accountId);
      if (confirmed !== true || accountId !== this.#account || !this.#mailboxes.has(mailboxId)) fail("import_not_confirmed");
      if (this.accounts.find(account => account.id === accountId).readOnly || !this.#mailboxes.get(mailboxId).mayAdd) fail("read_only");
      if (!(bytes instanceof Uint8Array) || !bytes.byteLength || bytes.byteLength > this.maxMessageBytes) fail("message_size");
      const upload = this.#session.upload.replace("{accountId}", accountId);
      const blob = await this.#request(upload, "POST", bytes, "message/rfc822");
      if (!object(blob) || blob.accountId !== accountId || !identifier(blob.blobId) || blob.size !== bytes.byteLength) fail("invalid_upload");
      let result;
      try {
        result = await this.#call("Email/import", {accountId, emails: {selected: {
          blobId: blob.blobId, mailboxIds: {[mailboxId]: true}, keywords: {},
        }}});
      } catch { fail("import_uncertain"); }
      if (object(result.notCreated) && Object.hasOwn(result.notCreated, "selected")) fail("import_rejected");
      const created = result.created?.selected;
      if (!object(created) || !identifier(created.id) || !object(result.created) ||
          Object.keys(result.created).length !== 1 || result.notCreated && Object.keys(result.notCreated).length) fail("import_uncertain");
      let fetched;
      try {
        fetched = await this.#call("Email/get", {accountId, ids: [created.id],
          properties: ["id", "blobId", "mailboxIds", "size", "messageId"]});
      } catch { fail("import_readback_failed"); }
      const saved = fetched.list?.[0];
      if (!Array.isArray(fetched.list) || fetched.list.length !== 1 || !object(saved) ||
          saved.id !== created.id || !identifier(saved.blobId) || !object(saved.mailboxIds) ||
          saved.mailboxIds[mailboxId] !== true || !Number.isSafeInteger(saved.size) || saved.size <= 0 ||
          !Array.isArray(fetched.notFound) || fetched.notFound.length) fail("import_readback_failed");
      return Object.freeze({accountId, mailboxId, emailId: saved.id, size: saved.size,
        metadataReadback: true, messageSent: false, rawMessageVerified: false});
    });
  }
}
