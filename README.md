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
delivery. Arbitrary peers must not receive readable mail. Encrypting an ordinary SMTP
message after reception protects storage but not a malicious receiving host; private
offline Internet reception therefore requires a separately implemented and verified
receiving boundary. No confidential-hardware or always-available delivery claim is made.

## Source development

Thunderbird is pinned to **157.0**, commit
`c747bb0160f873ab78692b2450612b232d38c30e`; its paired Gecko revision is recorded in
[`patches/thunderbird-source.json`](patches/thunderbird-source.json).

```sh
# Explicit source-only download; creates a fresh overlay, not a complete browser build.
python3 -B scripts/prepare_source.py --download --output build/thunderbird-overlay

# Requires an existing Node executable, optionally selected by VOLPAROSSA_TEST_NODE.
python3 -B tests/test_integration.py
```

The upstream source revision, input hashes and unchanged MPL notice are checked before
patching. Original integration code is GPL-3.0-only; upstream licenses remain unchanged.
See [third-party provenance](THIRD_PARTY_LICENSES.md).
