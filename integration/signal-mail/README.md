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

## Send a selected message through the recipient's Core mailbox

On the sender, include `--core-owner-key "$trusted_core_owner"` when pairing.
An existing Signal-only profile can repeat its original pairing with this
option. Obtain the recipient's Ed25519 Core identity through an independent
authenticated channel and associate it with the same intended device as the
trusted Signal bundle. The profile pins this choice.

```sh
node cli.mjs send --profile "$profile" --id "$stable_operation_id" \
  --input "$handoff/outgoing.eml" --invitation "$handoff/recipient.invitation" \
  --core-owner-key "$trusted_core_owner" --core-config "$config"
node cli.mjs send --profile "$profile" --id "$stable_operation_id" \
  --resume --core-config "$config"
```

The private JSON config contains absolute paths for `executable` (Core CLI),
`socket` (the local agent), `identity` (the sending owner's identity file) and
`passphrase` (its private passphrase file). The existing dual-direction Core
fixture config is also supported when its `remoteOwner` equals the trusted
owner. The Core CLI verifies the recipient-signed invitation, the allowed
sender and each provider's signed custody response.

The first command seals the original bytes and commits the exact invitation and
opaque packet before contacting the Core daemon. Resume works without the
original source or invitation file. An operation that has a Core manifest always
resumes that original publication. The sender keeps both provider receipts in
its private profile. Successful JSON includes `confirmed_providers: 2` and the
opaque `message_id`. Recipient import and read status are separate evidence.

## Send a draft to its explicitly paired email address

The Thunderbird host can hand a complete original draft to the address-aware
sender. Keep an owner-private (0700) directory containing a mode-0600
`address-book.json`. Its address belongs to the sending owner. Each contact
associates one explicit mailbox address with an already paired local profile,
the independently verified Signal bundle fingerprint and the independently
verified Core owner key. Use the actual canonical paths and keys from pairing.

```json
{
  "version": 1,
  "address": "alice@example.test",
  "contacts": [{
    "address": "bob@example.test",
    "profile": "/private/path/to/alice-bob-profile",
    "peer_bundle_sha256": "<64 lowercase hex digits>",
    "core_owner_key": "<64 lowercase hex digits>",
    "invitation": "/private/path/to/original-invitation",
    "core_config": "/private/path/to/core.json"
  }]
}
```

```sh
python3 integration/peer-mail/send_selected.py --owner "$owner_directory" \
  --id "$stable_operation_id" --input "$draft_file"
python3 integration/peer-mail/send_selected.py --owner "$owner_directory" \
  --id "$stable_operation_id" --resume
```

A desktop integration can supply the same complete message on binary standard
input with `--stdin`. Close the stream after the last byte. File and stream
input share the 4 MiB limit, address checks and operation ID. Replaying either
form requires an exact byte match.


The sender reads the original header block and requires a single matching
From mailbox. An explicit Sender must name that same mailbox. Across To, Cc
and Bcc there must be exactly one individual recipient. Domain matching
ignores case, and the local part must match exactly. Address groups, resent
messages, ambiguous headers and unpaired recipients require an explicit
ordinary-mail decision. The result carries `status=ordinary_mail_required`
and exit code 3. This command performs no ordinary-mail submission.

The initial choice commits the original draft digest, trusted pair, invitation
and Core configuration. Once the native Signal profile durably owns the exact
packet, the address selector releases its extra draft copy. Subsequent retries
use the originally selected profile and invitation even if the owner edits or
removes the address book. Successful JSON carries `status=provider_custody`,
`confirmed_providers=2` and the opaque Core `message_id`. A pending provider
operation returns exit code 1 with `status=retry_selected_send`; its retry
retains the authorised private route. The command finds Node on PATH or accepts
its explicit location with `--node`.
