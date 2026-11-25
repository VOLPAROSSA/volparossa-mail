# Thunderbird delivery and recovery

Each native owner Inbox ZIP is the original 13-file GitHub Actions artifact. It includes the packaged Thunderbird add-on, Stalwart build records, Inbox observations and the delivery receipts. Both runs used Thunderbird 157.0 and independently keyed provider stores on the same Linux machine.

| Native owner Inbox run | Source revision | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| [Saved draft and new message](native-owner-inbox-44558522297.zip) | [80ac0809](https://github.com/VOLPAROSSA/volparossa-mail/commit/80ac08099d536f2c86cac806f2b6bf3f0aab4a04) | 21,866 | `cd868bef3e14dfe291b2050af678974f3c6e761754211b5eb60f0d334311d150` |
| [Paired invitation, offline recovery and automatic pickup](native-owner-inbox-44562762968.zip) | [94f3162d](https://github.com/VOLPAROSSA/volparossa-mail/commit/94f3162d4b4ab89da25295a347b51c7857df1fec) | 22,142 | `c8534912d4e139712e65f18b970736e8d6c80bb2ee8e1badf2a8af3c74ba26ba` |

In both trials the sender clicked Send and a fresh receiving Thunderbird profile opened the delivery over verified TLS. The original 429-byte draft and all eight attachment bytes survived. Each new composition had its own message ID and exact MIME digest. The first was 808 bytes and the second was 833 bytes. Both providers acknowledged each import. The add-on bytes matched across runs. [Sender run](https://github.com/VOLPAROSSA/volparossa-mail/actions/runs/44558522297)

In the second trial two receivers recovered one interrupted import while the Core socket was disconnected. They verified the two providers’ individual signed receipts after the combined confirmation was lost. The watch later reconnected, received another 430-byte message automatically and stopped cleanly. The receipts are in `owner-inbox/peer-mail.json`, `peer-draft.json` and `peer-compose.json` inside the ZIP. [Receiver run](https://github.com/VOLPAROSSA/volparossa-mail/actions/runs/44562762968)

## Delivery between independent Core clients

A sender and recipient on separate Core client processes, sockets and network namespaces
took turns offline. Two providers held the encrypted message. After the receiver
imported all 90,139 original MIME bytes into Stalwart, both providers signed the
acknowledgements and the next fetch returned zero messages. The three phases record
peer identities, route selections, captures and host cleanup. These tests used
paired delivery inside a disposable twelve-node guest. Public SMTP reception is open.

| Original 36-file archive | Core source | Bytes | Archive SHA-256 |
| --- | --- | ---: | --- |
| [Independent clients](paired-mail-peers-45424248328.zip) | [`d272a11b`](https://github.com/VOLPAROSSA/volparossa/actions/runs/45424248328) | 35,643 | `0352630c680dd0ae2482bb074d2207b6fc8a8b8db86bfc89c832b008c196ee4c` |
| [Protected main repeat](paired-mail-peers-45510692398.zip) | [`e5daf201`](https://github.com/VOLPAROSSA/volparossa/actions/runs/45510692398) | 35,236 | `dd9f99657621c7af82ee8977eccf54b7b96cb1f5f269778a6261cb555342f278` |

Use the Core revision for the selected run and its
`tests/integration/paired-mail-peers-smoke.py report` command against the unpacked
`paired-mail-peers-smoke.json`, supplying the full source commit. It verifies the
report against the original observations, acknowledgements and before/after host hashes.
