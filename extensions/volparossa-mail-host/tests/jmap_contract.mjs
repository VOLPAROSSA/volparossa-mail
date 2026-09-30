// SPDX-License-Identifier: GPL-3.0-only
// Offline protocol tests. No server process or external request is started.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import {CORE, MAIL, LIMITS, MailHostError, StalwartAccount, serverOrigin, permissionOrigin} from "../jmap.mjs";

const origin = "https://mail.example.test:8443";
function session() {
  return {capabilities: {[CORE]: {maxSizeUpload: 16000000}, [MAIL]: {}},
    primaryAccounts: {[MAIL]: "42"}, accounts: {"42": {name: "Example account", isReadOnly: false,
      accountCapabilities: {[MAIL]: {}}}}, apiUrl: `${origin}/jmap/`,
    uploadUrl: `${origin}/jmap/upload/{accountId}/`};
}
function json(value, status = 200) {
  return new Response(JSON.stringify(value), {status, headers: {"content-type": "application/json"}});
}
const boxes = () => ({accountId: "42", list: [{id: "inbox", name: "Inbox <not html>", totalEmails: 4,
  unreadEmails: 2, myRights: {mayAddItems: true}}], notFound: []});
const credentials = () => ({server: origin, username: "user@example.test", password: "explicit-app-password"});
const message = new TextEncoder().encode("From: sender@example.test\r\nTo: owner@example.test\r\nSubject: selected fixture\r\n\r\nPublic fixture.\r\n");
const code = wanted => error => error instanceof MailHostError && error.code === wanted;

function backend(options = {}) {
  const calls = [];
  const fetcher = async (url, request) => {
    calls.push({url, request});
    assert.equal(request.redirect, "error"); assert.equal(request.credentials, "omit");
    assert.equal(request.cache, "no-store"); assert.equal(request.referrerPolicy, "no-referrer");
    assert.match(request.headers.Authorization, /^Basic /);
    assert.equal(new URL(url).origin, origin);
    if (url.endsWith("/session")) return json(options.session ?? session());
    if (url.endsWith("/upload/42/")) {
      assert.equal(request.headers["Content-Type"], "message/rfc822");
      assert.deepEqual(request.body, message);
      return json(options.upload ?? {accountId: "42", blobId: "blob_1", size: message.length});
    }
    const body = JSON.parse(request.body);
    assert.deepEqual(body.using, [CORE, MAIL]); assert.equal(body.methodCalls.length, 1);
    const [method, args, id] = body.methodCalls[0];
    assert.equal(args.accountId, "42");
    if (options.throwOn === method) throw new Error("private raw server failure must not escape");
    let value;
    if (method === "Mailbox/get") value = options.boxes ?? boxes();
    else if (method === "Email/import") {
      assert.deepEqual(args.emails, {selected: {blobId: "blob_1", mailboxIds: {inbox: true}, keywords: {}}});
      value = options.import ?? {accountId: "42", created: {selected: {id: "message_1"}}, notCreated: null};
    } else if (method === "Email/get") {
      assert.deepEqual(args.ids, ["message_1"]);
      assert.deepEqual(args.properties, ["id", "blobId", "mailboxIds", "size", "messageId"]);
      value = options.readback ?? {accountId: "42", list: [{id: "message_1", blobId: "stored_blob",
        mailboxIds: {inbox: true}, size: message.length}], notFound: []};
    } else throw new Error("unexpected method");
    return json({methodResponses: [[options.method ?? method, value, options.id ?? id]]});
  };
  return {client: new StalwartAccount(credentials(), fetcher), calls};
}

test("only explicit HTTPS origins or literal loopback HTTP are accepted", () => {
  assert.equal(serverOrigin(origin + "/"), origin);
  assert.equal(serverOrigin("http://127.0.0.1:8080"), "http://127.0.0.1:8080");
  assert.equal(serverOrigin("http://[::1]:8080"), "http://[::1]:8080");
  assert.equal(permissionOrigin(origin), "https://mail.example.test/*");
  for (const value of ["https://u:p@example.test", "https://example.test/a", "https://example.test?x", "https://example.test#x", " https://example.test", "file:///tmp/x", "https://example.test\n"]) {
    assert.throws(() => serverOrigin(value), code("invalid_server"));
  }
  for (const value of ["http://example.test", "http://localhost:8080", "http://192.168.1.1", "http://127.0.0.2"]) {
    assert.throws(() => serverOrigin(value), code("tls_required"));
  }
});

test("connect does not select an account, list mailboxes or import automatically", async () => {
  const {client, calls} = backend();
  assert.equal(calls.length, 0);
  const accounts = await client.connect();
  assert.deepEqual(accounts, [{id: "42", name: "Example account", readOnly: false}]);
  assert.equal(client.selectedAccountId, null); assert.equal(calls.length, 1);
  assert.equal(client.maxMessageBytes, LIMITS.message);
  await assert.rejects(client.mailboxes("42"), code("account_not_confirmed"));
  await assert.rejects(client.mailboxes("other", true), code("account_not_confirmed"));
  await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true}), code("import_not_confirmed"));
  assert.equal(calls.length, 1);
  client.disconnect();
});

test("explicit mailbox confirmation and selected-file import use real JMAP operations and metadata readback", async () => {
  const {client, calls} = backend();
  await client.connect();
  assert.deepEqual(await client.mailboxes("42", true), [{id: "inbox", name: "Inbox <not html>", total: 4, unread: 2, mayAdd: true}]);
  await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message}), code("import_not_confirmed"));
  assert.equal(calls.length, 2);
  const result = await client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true});
  assert.deepEqual(result, {accountId: "42", mailboxId: "inbox", emailId: "message_1", size: message.length,
    metadataReadback: true, messageSent: false, rawMessageVerified: false});
  assert.equal(calls.length, 5);
  assert.deepEqual(calls.filter(call => call.url.endsWith("/jmap/")).map(call => JSON.parse(call.request.body).methodCalls[0][0]),
    ["Mailbox/get", "Email/import", "Email/get"]);
  client.disconnect();
});

test("cross-origin, alternate-port, redirected and arbitrary-path session endpoints are rejected before credentials reach them", async () => {
  for (const mutation of [s => {s.apiUrl = "https://other.example.test/jmap/";},
    s => {s.apiUrl = "https://mail.example.test/jmap/";}, s => {s.apiUrl = origin + "/api/";},
    s => {s.uploadUrl = "https://other.example.test/jmap/upload/{accountId}/";},
    s => {s.uploadUrl += "?secret=1";}, s => {delete s.capabilities[MAIL];}]) {
    const value = session(); mutation(value);
    const {client, calls} = backend({session: value});
    await assert.rejects(client.connect(), code("invalid_session")); assert.equal(calls.length, 1); client.disconnect();
  }
  const client = new StalwartAccount(credentials(), async () => ({redirected: true}));
  await assert.rejects(client.connect(), code("redirect_rejected")); client.disconnect();
});

test("read-only account, unknown mailbox and upload bounds reject before upload", async () => {
  for (const readOnly of [true, false]) {
    const value = session(); value.accounts["42"].isReadOnly = readOnly;
    const mailboxes = boxes(); mailboxes.list[0].myRights.mayAddItems = readOnly;
    const {client, calls} = backend({session: value, boxes: mailboxes});
    await client.connect(); await client.mailboxes("42", true);
    await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true}), code("read_only"));
    assert.equal(calls.length, 2); client.disconnect();
  }
  const {client, calls} = backend(); await client.connect(); await client.mailboxes("42", true);
  await assert.rejects(client.importMessage({accountId: "42", mailboxId: "other", bytes: message, confirmed: true}), code("import_not_confirmed"));
  for (const bytes of [new Uint8Array(), new Uint8Array(LIMITS.message + 1), "not bytes"]) {
    await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes, confirmed: true}), code("message_size"));
  }
  assert.equal(calls.length, 2); client.disconnect();
});

test("upload account and byte count are verified before import", async () => {
  for (const upload of [{accountId: "other", blobId: "blob_1", size: message.length},
    {accountId: "42", blobId: "blob_1", size: 0}]) {
    const {client, calls} = backend({upload}); await client.connect(); await client.mailboxes("42", true);
    await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true}), code("invalid_upload"));
    assert.equal(calls.length, 3); client.disconnect();
  }
});

test("uncertain import and failed metadata readback do not retry or claim completion", async () => {
  for (const [throwOn, expected, count] of [["Email/import", "import_uncertain", 4], ["Email/get", "import_readback_failed", 5]]) {
    const {client, calls} = backend({throwOn}); await client.connect(); await client.mailboxes("42", true);
    await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true}), code(expected));
    assert.equal(calls.length, count); client.disconnect();
  }
  const {client, calls} = backend({import: {accountId: "42", created: null, notCreated: {selected: {type: "invalidEmail"}}}});
  await client.connect(); await client.mailboxes("42", true);
  await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true}), code("import_rejected"));
  assert.equal(calls.length, 4); client.disconnect();
});

test("wrong response correlation, duplicate mailboxes and inconsistent metadata fail closed", async () => {
  for (const options of [{id: "foreign"}, {method: "Email/get"}, {boxes: {accountId: "other", list: [], notFound: []}},
    {boxes: {...boxes(), list: [...boxes().list, ...boxes().list]}}]) {
    const {client} = backend(options); await client.connect();
    await assert.rejects(client.mailboxes("42", true), code("invalid_response")); client.disconnect();
  }
  const {client} = backend({readback: {accountId: "42", list: [{id: "message_1", blobId: "blob", mailboxIds: {other: true}, size: 1}], notFound: []}});
  await client.connect(); await client.mailboxes("42", true);
  await assert.rejects(client.importMessage({accountId: "42", mailboxId: "inbox", bytes: message, confirmed: true}), code("import_readback_failed"));
  client.disconnect();
});

test("response bodies, status codes and UTF-8 are bounded and validated", async () => {
  const cases = [
    [() => new Response("{}", {status: 401}), "authentication_failed"],
    [() => new Response("not json", {headers: {"content-type": "text/html"}}), "invalid_response"],
    [() => new Response("{}", {headers: {"content-type": "application/json-wrong"}}), "invalid_response"],
    [() => new Response("{}", {headers: {"content-type": "application/json", "content-length": String(LIMITS.response + 1)}}), "response_too_large"],
    [() => new Response(" ".repeat(LIMITS.response + 1), {headers: {"content-type": "application/json"}}), "response_too_large"],
    [() => new Response(new Uint8Array([0xff]), {headers: {"content-type": "application/json"}}), "invalid_response"],
  ];
  for (const [respond, expected] of cases) {
    const client = new StalwartAccount(credentials(), async () => respond());
    await assert.rejects(client.connect(), code(expected)); client.disconnect();
  }
});

test("disconnect aborts pending requests and prevents further credential use", async () => {
  let signal;
  const client = new StalwartAccount(credentials(), async (_url, options) => {
    signal = options.signal;
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), {once: true}));
  });
  const pending = client.connect();
  await assert.rejects(client.connect(), code("busy"));
  client.disconnect(); await assert.rejects(pending, code("request_cancelled"));
  assert.equal(signal.aborted, true); assert.deepEqual(client.accounts, []);
  await assert.rejects(client.connect(), code("disconnected"));
});

test("extension requires explicit host grant, no stored credentials, native commands, mail access or administration", () => {
  const read = path => readFileSync(new URL(path, import.meta.url), "utf8");
  const manifest = JSON.parse(read("../manifest.json"));
  assert.deepEqual(manifest.permissions, []);
  assert.deepEqual(manifest.optional_permissions, ["https://*/*", "http://127.0.0.1/*", "http://[::1]/*"]);
  const source = read("../ui.mjs") + read("../jmap.mjs") + read("../background.js");
  assert.doesNotMatch(source, /browser\.(?:storage|accounts|messages|nativeMessaging)|localStorage|sessionStorage|console\.|innerHTML|eval\(|EmailSubmission|x:Domain|x:Account/);
  const ui = read("../ui.mjs"), html = read("../index.html");
  assert.match(ui, /browser\.permissions\.request/);
  assert.match(ui, /window\.addEventListener\("pagehide", disconnect\)/);
  assert.match(ui, /if \(busy \|\| connection \|\| !el\["owner-confirmed"\]\.checked/);
  assert.match(ui, /!el\["import-confirmed"\]\.checked/);
  assert.match(ui, /el\.password\.value = ""/);
  assert.match(html, /type="file"/); assert.match(html, /not a decentralised mail gateway/);
  assert.match(html, /deliberately trust its operator/); assert.match(html, /never a randomly selected peer/);
  assert.match(html, /at rest does not hide plaintext from an SMTP receiver/);
});
