// SPDX-License-Identifier: GPL-3.0-only
import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { MailError, privateDir, publish, read, syncDirectory } from './files.mjs';

const ownerKey = key => typeof key === 'string' && /^[a-f0-9]{64}$/.test(key);
const storedOwner = state => Buffer.from(state.get('core-owner') ?? []).toString('hex');

export function bindCoreOwner(state, key) {
  if (!ownerKey(key)) throw new MailError('invalid trusted Core owner key');
  const existing = storedOwner(state);
  if (existing && existing !== key) throw new MailError('profile already paired to another Core owner');
  state.put('core-owner', Buffer.from(key,'hex'));
}

export function routeMail(db, state, values, sealed) {
  const owner = storedOwner(state);
  if (!owner || (values['core-owner-key'] !== undefined && values['core-owner-key'] !== owner)) {
    throw new MailError('expected the independently paired Core owner');
  }
  // Existing profiles gain the route journal on their first explicit Core send.
  db.exec('CREATE TABLE IF NOT EXISTS mail_routes (id TEXT PRIMARY KEY, invitation BLOB NOT NULL, message_bytes INTEGER NOT NULL) STRICT');
  if (!values.resume) {
    const invitation = read(values.invitation,16384);
    const prior = db.prepare('SELECT invitation FROM mail_routes WHERE id=?').get(values.id);
    if (prior && !Buffer.from(prior.invitation).equals(invitation)) throw new MailError('operation already uses a different invitation');
    if (!prior) db.prepare('INSERT INTO mail_routes VALUES (?,?,?)').run(values.id,invitation,sealed.message_bytes);
  }
  const row = db.prepare(`
    SELECT outgoing.packet, outgoing.message_sha256, mail_routes.invitation, mail_routes.message_bytes
    FROM outgoing JOIN mail_routes USING(id) WHERE id=?
  `).get(values.id);
  if (!row) throw new MailError('no retained send for this operation id');
  return { ...row, owner };
}

export function stageRoute(directory, id, route) {
  let state = directory;
  for (const name of ['core-send',id]) {
    state = join(state,name);
    try { mkdirSync(state, { mode: 0o700 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
    privateDir(state);
    syncDirectory(dirname(state));
  }
  publish(join(state,'packet'), route.packet, id);
  publish(join(state,'invitation'), route.invitation, id);
  return state;
}

export function deposit(directory, config, owner) {
  const worker = fileURLToPath(new URL('./deposit.py', import.meta.url));
  const { status, stdout } = spawnSync('/usr/bin/python3', ['-I','-B',worker,directory,config,owner], {
    stdio: ['ignore','pipe','ignore'], env: { PATH:'/usr/bin:/bin', LANG:'C.UTF-8' }, maxBuffer: 16384,
  });
  if (status !== 0) throw new MailError('Core send incomplete; retry this operation with --resume');
  const result = JSON.parse(stdout);
  if (result.confirmed_providers !== 2 || !ownerKey(result.message_id)) throw Error();
  return result;
}
