# Send a local Thunderbird draft to a paired contact

This Linux add-on sends the selected saved draft to the one address approved
in the owner’s Mail address book. Configure the two paired profiles and test
the address-bound sender first. The native host reads its configuration from
`$XDG_CONFIG_HOME/volparossa/mail` or `~/.config/volparossa/mail`. Keep the
Mail checkout and the pinned Node runtime available to Thunderbird.
[Pair and configure a contact](../../integration/signal-mail/README.md)

From that checkout, build the add-on and register the executable for the
current Linux account.

```sh
python3 -B scripts/peer_draft_install.py package volparossa-peer-draft.xpi
python3 -B scripts/peer_draft_install.py register
```

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
The native bridge passes the selected correspondence directly to the sender on
standard input. The recipient receives the message headers and original encoded MIME body,
including its attachments. The draft remains in Local Folders.
