# Thunderbird delivery and recovery

Each ZIP is the original 13-file GitHub Actions artifact. It includes the packaged Thunderbird add-on, Stalwart build records, Inbox observations and the delivery receipts. Both runs used Thunderbird 157.0 and independently keyed provider stores on the same Linux machine.

| Native owner Inbox run | Source revision | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| [Saved draft and new message](native-owner-inbox-44558522297.zip) | [80ac0809](https://github.com/VOLPAROSSA/volparossa-mail/commit/80ac08099d536f2c86cac806f2b6bf3f0aab4a04) | 21,866 | `cd868bef3e14dfe291b2050af678974f3c6e761754211b5eb60f0d334311d150` |
| [Paired invitation, offline recovery and automatic pickup](native-owner-inbox-44562762968.zip) | [94f3162d](https://github.com/VOLPAROSSA/volparossa-mail/commit/94f3162d4b4ab89da25295a347b51c7857df1fec) | 22,142 | `c8534912d4e139712e65f18b970736e8d6c80bb2ee8e1badf2a8af3c74ba26ba` |

In both trials the sender clicked Send and a fresh receiving Thunderbird profile opened the delivery over verified TLS. The original 429-byte draft and all eight attachment bytes survived. Each new composition had its own message ID and exact MIME digest. The first was 808 bytes and the second was 833 bytes. Both providers acknowledged each import. The add-on bytes matched across runs. [Sender run](https://github.com/VOLPAROSSA/volparossa-mail/actions/runs/44558522297)

In the second trial two receivers recovered one interrupted import while the Core socket was disconnected. They verified the two providers’ individual signed receipts after the combined confirmation was lost. The watch later reconnected, received another 430-byte message automatically and stopped cleanly. The receipts are in `owner-inbox/peer-mail.json`, `peer-draft.json` and `peer-compose.json` inside the ZIP. [Receiver run](https://github.com/VOLPAROSSA/volparossa-mail/actions/runs/44562762968)
