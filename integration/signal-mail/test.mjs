// SPDX-License-Identifier: GPL-3.0-only
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile, execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';

const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const mime = Buffer.from([
  'From: Alice <alice@example.invalid>', 'To: Bob <bob@example.invalid>',
  'Subject: =?UTF-8?Q?Rendez-vous_=C3=A0_la_gare?=', 'Message-ID: <hello@example.invalid>',
  'MIME-Version: 1.0', 'Content-Type: multipart/mixed; boundary="example"', '',
  '--example', 'Content-Type: text/plain; charset=utf-8', '', 'à bientôt',
  '--example', 'Content-Type: application/octet-stream', 'Content-Transfer-Encoding: base64', '',
  'AAD/AQID', '--example--', '',
].join('\r\n'));

function trial(t) {
  const dir = mkdtempSync(join(tmpdir(), 'signal-mail-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = name => join(dir, name);
  const args = (operation, owner, ...pairs) => ['--no-warnings', cli, operation, '--profile', file(owner), ...pairs];
  const call = (...values) => JSON.parse(execFileSync(process.execPath, args(...values), { stdio: ['ignore','pipe','pipe'] }));
  const parallel = async (...values) =>
    JSON.parse((await promisify(execFile)(process.execPath, args(...values))).stdout);
  const init = owner => call('init', owner, '--bundle', file(owner + '.bundle')).bundle_sha256;
  const pair = (a, b, fingerprint) => call('pair', a, '--peer-bundle', file(b + '.bundle'), '--trusted-bundle-sha256', fingerprint);
  const send = (owner,id,input,output) => ['seal',owner,'--id',id,'--input',file(input),'--output',file(output)];
  const receive = (owner,input,output) => ['open',owner,'--input',file(input),'--output',file(output)];
  const put = (name,bytes) => writeFileSync(file(name),bytes,{ mode: 0o600 });
  const bytes = name => readFileSync(file(name));
  return { file, args, call, parallel, init, pair, send, receive, put, bytes };
}

test('pinned native Signal exchanges original multipart bytes in restarted, independently paired profiles', async t => {
  const { file, call, parallel, init, pair, send, receive, put, bytes } = trial(t);
  const alice = init('alice'), bob = init('bob'), outsider = init('outsider');
  assert.equal(init('alice'), alice);
  assert.notEqual(alice, bob);
  assert.equal(hash(bytes('bob.bundle')), bob);
  assert.throws(() => pair('alice','bob',outsider));
  assert.throws(() => pair('alice','alice',alice));
  pair('alice','bob',bob);
  pair('alice','bob',bob);
  assert.throws(() => pair('alice','outsider',outsider));
  put('source',mime);
  const id = hash('outgoing-journal-row-1');
  const command = send('alice', id, 'source', 'wire');
  const first = call(...command);
  assert.deepEqual(first, { operation:'seal', packet_sha256:hash(bytes('wire')), message_sha256:hash(mime), message_bytes:mime.length });
  assert.equal(bytes('wire')[6], 3); // Native prekey message.
  assert.equal(bytes('wire').includes(mime), false);
  assert.throws(() => call(...receive('bob','wire','early')));
  pair('bob','alice',alice);
  pair('outsider','alice',alice);
  assert.throws(() => call(...receive('outsider','wire','wrong-owner')));
  put('corrupt', Buffer.from(bytes('wire')));
  const bad = bytes('corrupt'); bad[bad.length-1] ^= 1; put('corrupt',bad);
  assert.throws(() => call(...receive('bob','corrupt','invalid')));

  // Publishing can fail after the transaction commits. Both operations must replay
  // their original bytes from a different process after the caller repairs its output.
  put('occupied', Buffer.from('preserve this'));
  assert.throws(() => call(...receive('bob','wire','occupied')));
  assert.equal(bytes('occupied').toString(), 'preserve this');
  const opened = call(...receive('bob','wire','received'));
  assert.equal(opened.packet_sha256, first.packet_sha256);
  assert.equal(opened.message_sha256, first.message_sha256);
  assert.deepEqual(bytes('received'),mime);

  const second = Buffer.from('From: b@example.invalid\r\nTo: a@example.invalid\r\n\r\nReçu\r\n');
  put('reply',second);
  const replyId = hash('reply-journal-entry');
  assert.throws(() => call(...send('bob',replyId,'reply','occupied')));
  await Promise.all(['return1','return2'].map(name => parallel(...send('bob',replyId,'reply',name))));
  assert.deepEqual(bytes('return1'), bytes('return2'));
  assert.equal(bytes('return1')[6], 2); // The established native session.
  call(...receive('alice','return1','reply-opened'));
  assert.deepEqual(bytes('reply-opened'),second);
  assert.throws(() => call(...send('alice',id,'reply','wire')));
  await Promise.all([parallel(...command), parallel(...receive('bob','wire','received'))]);
  assert.equal(hash(bytes('wire')), first.packet_sha256);
  assert.deepEqual(bytes('received'),mime);
  chmodSync(file('alice'), 0o755);
  assert.throws(() => call(...command));
});

test('concurrent first sends and out-of-order prekey messages keep distinct transactions', async t => {
  const { call, parallel, init, pair, send, receive, put, bytes } = trial(t);
  const a = init('a'), b = init('b');
  pair('a','b',b); pair('b','a',a);
  for (const i of [1,2,3]) put('m'+i, Buffer.concat([mime, Buffer.from(String(i))]));
  const ids = [1,2,3].map(i => hash('row'+i));
  await Promise.all(ids.map((id,i) => parallel(...send('a',id,'m'+(i+1),'p'+(i+1)))));
  for (const i of [3,1,2]) {
    call(...receive('b','p'+i,'r'+i));
    assert.deepEqual(bytes('m'+i),bytes('r'+i));
  }
});


test('abrupt process death rolls back unfinished ratchets and republishes committed mail', t => {
  const { file, args, call, init, pair, send, receive, put, bytes } = trial(t);
  const a = init('a'), b = init('b');
  pair('a','b',b); pair('b','a',a);
  put('source',mime);
  const id = hash('stable-owner-mailbox-transaction');
  const outbound = send('a',id,'source','wire');
  const inbound = receive('b','wire','opened');
  function crash(where, command) {
    // Fault injection is confined to this test child. Nothing in the production
    // protocol or CLI recognizes a fault option or a test environment variable.
    const failure = where === 'publish' ? `
      import fs from 'node:fs';
      import {syncBuiltinESMExports} from 'node:module';
      fs.linkSync = () => process.kill(process.pid,'SIGKILL');
      syncBuiltinESMExports();
    ` : `
      import {DatabaseSync} from 'node:sqlite';
      const prepare = DatabaseSync.prototype.prepare;
      DatabaseSync.prototype.prepare = function(sql) {
        if (sql.startsWith(${JSON.stringify('INSERT INTO '+where)})) process.kill(process.pid,'SIGKILL');
        return prepare.call(this,sql);
      };
    `;
    put('interrupt.mjs',failure);
    const result = spawnSync(process.execPath, ['--import',pathToFileURL(file('interrupt.mjs')).href,...args(...command)]);
    assert.equal(result.signal,'SIGKILL');
  }
  function query(owner,sql) {
    const db = new DatabaseSync(file(owner+'/signal-mail.sqlite'));
    try { return db.prepare(sql).get(); } finally { db.close(); }
  }
  crash('outgoing',outbound);
  assert.equal(query('a',"SELECT value FROM state WHERE name='session'"),undefined);
  crash('publish',outbound);
  const saved = query('a','SELECT packet FROM outgoing').packet;
  assert.equal(readdirSync(file('.')).filter(n=>n.startsWith('.signal-mail-')).length,1);
  call(...outbound);
  assert.deepEqual(bytes('wire'),Buffer.from(saved));
  assert.equal(readdirSync(file('.')).filter(n=>n.startsWith('.signal-mail-')).length,0);
  crash('incoming',inbound);
  assert.equal(query('b',"SELECT count(*) AS n FROM state WHERE name IN ('kyber','prekey')").n,2);
  assert.equal(query('b','SELECT count(*) AS n FROM incoming').n,0);
  crash('publish',inbound);
  assert.equal(query('b',"SELECT count(*) AS n FROM state WHERE name IN ('kyber','prekey')").n,0);
  call(...inbound);
  assert.deepEqual(bytes('opened'),mime);
  assert.equal(readdirSync(file('.')).filter(n=>n.startsWith('.signal-mail-')).length,0);
});
