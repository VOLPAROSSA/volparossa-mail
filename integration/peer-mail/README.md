# Receive Signal-encrypted mail into an owner mailbox

An owner can accept a complete RFC 5322 message from a specific Core handoff.
The sender selects the original message, including its headers and attachments,
and uses the paired Signal Mail profile’s `send` command. It encrypts the message
and deposits it with both providers named by the recipient’s signed invitation.
The recipient can be offline during deposit. [Pair the devices and send a message](../signal-mail/README.md#send-a-selected-message-through-the-recipients-core-mailbox).

Use the normal Core client to fetch from the recipient's invitation into a new
private directory. Retain that directory until Stalwart and both providers confirm
the handoff. Core's `fetch` command leaves both providers in custody. Supply the
mailbox owner's identity and its existing passphrase file.

```sh
volparossa --control-socket "$SOCKET" content mailbox fetch \
  --invitation "$INVITATION" --output-dir "$FRESH_OUTPUT" \
  --identity "$OWNER_IDENTITY" --passphrase-file "$OWNER_PASSPHRASE_FILE"
```

Create an owner-only directory (mode 0700) for the local delivery journal and
a private connection file (mode 0600, in a directory with mode 0700). Choose
the account and destination mailbox IDs from the owner's JMAP server. Point `coreConfig` to the same owner-only Core configuration used to fetch the
message. It contains absolute `socket`, `identity` and `passphrase` file paths.

```json
{
  "server": "http://127.0.0.1:8080",
  "username": "owner@example.test",
  "accountId": "b",
  "mailboxId": "a",
  "coreConfig": "/home/owner/.config/volparossa/core.json"
}
```

Run the receiver from the Mail checkout and enter the mailbox password at its
hidden prompt. A service can supply a one-line password on standard input.
Use the particular message directory named by the Core fetch receipt.

```sh
python3 -B integration/peer-mail/accept.py \
  --profile "$SIGNAL_RECIPIENT_PROFILE" --handoff "$FRESH_OUTPUT/$MESSAGE_ID" \
  --connection "$PRIVATE_CONNECTION_FILE" --state-dir "$PRIVATE_JOURNAL"
```

The receiver opens the Signal packet, imports its exact MIME bytes into the chosen
mailbox, downloads the stored message and compares every byte. It then asks Core
to acknowledge the original opaque packet to both providers. The receipt reports
the account, email ID, original size, digests and provider acknowledgements. The
operator of the selected Stalwart server can read the imported correspondence.

Stalwart commits an opaque delivery keyword with its email. A retry uses this
keyword to find an import whose reply was lost, including after a server restart.
Each attempted import uses the original saved JMAP Email state. An unrelated
state change with no matching email leaves an uncertain handoff for the owner to
reconcile. Keep the original Signal profile, journal and handoff when the command
reports an incomplete operation. The receipt confirms mailbox import. Read status
belongs to the mail client.

With the owner service’s optional IMAPS listener configured, open the selected
mailbox in Thunderbird using implicit TLS on 127.0.0.1 port 2993. The native
trial starts a fresh Thunderbird 157 profile after delivery, selects the message
in its ordinary Inbox and checks the displayed subject, Message-ID and body.
It uses fictional accounts, two independently keyed Core stores served over
local sockets and an actual Stalwart Community process. Automatic
address discovery, Thunderbird composition and protected multi-node mail routing
remain separate integration work.
