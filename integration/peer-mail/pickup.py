# SPDX-License-Identifier: GPL-3.0-only
"""Collect one owner-authorized mailbox and retain unfinished handoffs for the next invocation."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess

from accept import accept, connection, directory, invoke, read, require

MESSAGE_ID = re.compile(r"[a-f0-9]{64}")
MAX_PACKET = 4 * 1024 * 1024


def sync(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_dir(path):
    path.mkdir(mode=0o700, exist_ok=True)
    return directory(path)


def publish(path, data):
    # The owner holds the pickup lock. A crash can leave only this reserved temporary name.
    next_path = path.with_suffix(".next")
    with os.fdopen(os.open(next_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), "wb") as out:
        info = os.fstat(out.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600, "invalid private pickup journal")
        out.truncate(0)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(next_path, path)
    sync(path.parent)


HANDOFF = {"payload", "import-token", "pending.pb"}
CORE_TEMP = re.compile(r"\.tmp[A-Za-z0-9]{6}")
CIPHER_TEMP = re.compile(r"\.mailbox-ciphertext-[A-Za-z0-9]{6}")
CACHE_FILES = {".volparossa-owner-v1", ".volparossa-index-v1",
               ".volparossa-index-next-v1", ".volparossa-chunk-next-v1"}


def files(root, allowed):
    entries = list(directory(root).iterdir())
    for path in entries:
        meta = path.lstat()
        require(allowed(path.name) and stat.S_ISREG(meta.st_mode) and meta.st_uid == os.getuid()
                and meta.st_nlink == 1 and stat.S_IMODE(meta.st_mode) == 0o600,
                "unexpected file in pickup journal; original files retained")
    return entries


def remove_files(root, entries):
    for path in entries:
        path.unlink()
    root.rmdir()


def ciphertext_files(root):
    children = list(directory(root).iterdir())
    require(not children or children == [root / "cache"], "unexpected Core ciphertext staging directory")
    return files(children[0], lambda name: name in CACHE_FILES or MESSAGE_ID.fullmatch(name)) if children else []


def adopt(stage, pending, report=None):
    """Core publishes pending.pb last. An interrupted fetch has never acknowledged a provider."""
    if not os.path.lexists(stage) and report is None:
        return
    items = sorted(directory(stage).iterdir())
    if report is not None:
        expected = [row["message_id"] for row in report["pending"]]
        require(len(expected) == report["messages_fetched"] == report["messages_discovered"]
                and sorted(expected) == [item.name for item in items]
                and all((item / "pending.pb").is_file() for item in items), "incomplete Core fetch inventory")
    # Inspect every name before moving or deleting anything. Refuse unexpected owner files in place.
    actions = []
    for item in items:
        if CIPHER_TEMP.fullmatch(item.name):
            actions.append(("cipher", item, ciphertext_files(item)))
            continue
        require(MESSAGE_ID.fullmatch(item.name), "unexpected Core fetch entry; retained for inspection")
        entries = files(item, lambda name: name in HANDOFF or CORE_TEMP.fullmatch(name))
        target = pending / item.name
        if not (item / "pending.pb").exists():
            actions.append(("discard", item, entries))
            continue
        read(item / "pending.pb", 16384)
        require(len(read(item / "import-token", 32)) == 32, "invalid Core import token")
        packet = read(item / "payload", MAX_PACKET)
        if os.path.lexists(target):
            directory(target)
            require(read(target / "payload", MAX_PACKET) == packet, "conflicting Core handoff")
            actions.append(("discard", item, entries))
        else:
            actions.append(("adopt", item, entries))
    for action, item, entries in actions:
        if action == "adopt":
            item.rename(pending / item.name)
            sync(pending)
        elif action == "cipher":
            if (item / "cache").exists():
                remove_files(item / "cache", entries)
            item.rmdir()
        else:
            # Invalidate a duplicate before removing either of its two required inputs.
            marker = item / "pending.pb"
            if marker in entries:
                marker.unlink()
                sync(item)
                entries.remove(marker)
            remove_files(item, entries)
    stage.rmdir()
    sync(stage.parent)


def handoff_binding(item):
    return {name: hashlib.sha256(read(item / name, limit)).hexdigest()
            for name, limit in (("payload", MAX_PACKET), ("pending.pb", 16384), ("import-token", 32))}


def completed(item, cfg):
    proof = json.loads(read(item / "mail-readback.json", 8192))
    delivery = proof["delivery"]
    bound = handoff_binding(item)
    require(proof["handoff"] == bound and delivery["message_id"] == item.name
            and delivery["signal_packet_sha256"] == bound["payload"]
            and delivery["account_id"] == cfg["accountId"] and delivery["mailbox_id"] == cfg["mailboxId"]
            and MESSAGE_ID.fullmatch(delivery["message_sha256"]) and type(delivery["message_bytes"]) is int
            and 0 < delivery["message_bytes"] <= MAX_PACKET and isinstance(delivery["email_id"], str)
            and delivery["email_id"], "stored mailbox readback differs from the pending handoff")
    confirmation = json.loads(read(item / "confirmed.json", 32768))
    acks = confirmation["acknowledgement_receipts"]
    require(confirmation["operation"] == "mailbox_confirm_import" and confirmation["message_id"] == item.name
            and confirmation["consumer_import_attested"] is True and confirmation["acknowledged_providers"] == 2
            and isinstance(acks, list) and len(acks) == 2, "missing final provider receipts")
    providers = set()
    for ack in acks:
        key = ack["provider_key"]
        require(MESSAGE_ID.fullmatch(key) and key not in providers and ack["message_id"] == item.name
                and ack["version"] == 1 and re.fullmatch(r"(?:[0-9a-f]{2})+", ack["receipt"])
                and ack == json.loads(read(item / ("ack-" + key + ".json"), 32768)),
                "provider acknowledgement differs from its original")
        providers.add(key)
    return {"delivery": dict(delivery, acknowledged_providers=2), "confirmation": confirmation}


def retiring_files(item):
    return files(item, lambda name: name in HANDOFF | {"mail-readback.json", "confirmed.json"}
                 or re.fullmatch(r"ack-[0-9a-f]{64}\.json", name) or CORE_TEMP.fullmatch(name))


def import_pending(args, password, cfg, hold, work, attempted):
    count = 0
    pending, receipts, retired = work / "pending", work / "receipts", work / "retiring"
    for item in sorted(pending.iterdir()):
        require(MESSAGE_ID.fullmatch(item.name), "unexpected pickup item")
        if item.name in attempted:
            continue
        attempted.add(item.name)
        directory(item)
        receipt = receipts / (item.name + ".json")
        proof = item / "mail-readback.json"
        if not (os.path.lexists(proof) and os.path.lexists(item / "confirmed.json")):
            selected = argparse.Namespace(**(vars(args) | {"handoff": item}))

            def record(delivery):
                publish(proof, json.dumps({"handoff": handoff_binding(item), "delivery": delivery}).encode())

            try:
                accept(selected, password, cfg, hold, on_verified=record)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                continue
        result = completed(item, cfg)
        entries = retiring_files(item)
        if os.path.lexists(receipt):
            require(json.loads(read(receipt, 49152)) == result, "completed receipt differs from retained message")
        else:
            publish(receipt, json.dumps(result).encode())
            count += 1
        item.rename(retired)
        sync(pending)
        sync(work)
        remove_files(retired, [retired / path.name for path in entries])
        sync(work)
    return count


def pickup(args, password):
    state, profile = directory(args.state_dir), directory(args.profile)
    cfg = connection(args.connection, allow_missing_socket=True)
    grant = read(args.invitation, 16384)
    settings = json.dumps([str(profile), cfg, hashlib.sha256(grant).hexdigest()], sort_keys=True).encode()
    binding = hashlib.sha256(settings).hexdigest().encode("ascii")
    work = private_dir(state / "pickup")
    sync(state)
    fd = os.open(work, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        route = work / "route.sha256"
        if os.path.lexists(route):
            require(read(route, 64) == binding, "pickup journal belongs to another route or mailbox")
        else:
            publish(route, binding)
        invitation = work / "invitation"
        if os.path.lexists(invitation):
            require(read(invitation, 16384) == grant, "retained invitation differs")
        else:
            publish(invitation, grant)
        pending = private_dir(work / "pending")
        private_dir(work / "receipts")
        sync(work)
        stage, retired = work / "fetch", work / "retiring"
        if os.path.lexists(retired):
            remove_files(retired, retiring_files(retired))
            sync(work)
        adopt(stage, pending)
        attempted = set()
        count = import_pending(args, password, cfg, (fd,), work, attempted)
        try:
            fetched = invoke([args.core, "--control-socket", cfg["controlSocket"], "content", "mailbox", "fetch",
                              "--identity", cfg["identity"], "--passphrase-file", cfg["passphraseFile"],
                              "--invitation", invitation, "--output-dir", stage], "Core could not fetch the mailbox", hold=(fd,))
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"operation": "mailbox_pickup", "imported": count, "pending": sum(1 for _ in pending.iterdir()),
                    "listed_providers": None, "degraded": True, "fetch_complete": False}
        require(fetched["operation"] == "mailbox_fetch" and fetched["output_dir"] == str(stage)
                and fetched["provider_custody_consumed"] is False and fetched["consumer_import_attested"] is False
                and fetched["acknowledged_providers_per_message"] == 0 and type(fetched["listed_providers"]) is int
                and fetched["listed_providers"] in (1, 2) and fetched["degraded"] is (fetched["listed_providers"] != 2),
                "unexpected Core fetch result")
        adopt(stage, pending, fetched)
        count += import_pending(args, password, cfg, (fd,), work, attempted)
        remaining = sum(1 for _ in pending.iterdir())
        return {"operation": "mailbox_pickup", "imported": count, "pending": remaining,
                  "listed_providers": fetched["listed_providers"], "degraded": fetched["degraded"], "fetch_complete": True}
    finally:
        os.close(fd)
