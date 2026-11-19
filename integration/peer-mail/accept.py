#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Import one owner-selected Core handoff into Stalwart, then acknowledge its providers."""

import argparse
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MAX_PACKET = 4 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def directory(path):
    meta = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(meta.st_mode)
            and meta.st_uid == os.getuid() and stat.S_IMODE(meta.st_mode) == 0o700,
            "expected a private owner directory")
    return path


def read(path, maximum):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        meta = os.fstat(stream.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.getuid() and meta.st_nlink == 1
                and stat.S_IMODE(meta.st_mode) == 0o600 and 0 < meta.st_size <= maximum,
                "invalid private input")
        data = stream.read(maximum + 1)
        require(len(data) == meta.st_size, "private input changed")
        return data


def invoke(args, operation, credential=None):
    # Subprocesses return private operational receipts. Only selected fields leave this adapter.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(args, input=credential, stdin=subprocess.DEVNULL if credential is None else None,
                                stdout=output, stderr=subprocess.DEVNULL, timeout=150, check=False)
        require(result.returncode == 0 and 0 < output.tell() <= 2 * 1024 * 1024, operation)
        output.seek(0)
        return json.load(output)


def accept(args, password):
    handoff, state, profile = (directory(p) for p in (args.handoff, args.state_dir, args.profile))
    require(re.fullmatch(r"[0-9a-f]{64}", handoff.name), "invalid Core message directory")
    directory(args.connection.parent)
    cfg = json.loads(read(args.connection, 8192))
    jmap = {"server", "username", "accountId", "mailboxId"}
    core = {"controlSocket": "socket", "identity": "identity", "passphraseFile": "passphrase"}
    require(isinstance(cfg, dict) and all(type(v) is str and v for v in cfg.values()), "invalid owner connection")
    if set(cfg) == jmap | {"coreConfig"}:
        route = Path(cfg["coreConfig"])
        directory(route.parent)
        owner = json.loads(read(route, 8192))
        require(isinstance(owner, dict) and all(type(owner.get(k)) is str and owner[k] for k in core.values()),
                "invalid owner Core configuration")
        cfg.update({key: owner[field] for key, field in core.items()})
    else:
        require(set(cfg) == jmap | core.keys(), "invalid owner connection")
    for key in ("controlSocket", "identity", "passphraseFile"):
        p = Path(cfg[key])
        require(p.is_absolute() and p.resolve(strict=True) == p, "invalid Core path")
    require(stat.S_ISSOCK(Path(cfg["controlSocket"]).stat().st_mode), "invalid Core control socket")
    require(len(read(handoff / "import-token", 32)) == 32, "invalid Core import token")
    read(handoff / "pending.pb", 16384)
    with os.fdopen(os.open(state / ".lock", os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600),
                   "rb") as lock:
        meta = os.fstat(lock.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_uid == os.getuid()
                and stat.S_IMODE(meta.st_mode) == 0o600, "invalid owner lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        packet = read(handoff / "payload", MAX_PACKET)
        key = hashlib.sha256(packet).hexdigest()
        with tempfile.TemporaryDirectory(prefix=".delivery-", dir=state) as temp:
            work = Path(temp)
            cipher = work / "packet"
            cipher.write_bytes(packet)
            mime = work / "message.eml"
            opened = invoke([args.node, HERE.parent / "signal-mail/cli.mjs", "open", "--profile", profile,
                             "--input", cipher, "--output", mime], "Signal did not open this delivery")
            message = read(mime, MAX_PACKET)
            digest = hashlib.sha256(message).hexdigest()
            require(opened.get("packet_sha256") == key and opened.get("message_sha256") == digest
                    and opened.get("message_bytes") == len(message), "Signal readback differed")
            # Pin the selected account together with its message, even across a second Core fetch.
            dest = work / "destination.json"
            dest.write_text(json.dumps({k: cfg[k] for k in ("server", "username", "accountId", "mailboxId")}))
            imported = invoke([args.node, HERE / "import-mime.mjs", dest, mime, state / (key + ".json"), key],
                              "Stalwart import unconfirmed; keep and retry the original handoff", password)
            require(imported.get("rawMessageVerified") is True and imported.get("messageSha256") == digest
                    and imported.get("size") == len(message) and imported.get("deliveryId") == key
                    and imported.get("accountId") == cfg["accountId"] and imported.get("mailboxId") == cfg["mailboxId"],
                    "Stalwart readback differed")
        confirm = invoke([args.core, "--control-socket", cfg["controlSocket"], "content", "mailbox", "confirm-import",
                          "--identity", cfg["identity"], "--passphrase-file", cfg["passphraseFile"],
                          "--pending", handoff / "pending.pb", "--import-token-file", handoff / "import-token",
                          "--imported-sha256", key],
                         "Stalwart verified the message; Core acknowledgement is incomplete, retain the handoff")
        require(confirm.get("operation") == "mailbox_confirm_import" and confirm.get("message_id") == handoff.name
                and confirm.get("consumer_import_attested") is True and confirm.get("acknowledged_providers") == 2,
                "Core did not confirm both providers")
        return {"message_id": handoff.name, "signal_packet_sha256": key, "message_sha256": digest,
                "message_bytes": len(message), "account_id": cfg["accountId"], "mailbox_id": cfg["mailboxId"],
                "email_id": imported["emailId"], "acknowledged_providers": 2}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "handoff", "connection", "state-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--node", default=shutil.which("node") or "node")
    parser.add_argument("--core", default=shutil.which("volparossa") or "volparossa")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if sys.stdin.isatty():
            password = getpass.getpass("Owner mailbox password: ").encode()
        else:
            password = sys.stdin.buffer.read(4098).removesuffix(b"\n")
        require(0 < len(password) <= 4096 and b"\n" not in password and b"\r" not in password,
                "expected a single mailbox password")
        result = accept(args, password)
        print(json.dumps(result))
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc) if type(exc) is ValueError else "Mail delivery incomplete (" + type(exc).__name__ + ")",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
