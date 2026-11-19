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

async function verifyOctets(response, expected) {
  if (response.status !== 200 || !response.body) fail("peer_bytes_unverified");
  const reader = response.body.getReader();
  let offset = 0;
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      if (value.length > expected.length - offset) fail("peer_bytes_unverified");
      for (const byte of value) {
        if (byte !== expected[offset++]) fail("peer_bytes_unverified");
      }
    }
    if (offset !== expected.length) fail("peer_bytes_unverified");
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
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
  #pendingImport = null;

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
    this.#pendingImport = null;
    this.#session = null;
    this.#account = null;
    this.#mailboxes.clear();
  }

  async #request(url, method, body, type = "application/json", expectedBytes = null) {
    if (!this.#authorization) fail("disconnected");
    endpoint(this.#origin, url, path => path === "/jmap/session" || path === "/jmap/" ||
      path === "/jmap" || /^\/jmap\/upload\/[A-Za-z0-9_-]{1,256}\/$/.test(path) ||
      (method === "GET" && expectedBytes && /^\/jmap\/download\/[A-Za-z0-9_-]{1,256}\/[A-Za-z0-9_-]{1,256}\/message.eml$/.test(path)));
    const controller = new AbortController();
    this.#controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), LIMITS.requestMs);
    try {
      const fetch = this.#fetch;
      const response = await fetch(url, {method, headers: {Authorization: this.#authorization,
        Accept: expectedBytes ? "*/*" : "application/json", ...(body === undefined ? {} : {"Content-Type": type})}, body,
        signal: controller.signal, credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer"});
      if (response.redirected || response.url && response.url !== url) fail("redirect_rejected");
      const value = expectedBytes ? await verifyOctets(response, expectedBytes) : await boundedJson(response);
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
      this.#pendingImport = null;
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
      if (this.#pendingImport?.accountId !== accountId) this.#pendingImport = null;
      this.#account = null; this.#mailboxes.clear();
      const result = await this.#call("Mailbox/get", {accountId, ids: null,
        properties: ["id", "name", "totalEmails", "unreadEmails", "myRights"]});
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

  #checkImport(accountId, mailboxId, bytes, confirmed) {
    this.#requireAccount(accountId);
    if (confirmed !== true || accountId !== this.#account || !this.#mailboxes.has(mailboxId)) fail("import_not_confirmed");
    if (this.accounts.find(account => account.id === accountId).readOnly || !this.#mailboxes.get(mailboxId).mayAdd) fail("read_only");
    if (!(bytes instanceof Uint8Array || bytes instanceof Blob)) fail("message_size");
    const size = bytes instanceof Blob ? bytes.size : bytes.byteLength;
    if (!size || size > this.maxMessageBytes) fail("message_size");
    return size;
  }

  async #createMessage(accountId, mailboxId, bytes, size, keywords, condition) {
    const upload = this.#session.upload.replace("{accountId}", accountId);
    const blob = await this.#request(upload, "POST", bytes, "message/rfc822");
    if (!object(blob) || blob.accountId !== accountId || !identifier(blob.blobId) || blob.size !== size) fail("invalid_upload");
    let result;
    try {
      result = await this.#call("Email/import", {accountId, ...condition, emails: {selected: {
        blobId: blob.blobId, mailboxIds: {[mailboxId]: true}, keywords,
      }}});
    } catch { fail("import_uncertain"); }
    if (object(result.notCreated) && Object.hasOwn(result.notCreated, "selected")) {
      const rejectedOnly = Object.keys(result.notCreated).length === 1 && object(result.notCreated.selected) &&
        typeof result.notCreated.selected.type === "string" &&
        (result.created == null || (object(result.created) && !Object.keys(result.created).length));
      fail(rejectedOnly ? "import_rejected" : "import_uncertain");
    }
    const created = result.created?.selected;
    if (!object(created) || !identifier(created.id) || !object(result.created) ||
        Object.keys(result.created).length !== 1 ||
        (result.notCreated != null && (!object(result.notCreated) || Object.keys(result.notCreated).length))) fail("import_uncertain");
    return created.id;
  }

  async importMessage({accountId, mailboxId, bytes, confirmed = false}) {
    return this.#exclusive(async () => {
      const size = this.#checkImport(accountId, mailboxId, bytes, confirmed);
      this.#pendingImport = null;
      const emailId = await this.#createMessage(accountId, mailboxId, bytes, size, {}, {});
      this.#pendingImport = {accountId, mailboxId, emailId, size};
      return this.#readImport(this.#pendingImport);
    });
  }

  // A peer delivery owns its ciphertext digest. The owner persists the first observed
  // Email state before submission. Stalwart sets the opaque keyword in the import itself.
  async importPeerMessage({accountId, mailboxId, bytes, deliveryId, retainState, confirmed = false}) {
    return this.#exclusive(async () => {
      const size = this.#checkImport(accountId, mailboxId, bytes, confirmed);
      if (!(bytes instanceof Uint8Array) || typeof deliveryId !== "string" || !/^[0-9a-f]{64}$/.test(deliveryId) ||
          typeof retainState !== "function") fail("invalid_peer_delivery");
      const original = Uint8Array.from(bytes);
      const keyword = "vp-" + deliveryId;
      const lookup = async () => {
        const result = await this.#call("Email/query", {accountId, filter: {hasKeyword: keyword}, limit: 2});
        if (!Array.isArray(result.ids) || result.ids.length > 1 ||
            result.ids.some(id => !identifier(id))) fail("peer_delivery_ambiguous");
        return result.ids[0];
      };

      let emailId = await lookup();
      if (!emailId) {
        const observed = await this.#call("Email/get", {accountId, ids: [], properties: ["id"]});
        if (!text(observed.state) || !Array.isArray(observed.list) || observed.list.length ||
            !Array.isArray(observed.notFound) || observed.notFound.length) fail("invalid_response");
        const originalState = await retainState(observed.state);
        if (originalState !== observed.state) fail("peer_import_needs_reconciliation");
        emailId = await this.#createMessage(accountId, mailboxId, original, size, {[keyword]: true},
          {ifInState: originalState});
        if (await lookup() !== emailId) fail("peer_import_needs_reconciliation");
      }
      const stored = await this.#storedMessage({accountId, mailboxId, emailId, size});
      await this.#request(`${this.#origin}/jmap/download/${accountId}/${stored.blobId}/message.eml`,
        "GET", undefined, undefined, original);
      return Object.freeze({accountId, mailboxId, emailId, size, deliveryId, rawMessageVerified: true});
    });
  }

  canRecheckImport(accountId, mailboxId) {
    const pending = this.#pendingImport;
    return pending !== null && accountId === this.#account && this.#mailboxes.has(mailboxId) &&
      pending.accountId === accountId && pending.mailboxId === mailboxId;
  }

  async recheckImport(accountId, mailboxId) {
    return this.#exclusive(() => {
      if (!this.canRecheckImport(accountId, mailboxId)) fail("no_import_to_check");
      return this.#readImport(this.#pendingImport);
    });
  }

  async #storedMessage({accountId, mailboxId, emailId, size}) {
    let fetched;
    try {
      fetched = await this.#call("Email/get", {accountId, ids: [emailId],
        properties: ["id", "blobId", "mailboxIds", "size"]});
    } catch { fail("import_readback_failed"); }
    const saved = fetched.list?.[0];
    if (!Array.isArray(fetched.list) || fetched.list.length !== 1 || !object(saved) ||
        saved.id !== emailId || !identifier(saved.blobId) || !object(saved.mailboxIds) ||
        saved.mailboxIds[mailboxId] !== true || saved.size !== size ||
        !Array.isArray(fetched.notFound) || fetched.notFound.length) fail("import_readback_failed");
    return saved;
  }

  async #readImport({accountId, mailboxId, emailId, size}) {
    const saved = await this.#storedMessage({accountId, mailboxId, emailId, size});
    this.#pendingImport = null;
    return Object.freeze({accountId, mailboxId, emailId: saved.id, size: saved.size,
      metadataReadback: true, messageSent: false, rawMessageVerified: false});
  }
}
