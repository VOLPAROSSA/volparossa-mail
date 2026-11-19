// SPDX-License-Identifier: GPL-3.0-only
import { randomInt, randomUUID } from 'node:crypto';
import {
  CiphertextMessageType, IdentityChange, IdentityKeyPair, KEMKeyPair, KEMPublicKey,
  KyberPreKeyRecord, PreKeyBundle, PreKeyRecord, PreKeySignalMessage, PrivateKey,
  ProtocolAddress, PublicKey, SessionRecord, SignedPreKeyRecord, SignalMessage,
  processPreKeyBundle, signalDecrypt, signalDecryptPreKey, signalEncrypt,
} from '@signalapp/libsignal-client';
import { MailError } from './files.mjs';

// The existing Core private-message CLI accepts at most 4 MiB, including framing.
export const MAX_PACKET = 4 * 1024 * 1024;
const MAGIC = Buffer.from('VMAIL\x01', 'latin1');
const b64 = bytes => Buffer.from(bytes).toString('base64');
const address = bundle => ProtocolAddress.new(bundle.address, 1);

export function makeLocal(put) {
  const identity = IdentityKeyPair.generate();
  const ec = PrivateKey.generate();
  const signed = PrivateKey.generate();
  const pq = KEMKeyPair.generate();
  const signedSignature = identity.privateKey.sign(signed.getPublicKey().serialize());
  const pqSignature = identity.privateKey.sign(pq.getPublicKey().serialize());
  const time = Date.now();
  put('identity', identity.serialize());
  put('prekey', PreKeyRecord.new(1, ec.getPublicKey(), ec).serialize());
  put('signed', SignedPreKeyRecord.new(1, time, signed.getPublicKey(), signed, signedSignature).serialize());
  put('kyber', KyberPreKeyRecord.new(1, time, pq, pqSignature).serialize());
  return Buffer.from(JSON.stringify({
    version: 1, address: randomUUID(), registration: randomInt(1, 16384),
    identity: b64(identity.publicKey.serialize()), prekey: b64(ec.getPublicKey().serialize()),
    signed: b64(signed.getPublicKey().serialize()), signedSignature: b64(signedSignature),
    kyber: b64(pq.getPublicKey().serialize()), kyberSignature: b64(pqSignature),
  }) + '\n');
}

function binary(value) {
  if (typeof value !== 'string') throw Error();
  const bytes = Buffer.from(value, 'base64');
  if (!bytes.length || b64(bytes) !== value) throw Error();
  return bytes;
}

export function decodeBundle(raw) {
  const b = JSON.parse(Buffer.from(raw).toString('utf8'));
  if (b.version !== 1 || !/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(b.address) ||
      !Number.isInteger(b.registration) || b.registration < 1 || b.registration > 16383) throw Error();
  const identity = PublicKey.deserialize(binary(b.identity));
  const signed = PublicKey.deserialize(binary(b.signed));
  const kyber = KEMPublicKey.deserialize(binary(b.kyber));
  const ecSignature = binary(b.signedSignature);
  const pqSignature = binary(b.kyberSignature);
  if (!identity.verify(signed.serialize(), ecSignature) || !identity.verify(kyber.serialize(), pqSignature)) throw Error();
  const native = PreKeyBundle.new(b.registration, 1, 1, PublicKey.deserialize(binary(b.prekey)),
    1, signed, ecSignature, identity, 1, kyber, pqSignature);
  return { address: address(b), registration: b.registration, identity, native };
}

export function channel(state, local, peer) {
  // A profile has exactly one independently authenticated remote identity and device.
  function expected(addr) {
    if (addr.name() !== peer.address.name() || addr.deviceId() !== peer.address.deviceId()) throw Error();
  }
  function record(name, type, id) {
    if (id !== 1) throw Error();
    return type.deserialize(state.get(name));
  }
  const identities = {
    async getIdentityKeyPair() { return IdentityKeyPair.deserialize(state.get('identity')); },
    async getLocalRegistrationId() { return local.registration; },
    async getIdentity(addr) { expected(addr); return peer.identity; },
    async isTrustedIdentity(addr, key) { expected(addr); return peer.identity.equals(key); },
    async saveIdentity(addr, key) {
      expected(addr);
      if (!peer.identity.equals(key)) throw Error();
      return IdentityChange.NewOrUnchanged;
    },
  };
  const sessions = {
    async getSession(addr) {
      expected(addr);
      const bytes = state.get('session');
      return bytes ? SessionRecord.deserialize(bytes) : null;
    },
    async saveSession(addr, session) { expected(addr); state.put('session', session.serialize()); },
  };
  const prekeys = {
    async getPreKey(id) { return record('prekey', PreKeyRecord, id); },
    async removePreKey(id) { if (id !== 1) throw Error(); state.remove('prekey'); },
  };
  const signed = { async getSignedPreKey(id) { return record('signed', SignedPreKeyRecord, id); } };
  const kyber = {
    async getKyberPreKey(id) { return record('kyber', KyberPreKeyRecord, id); },
    // This is a single-use prekey dedicated to this owner pair.
    async markKyberPreKeyUsed(id) { if (id !== 1) throw Error(); state.remove('kyber'); },
  };
  const args = [peer.address, local.address, sessions, identities];
  return {
    async encrypt(message) {
      if (!await sessions.getSession(peer.address)) await processPreKeyBundle(peer.native, ...args);
      const ciphertext = await signalEncrypt(message, ...args);
      const packet = Buffer.concat([MAGIC, Buffer.from([ciphertext.type()]), ciphertext.serialize()]);
      if (packet.length > MAX_PACKET) throw new MailError('encrypted message exceeds the Core 4 MiB limit');
      return packet;
    },
    async decrypt(packet) {
      if (!packet.subarray(0, MAGIC.length).equals(MAGIC)) throw Error();
      const wire = packet.subarray(MAGIC.length + 1);
      switch (packet[MAGIC.length]) {
        case CiphertextMessageType.PreKey:
          return Buffer.from(await signalDecryptPreKey(PreKeySignalMessage.deserialize(wire), ...args, prekeys, signed, kyber));
        case CiphertextMessageType.Whisper:
          return Buffer.from(await signalDecrypt(SignalMessage.deserialize(wire), ...args));
        default: throw Error();
      }
    },
  };
}
