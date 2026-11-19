# Signal mail between paired owners

This Linux adapter encrypts an entire RFC 5322 file, including its original
headers, line endings and MIME parts, with the pinned native Signal Protocol
library. A profile represents one owner device paired to one remote device.
The pair can exchange messages in either direction without a Signal account or
a Signal server. Transport and Stalwart import run in their own components.

Install the locked dependency once with Node 24.19.0 in this directory.

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm test
```

Each owner runs the following commands on their own device. Supply existing
mode-0700 directories for handoffs and mode-0600 input files. The first
command creates the private profile and outputs only its public prekey bundle.

```sh
node cli.mjs init --profile "$profile" --bundle "$handoff/local.bundle"
node cli.mjs pair --profile "$profile" --peer-bundle "$handoff/peer.bundle" \
  --trusted-bundle-sha256 "$independently_verified_peer_fingerprint"
node cli.mjs seal --profile "$profile" --id "$stable_operation_id" \
  --input "$handoff/outgoing.eml" --output "$handoff/packet"
node cli.mjs open --profile "$profile" --input "$handoff/received-packet" \
  --output "$handoff/incoming.eml"
```

Each owner obtains the SHA-256 fingerprint from the other owner's trusted
device through an independent authenticated channel. A returned bundle and
fingerprint from the same untrusted transport cannot establish peer identity.
Both owners explicitly pair their profiles. Each exact bundle fingerprint binds
the device address, native identity, signed EC/PQ prekeys and one-time EC prekey.
For a new device or a lost profile, pair fresh profiles with newly verified
fingerprints. The address and cryptographic identity belong to this Mail pair.

Use the same 64-digit lowercase hexadecimal operation ID for retries of one
outgoing mail transaction. Distinct submissions can use different IDs for
identical mail bytes. `seal` and `open` print a JSON record containing
`operation`, `packet_sha256`, `message_sha256` and `message_bytes`.
`init` and `pair` report `bundle_sha256`. The file passed between owners is a
small version/type prefix followed by the unmodified native Signal ciphertext.
The total packet must fit within Core's four-MiB private-message limit.

The private SQLite profile commits session changes, consumed prekeys and replay
data together. Repeating a successful operation can republish the same bytes
to a new file. An existing output must already contain the exact bytes.
Opened mail and outgoing ciphertext remain in the profile to support retries.
Protect the profile and its backups like a local mailstore. It contains local
secret keys and message content, with owner-only filesystem permissions.
The adapter performs no network operations and never submits mail over SMTP.
