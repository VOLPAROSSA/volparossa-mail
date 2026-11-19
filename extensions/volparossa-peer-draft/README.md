# Send a local Thunderbird draft to a paired contact

This Linux add-on sends the selected saved draft to the one address approved
in the owner’s Mail address book. Use `contacts.py pair` on each owner’s
device to authorize the other address and its trusted identities. The native host reads its configuration from
`$XDG_CONFIG_HOME/volparossa/mail` or `~/.config/volparossa/mail`. Keep the
Mail checkout and the Node runtime used for pairing at their registered paths.
[Pair and configure a contact](../../integration/signal-mail/README.md#send-a-draft-to-its-explicitly-paired-email-address)

From that checkout, build the add-on and register the executable for the
current Linux account.

```sh
python3 -B scripts/peer_draft_install.py package volparossa-peer-draft.xpi
python3 -B scripts/peer_draft_install.py register
```

Registration remembers the Node executable on your shell’s PATH. To select a
different installation, run `register --node /absolute/path/to/node`.

In Thunderbird, open Add-ons Manager and choose **Install Add-on From File**.
Select the XPI. The development package can also be loaded under **Debug
Add-ons** for the current session. The add-on requests access to the selected
message, its account and the native host. The native host registration lists
this add-on’s exact extension ID.

1. Save the draft in **Local Folders → Drafts**. Thunderbird’s account settings
   let you choose this destination under **Copies & Folders**.
2. Select one saved draft, click **Send draft to paired contact** on the toolbar,
   review the displayed sender, recipient and subject, then click **Send privately**.
3. When both providers confirm custody, the recipient can pick up the message.
   An unconfirmed result can be retried with the same saved correspondence.
   For an address that needs ordinary mail, return to Thunderbird and choose
   that delivery yourself.

Thunderbird’s local status flags, tags and draft bookkeeping stay on the sender.
Private delivery requires the attached file itself. For a Filelink, detached or
deleted attachment, attach the original file in Thunderbird and save the draft again.
The encrypted message must fit within 4 MiB, including its Signal overhead.
Thunderbird asks you to reduce a draft that exceeds this limit.
The native bridge passes the selected correspondence directly to the sender on
standard input. The recipient receives the message headers and original encoded MIME body,
including its attachments. The draft remains in Local Folders.
