// SPDX-License-Identifier: GPL-3.0-only
import { createHash } from 'node:crypto';
import { lstatSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { parseArgs } from 'node:util';
import { MailError, MessageTooLarge, privateDir, publish, read, readExact, syncDirectory } from './files.mjs';
import { channel, decodeBundle, makeLocal, MAX_PACKET } from './protocol.mjs';
import { bindCoreOwner, deposit, routeMail, stageRoute } from './route.mjs';

const fields = new Map([
  ['init', ['bundle']], ['pair', ['peer-bundle', 'trusted-bundle-sha256']],
  ['seal', ['id', 'input', 'output']], ['open', ['input', 'output']],
  ['send', ['id', 'core-config']],
]);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

async function seal(db, session, values, input = read(values.input, MAX_PACKET, { message: true })) {
  const messageHash = values['stdin-sha256'] ?? hash(input);
  const existing = db.prepare('SELECT message_sha256,packet FROM outgoing WHERE id=?').get(values.id);
  if (existing && existing.message_sha256 !== messageHash) throw new MailError('operation id already seals different message bytes');
  const packet = existing?.packet ?? await session.encrypt(input);
  if (!existing) db.prepare('INSERT INTO outgoing VALUES (?,?,?)').run(values.id, messageHash, packet);
  return { packet, message_sha256: messageHash, message_bytes: input.length };
}

async function run() {
  process.umask(0o077);
  const operation = process.argv[2];
  if (!fields.has(operation)) throw new MailError('expected init, pair, seal, open or send');
  const names = ['profile', ...fields.get(operation)];
  const options = Object.fromEntries(names.map(name => [name, { type: 'string' }]));
  if (operation === 'pair' || operation === 'send') options['core-owner-key'] = { type: 'string' };
  if (operation === 'send') {
    options.resume = { type: 'boolean' };
    options['prepare-only'] = { type: 'boolean' };
    options['peer-bundle-sha256'] = { type: 'string' };
    options.input = { type: 'string' };
    options['stdin-size'] = { type: 'string' };
    options['stdin-sha256'] = { type: 'string' };
    options.invitation = { type: 'string' };
  }
  const { values } = parseArgs({ args: process.argv.slice(3), options });
  if (names.some(name => !values[name])) throw new MailError('missing command arguments');

  let input;
  if (operation === 'send') {
    const stream = values['stdin-size'] !== undefined || values['stdin-sha256'] !== undefined;
    if (values.resume ? (stream || values.input || values.invitation || values['prepare-only'])
        : (Boolean(values.input) === stream || !values.invitation || !values['core-owner-key'])) {
      throw new MailError('choose the original mail, invitation and owner key, or resume the retained send');
    }
    if (stream) {
      const size = Number(values['stdin-size']);
      if (String(size) !== values['stdin-size'] || !Number.isSafeInteger(size) || size < 1 ||
          !digest(values['stdin-sha256'])) {
        throw new MailError('invalid selected message length or digest');
      }
      if (size > MAX_PACKET) throw new MessageTooLarge('input_exceeds_limit');
      // Read before opening the native ratchet transaction or waiting on a provider.
      input = readExact(0, size);
      if (hash(input) !== values['stdin-sha256']) throw new MailError('selected message digest differs');
    }
  }
  if (operation === 'init') {
    try { mkdirSync(values.profile, { mode: 0o700 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    syncDirectory(dirname(resolve(values.profile)));
  }
  const directory = privateDir(values.profile);
  const target = values.output ?? values.bundle;
  if (target) privateDir(dirname(target));
  const file = join(directory, 'signal-mail.sqlite');
  const st = lstatSync(file, { throwIfNoEntry: false });
  if (st ? (!st.isFile() || st.nlink !== 1 || st.uid !== process.getuid() || (st.mode & 0o077)) : operation !== 'init') throw Error();
  let route, routeDirectory, result;
  const db = new DatabaseSync(file);
  try {
    db.exec('PRAGMA busy_timeout=30000; PRAGMA synchronous=FULL; PRAGMA secure_delete=ON; BEGIN IMMEDIATE');
    if (operation === 'init') db.exec(`
      CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value BLOB NOT NULL) STRICT;
      CREATE TABLE IF NOT EXISTS outgoing (id TEXT PRIMARY KEY, message_sha256 TEXT NOT NULL, packet BLOB NOT NULL) STRICT;
      CREATE TABLE IF NOT EXISTS incoming (packet_sha256 TEXT PRIMARY KEY, message BLOB NOT NULL) STRICT;
    `);
    const state = {
      get(name) { return db.prepare('SELECT value FROM state WHERE name=?').get(name)?.value; },
      put(name, value) { db.prepare('INSERT OR REPLACE INTO state VALUES (?,?)').run(name, value); },
      remove(name) { db.prepare('DELETE FROM state WHERE name=?').run(name); },
    };

    let output;
    if (operation === 'init') {
      output = state.get('local');
      if (!output) { output = makeLocal(state.put); state.put('local', output); }
      result = { bundle_sha256: hash(output) };
    } else {
      const local = decodeBundle(state.get('local'));
      const previous = state.get('peer');
      if (operation === 'pair') {
        const raw = read(values['peer-bundle'], 8192);
        const fingerprint = values['trusted-bundle-sha256'];
        if (!digest(fingerprint) || fingerprint !== hash(raw)) throw new MailError('trusted peer fingerprint mismatch');
        if (previous && !Buffer.from(previous).equals(raw)) throw new MailError('profile already paired to another device');
        const peer = decodeBundle(raw);
        if (peer.address.name() === local.address.name() || peer.identity.equals(local.identity)) throw new MailError('cannot pair to the local identity');
        if (values['core-owner-key'] !== undefined) bindCoreOwner(state, values['core-owner-key']);
        state.put('peer', raw);
        result = { bundle_sha256: fingerprint };
      } else {
        if (!previous) throw new MailError('owner pairing required');
        if (operation === 'send' && values['peer-bundle-sha256'] !== undefined &&
            values['peer-bundle-sha256'] !== hash(previous)) throw new MailError('selected peer fingerprint differs from paired device');
        const peer = decodeBundle(previous);
        const session = channel(state, local, peer);
        if (operation !== 'open') {
          if (!digest(values.id)) throw new MailError('expected a 64-digit lowercase hexadecimal operation id');
          const sealed = values.resume ? undefined : await seal(db, session, values, input);
          input = undefined;
          route = operation === 'send' ? routeMail(db, state, values, sealed) : undefined;
          const { packet, message_sha256, message_bytes } = route ?? sealed;
          output = packet;
          result = { packet_sha256: hash(packet), message_sha256, message_bytes };
        } else {
          const input = read(values.input, MAX_PACKET);
          const inputHash = hash(input);
          const existing = db.prepare('SELECT message FROM incoming WHERE packet_sha256=?').get(inputHash);
          output = existing?.message ?? await session.decrypt(input);
          if (!existing) db.prepare('INSERT INTO incoming VALUES (?,?)').run(inputHash, output);
          result = { packet_sha256: inputHash, message_sha256: hash(output), message_bytes: output.length };
        }
      }
    }
    // The ratchet, one-time-key consumption and replay bytes commit together.
    // A caller can republish the exact bytes even after dying before output or import.
    db.exec('COMMIT');
    if (target || route) {
      db.exec('BEGIN IMMEDIATE');
      if (target) publish(target, output, hash(state.get('local')));
      if (route) routeDirectory = stageRoute(directory, values.id, route);
      db.exec('COMMIT');
    }
  } finally { db.close(); }
  // Waiting for two providers never takes the local Signal ratchet lock.
  if (route) {
    if (values['prepare-only']) result.status = 'prepared';
    else Object.assign(result, deposit(routeDirectory, values['core-config'], route.owner));
  }
  return { operation, ...result };
}

try { console.log(JSON.stringify(await run())); }
catch (error) {
  if (error instanceof MessageTooLarge) {
    console.log(JSON.stringify({ operation: process.argv[2], status: 'message_too_large',
      reason: error.reason, limit_bytes: MAX_PACKET }));
    process.exitCode = 4;
  } else {
    console.error(error instanceof MailError ? error.message : 'signal-mail operation failed');
    process.exitCode = 1;
  }
}
