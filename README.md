![VOLPAROSSA Mail banner with a fox and sealed envelope in a vintage postage-stamp design](docs/assets/banner-volparossa-mail.png)

# VOLPAROSSA Mail

**Keep your email address. Work toward more control over how mail arrives and stays.**

Changing mail infrastructure should not require abandoning the accounts and
contacts you already use. VOLPAROSSA Mail builds on Thunderbird, aiming to keep
ordinary email alongside encrypted delivery between compatible participants and
an owner-controlled mailbox using Stalwart.

The reusable [VOLPAROSSA core](https://github.com/VOLPAROSSA/volparossa) is intended
to handle peer delivery, encrypted offline custody and cooperative intelligence.
The Mail extension can already import a selected message from Thunderbird into a Stalwart
mailbox and confirm its stored metadata. Peer delivery remains in development.

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
work connects an owner-controlled instance. On Linux, an owner can provision a new
local mailbox and run it as a system service. Offline peer custody remains unfinished.

The intended service supports both ordinary Internet mail and preferred VOLPAROSSA
delivery. Arbitrary peers must not receive readable mail. Owner-controlled or deliberately
trusted SMTP reception is separate from ciphertext-only custody by peers. Stalwart's
mailbox-body encryption does not by itself protect reception, queued copies or metadata
from the receiving operator. Peer custody must encrypt the complete private message,
including sensitive headers, before sharing it; that cannot retroactively hide ordinary
SMTP plaintext from its receiver. Private offline Internet reception therefore requires
a separately implemented and verified receiving boundary. No confidential-hardware or
always-available delivery claim is made.

### Run an owner mailbox as a Linux service

The supplied systemd unit runs Stalwart Community independently of Thunderbird.
It opens a v0.16.24 SQLite mailbox. Stalwart reads the account, listeners and
certificates configured in that store. Review the listeners before starting it
and use unprivileged ports. The service runs under a dedicated account and
restarts after an unexpected exit.

From this checkout, install the unit and a verified Community executable on the
owner's system. Keep the original server stopped during a migration.

```sh
sudo systemd-sysusers packaging/systemd/volparossa-mail.conf
sudo install -d -m 0700 -o volparossa-mail -g volparossa-mail /var/lib/volparossa-mail
sudo install -D -m 0755 "$STALWART_BINARY" /usr/local/libexec/volparossa-mail/stalwart
sudo install -m 0644 packaging/systemd/store.json packaging/systemd/initialize-owner.py /usr/local/libexec/volparossa-mail/
sudo install -m 0644 packaging/systemd/volparossa-mail.service /etc/systemd/system/
sudo systemctl daemon-reload
```

For a new local mailbox, use your chosen address and enter its password at the
hidden prompt. The temporary setup process requires a private network namespace
because Stalwart's recovery listener binds all interfaces. The initializer opens
the new mailbox with the owner's credentials in normal mode, verifies the Inbox
and publishes the finished SQLite database atomically. Repeating the command
preserves an existing mailbox.

```sh
sudo systemd-run --quiet --pty --wait --collect -p PrivateNetwork=yes \
  -p User=volparossa-mail -p Group=volparossa-mail -p UMask=0077 -p LimitCORE=0 \
  /usr/bin/python3 -B /usr/local/libexec/volparossa-mail/initialize-owner.py owner@example.test
```

The new mailbox accepts JMAP on `http://127.0.0.1:8080` and inbound SMTP on
`127.0.0.1:2525`. The Mail host extension can connect with the full address and
the password you chose. For a migration, use Stalwart's backup and restore
procedure to prepare a consistent store at `/var/lib/volparossa-mail/store.sqlite`
with its associated files owned by `volparossa-mail`. Copying an active SQLite
file can lose uncheckpointed mail. Start the installed service after setup or
restoration.

```sh
sudo systemctl enable --now volparossa-mail.service
systemctl status volparossa-mail.service
```

The unit skips startup if the store is missing or empty. Use
`sudo systemctl disable --now volparossa-mail.service` to stop automatic startup
and leave the mailbox in place. External SMTP reception additionally needs a
trusted receiving host, domain routing, suitable TLS and a reachable endpoint.

### First connector: choose, connect, import

The [Mail host extension](extensions/volparossa-mail-host/manifest.json) provides an explicit
JMAP connection to your own Stalwart instance. With a user/app password held only in the
tab's memory, you choose an account and mailbox, then optionally import one selected `.eml`
file. It confirms that the stored message ID, destination mailbox and byte count match the import. If the
server returned a message ID but the readback failed, choose **Check stored metadata again**
in the same mailbox. The connector requests that message's metadata once. Disconnecting
clears the pending ID. An import with no confirmed ID requires inspecting the mailbox. This is
not a raw-message integrity proof or confirmation of delivery through VOLPAROSSA.

Nothing connects automatically. Remote origins require HTTPS and explicit host permission;
plain HTTP is restricted to literal loopback addresses. Use a remote operator only if you
deliberately trust it with the account's mail—not a randomly selected storage peer. The
extension does not send mail, collect existing Thunderbird account credentials, administer
Stalwart or start an SMTP service. An uncertain import is not silently retried.

For development, load the extension's `manifest.json` as a temporary Thunderbird add-on.
In **Thunderbird 157.0**, the toolbar action, host permission prompt and native file
picker were exercised against a real **Stalwart Community v0.16.24** mailbox. After the
import, a loopback observer verified Stalwart's successful metadata response and returned
HTTP 503 to Thunderbird. Clicking **Check stored metadata again** confirmed the same
message. The connector issued one upload, one import and two metadata reads. An independent
download from Stalwart matched all 313 selected MIME bytes.
[Thunderbird and Stalwart proof](third_party/thunderbird-stalwart-extension-smoke.json).

The connector now uploads the file chosen in Thunderbird directly. In a browser trial
at the 8 MiB limit, this removed one 8 MiB JavaScript copy while the server received
identical bytes. The mailbox and message requests also omit the unused folder role,
parent and Internet Message-ID. Thunderbird 157.0 repeated the import, explicit
metadata retry and Stalwart crash recovery with the updated connector.
[Native trial inputs and receipts](third_party/thunderbird-selected-file-smoke.json).

A separate native UI test used a controlled JMAP server and a 165-byte message.
[Controlled JMAP proof](third_party/thunderbird-extension-smoke.json).

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

After a separate adapter import, the fixture removed the selected message file,
killed Stalwart and confirmed that its listening socket had closed. A new
Stalwart process reopened the original SQLite database. The server refused an
incorrect password with HTTP 401. The original owner could open the same account
and mailbox and retrieve the exact 313 MIME bytes by the original message ID.
This used a disposable Community server in recovery mode. Add
`--crash-recovery` to the smoke command below to repeat it.
[Mailbox recovery receipt](third_party/stalwart-mailbox-crash-recovery/result.json)
· [Cleanup and host checks](third_party/stalwart-mailbox-crash-recovery/boundary.json).

The September run used the connector adapter directly. The November Thunderbird run
above exercised the same JMAP operations through the interface. A separate local
SMTP trial received mail after Thunderbird quit. Public Internet reception, peer
delivery and recovery of an installed background service remain open. [September adapter proof](third_party/stalwart-extension-smoke.json).

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
Public-domain deployment and the full account lifecycle remain unfinished. Run recovery tests
in a disposable VM or isolated namespace because the upstream recovery listener binds
to all interfaces by default. See the
[source preparation boundary](third_party/stalwart/NOTICE.md).

`scripts/smoke_stalwart.py` requires explicit paths and independently verified SHA-256
hashes for an existing Stalwart executable and Node runtime, plus a fresh directory under
`build/`. By default it validates inputs and prints the intended changes; `--execute`
runs the isolated test. It downloads or installs nothing. Run its offline fixture checks
with `python3 -B tests/test_stalwart_smoke.py`.

### Reproduce the Thunderbird mailbox recovery trial

Use a nonroot Linux x86_64 account with Python 3.12+, bubblewrap with user namespaces,
D-Bus, X11 client libraries including XTest, and xwininfo. Supply the official en-US
Thunderbird 157.0 archive, a verified Community SQLite Stalwart build and a verified Xvfb
executable. The pinned Mozilla SHA-512 and launcher SHA-256 are recorded in the
[release receipt](third_party/thunderbird-extension-smoke.json).

```sh
mkdir -p build
python3 -B scripts/smoke_thunderbird_stalwart.py \
  --thunderbird-archive "$THUNDERBIRD_ARCHIVE" \
  --stalwart "$STALWART_BINARY" --stalwart-sha256 "$STALWART_SHA256" \
  --xvfb "$XVFB_BINARY" --xvfb-sha256 "$XVFB_SHA256" \
  --output build/thunderbird-recovery --execute
```

Use absolute input paths and hashes obtained from the reviewed builds. Omit `--execute`
to validate the inputs and print the exact revisions. Each execution needs a fresh output
directory. The runner starts a private X display, a new Thunderbird profile and a Stalwart
server with only loopback available. It clicks the extension toolbar action, selects a
synthetic MIME file, interrupts the first metadata response and clicks the retry button.
It then quits Thunderbird and reads the saved mail from the running server. After an
isolated Stalwart process crash, it reopens the same SQLite file, checks that a wrong
password receives HTTP 401 and reads the original account, metadata and message bytes.
The three JSON receipts record the verified inputs, observed sequence and cleanup.

Add `--smtp` to the Thunderbird command above and choose a fresh output directory
to exercise ordinary owner reception. The test configures Stalwart's normal mode
on private loopback ports 2525 and 8080. After Thunderbird quits, a synthetic SMTP
sender submits 267 MIME bytes to the local owner. Stalwart accepts the message
with 250 and refuses both an unknown local recipient and an external relay
recipient with 550. The owner reads the message in the Inbox, then recovers
the same stored bytes, including the receiver's added headers, after Stalwart
restarts. The disposable namespace has no external route.
[SMTP and mailbox receipt](third_party/thunderbird-stalwart-owner-smtp/ui-restart.json)
· [Executable inputs](third_party/thunderbird-stalwart-owner-smtp/inputs.json)
· [Cleanup](third_party/thunderbird-stalwart-owner-smtp/boundary.json).

Use `--inbox` with a fresh output directory to open the delivered message in
Thunderbird’s ordinary IMAP inbox. The trial also needs `openssl` and the NSS
`certutil` command. It runs the SMTP and restart steps, then starts Thunderbird
with a fresh profile, a synthetic owner account and the fixture’s local certificate
authority. It selects the delivered subject in the message list and checks the
rendered body. The IMAP listener uses implicit TLS on loopback port 2993.
[Native inbox result](third_party/thunderbird-stalwart-native-inbox/ui-restart.json) ·
[Verified inputs](third_party/thunderbird-stalwart-native-inbox/inputs.json) ·
[Cleanup](third_party/thunderbird-stalwart-native-inbox/boundary.json)

The **Native Thunderbird owner inbox** workflow rebuilds the pinned Stalwart Community
server and repeats this trial when its inputs change. To run it manually, select the
workflow in Actions and choose **Run workflow**. The artifact contains the build,
mailbox and cleanup receipts. [Workflow](.github/workflows/native-owner-inbox.yml)

The verified Thunderbird 157.0 run recovered all 313 bytes after both lifecycle
checks. [UI and mailbox result](third_party/thunderbird-stalwart-mailbox-crash-recovery/ui-restart.json)
· [Runtime and source hashes](third_party/thunderbird-stalwart-mailbox-crash-recovery/inputs.json)
· [Cleanup and host network](third_party/thunderbird-stalwart-mailbox-crash-recovery/boundary.json)
