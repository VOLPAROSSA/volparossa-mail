![VOLPAROSSA Mail banner with a fox and sealed envelope in a vintage postage-stamp design](docs/assets/banner-volparossa-mail.png)

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
The adapter has now passed a real Stalwart test; loading and operating the interface in
actual Thunderbird still needs validation. Neither establishes a complete self-hosted or
offline mail deployment.

### Verified: a real mailbox round trip

The pinned **Stalwart v0.16.24 Community** executable was built from unchanged source
with Rust 1.98.1, locked dependencies, SQLite and no Enterprise/default features. Its
development build then passed a disposable, loopback-only test on 30 September 2026:

- Provision a synthetic domain, restricted owner account and mailbox through Stalwart's
  current JMAP management API—not the outdated `/api` examples.
- Run the actual extension adapter with real network requests: discover the account,
  list its mailboxes, upload a selected message, import it and read its metadata back.
- Independently download the stored message and compare all **313 original MIME bytes**.
- Stop the processes, remove the temporary database, passwords, message and raw logs,
  and confirm that the host's route/DNS snapshot is unchanged.

The extension itself still promises metadata verification only; full-byte comparison was
an additional fixture check. The test uses recovery mode inside fresh user, network and
process namespaces. Its `[::]:8080` listener cannot reach the host or Internet, and the
upstream default web-interface download has no external network access. New mount targets
live under disposable `/opt/work`; synthetic addresses use `.test`, which the pinned
server accepts as a reserved domain. No real mail account or public SMTP service is used.

This proves the **connector's live JMAP import/readback**, not SMTP sending or receiving,
the Thunderbird interface, Signal Protocol/overlay delivery, offline Internet reception,
or the lifetime and recovery of an installed background mail service. Trusted mail hosting
remains distinct from opaque, encrypted storage on untrusted peers. Exact build, fixture
and evidence hashes are recorded in the
[scoped proof receipt](third_party/stalwart-extension-smoke.json).

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
download or already verified archive. The source-preparation manifest records that earlier
stage; the proof receipt above records the later passing development build and live test.
Production provisioning, account/bootstrap lifecycle and native Thunderbird integration
remain unfinished. Recovery testing belongs in a disposable VM or isolated namespace:
the upstream recovery listener is not loopback-only. See the
[source preparation boundary](third_party/stalwart/NOTICE.md).

`scripts/smoke_stalwart.py` requires explicit paths and independently verified SHA-256
hashes for an existing Stalwart executable and Node runtime, plus a fresh directory under
`build/`. By default it validates inputs and prints the intended changes; `--execute`
runs the isolated test. It downloads or installs nothing. Run its offline fixture checks
with `python3 -B tests/test_stalwart_smoke.py`.
