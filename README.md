# Project VOLPAROSSA Mail

Thunderbird integration for the VOLPAROSSA **Decentralized Intelligent Cooperative
Network**: familiar mail accounts, cooperative intelligence and a path toward private,
decentralized delivery and self-hosted mail.

**Development integration, not a finished mail client.** No production account, mail
server, DNS record or network participation is activated by this repository.

## Keep your mail, add the network

The intended client preserves existing accounts and ordinary mail. Compatible peers
should exchange messages through VOLPAROSSA using the actual Signal Protocol, with
encrypted store-and-forward delivery when a recipient is offline. Stored by peers,
imported by a recipient and read by a user are distinct states. Missing acknowledgements
must not silently trigger ordinary-mail retransmission or hide a delivery failure.

The core is developed in [volparossa](https://github.com/VOLPAROSSA/volparossa).
Mailbox transport, application import, authenticated address/device discovery and Signal
sessions still need to be joined and tested in real Thunderbird. Existing core HPKE
messages are not a substitute for the Signal Protocol.

## Cooperative intelligence

The first source overlay adds **Tools → VOLPAROSSA AI** with a separate core adapter.
It accepts explicitly reviewed **public** text only: the user must confirm sharing,
rights and a supported license before dispatch. It does not harvest mail or attachments,
send private correspondence to compute peers, train on mailbox contents or fall back to
a cloud service. The present public compute endpoint does not provide confidential peer
inference.

This is not a native Thunderbird Assist provider replacement. ThunderAI is being evaluated
as an alternative mail-facing interface; it is not yet bundled or connected. Source tests
and an exact upstream overlay do not prove a working Thunderbird/peer deployment.

## Own your mailbox

The requested self-hosting extension uses **Stalwart Community** as a separate background
mail service. Closing Thunderbird must not shut down that service. The first integration
work connects an explicitly configured owner-controlled instance; automatic server
provisioning, account setup and offline peer custody remain unfinished.

The intended service supports both ordinary Internet mail and preferred VOLPAROSSA
delivery. Arbitrary peers must not receive readable mail. Owner-controlled or deliberately
trusted SMTP reception is separate from ciphertext-only custody by peers. Stalwart's
mailbox-body encryption does not by itself protect reception, queued copies or metadata
from the receiving operator. Peer custody must encrypt the complete private message,
including sensitive headers, before sharing it; that cannot retroactively hide ordinary
SMTP plaintext from its receiver. Private offline Internet reception therefore requires
a separately implemented and verified receiving boundary. No confidential-hardware or
always-available delivery claim is made.

### First connector: choose, connect, import

The [Mail host extension](extensions/volparossa-mail-host/manifest.json) provides an explicit
JMAP connection to your own Stalwart instance. With a user/app password held only in the
tab's memory, you choose an account and mailbox, then optionally import one selected `.eml`
file. It checks the import response and reads the stored message's metadata back. This is
not a raw-message integrity proof or confirmation of delivery through VOLPAROSSA.

Nothing connects automatically. Remote origins require HTTPS and explicit host permission;
plain HTTP is restricted to literal loopback addresses. Use a remote operator only if you
deliberately trust it with the account's mail—not a randomly selected storage peer. The
extension does not send mail, collect existing Thunderbird account credentials, administer
Stalwart or start an SMTP service. An uncertain import is not silently retried.

For development, load the extension's `manifest.json` as a temporary Thunderbird add-on.
It still needs validation in actual Thunderbird against the pinned Stalwart server. The
offline adapter tests establish request/response behavior, not a working self-hosted or
offline mail deployment.

## Source development

Thunderbird is pinned to **157.0**, commit
`c747bb0160f873ab78692b2450612b232d38c30e`; its paired Gecko revision is recorded in
[`patches/thunderbird-source.json`](patches/thunderbird-source.json).

```sh
# Explicit source-only download; creates a fresh overlay, not a complete browser build.
python3 -B scripts/prepare_source.py --download --output build/thunderbird-overlay

# Requires an existing Node executable, optionally selected by VOLPAROSSA_TEST_NODE.
python3 -B tests/test_integration.py

# Explicit, separate Stalwart source preparation; no server is started.
python3 -B scripts/prepare_stalwart.py --fetch --output build/stalwart-0.16.24
python3 -B scripts/prepare_stalwart.py --validate build/stalwart-0.16.24

# Offline connector contracts; use an existing Node executable.
node --test extensions/volparossa-mail-host/tests/jmap_contract.mjs
```

The upstream source revision, input hashes and unchanged MPL notice are checked before
patching. Original integration code is GPL-3.0-only; upstream licenses remain unchanged.
See [third-party provenance](THIRD_PARTY_LICENSES.md).

Stalwart Community is separately pinned to **v0.16.24**,
`af37a234981722493b74623a983581691d2b70b6`, with its original licenses and complete source
inventory retained. Preparation requires a fresh output directory and an explicit source
download or already verified archive. Its Community build, account/bootstrap lifecycle and
native mailbox proof remain unfinished. Bootstrap first belongs in a disposable VM: the
upstream recovery listener is not loopback-only. See the
[source preparation boundary](third_party/stalwart/NOTICE.md).
