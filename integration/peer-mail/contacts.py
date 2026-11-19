#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Manage the owner's address-to-Signal pairings from independently authenticated contact details."""
import argparse
from email.errors import MessageError
from email.headerregistry import Address
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from send_selected import BOOK_LIMIT, SIGNAL, address_book, contact_route, core_settings, key, object_pairs
from deposit import HEX, checked_executable, flush_directory, owned, private, read


class ContactError(ValueError):
    pass


def mailbox(text):
    if not text or text != text.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ContactError("invalid_address")
    address = Address(addr_spec=text)
    return address.addr_spec, key(address)


def checked(command, reason):
    child = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=45)
    if child.returncode or not 0 < len(child.stdout) <= 8192:
        raise ContactError(reason)
    return json.loads(child.stdout)


def publish(owner, data):
    staging = owner / ".address-book.next"
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "wb") as out:
        owned(os.fstat(out.fileno()))
        out.truncate(0)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(staging, owner / "address-book.json")
    flush_directory(owner)


def pair(args, address, previous, book):
    owner_key, fingerprint = args.core_owner_key, args.trusted_bundle_sha256
    if not HEX.fullmatch(owner_key):
        raise ContactError("invalid_identity_fingerprint")
    profile = args.profile.absolute()
    private(profile, directory=True)
    bundle, invitation = args.peer_bundle.absolute(), args.invitation.absolute()
    if hashlib.sha256(read(bundle, 8192)).hexdigest() != fingerprint:
        raise ContactError("signal_fingerprint_mismatch")
    grant = read(invitation, 16384)
    cfg = core_settings(json.loads(read(args.core_config.absolute(), 8192), object_pairs_hook=object_pairs), owner_key)
    entry = dict(address=address, profile=str(profile), peer_bundle_sha256=fingerprint, core_owner_key=owner_key,
                 invitation_hex=grant.hex(), core=cfg)
    if previous:
        if (previous["profile"] != str(profile) or previous["peer_bundle_sha256"] != fingerprint
                or previous["core_owner_key"] != owner_key or contact_route(previous) != (grant, cfg)):
            raise ContactError("different_contact_already_paired")
    else:
        book["contacts"].append(entry)
    encoded = (json.dumps(book, ensure_ascii=True, indent=2) + "\n").encode()
    if len(encoded) > BOOK_LIMIT:
        raise ContactError("address_book_full")

    executable = checked_executable(Path(cfg["executable"]))
    for field in ("identity", "passphrase"):
        private(Path(cfg[field]))
    checked_grant = checked([executable, "--control-socket", cfg["socket"], "content", "mailbox", "verify-invitation",
                             "--invitation", invitation, "--owner-key", owner_key, "--identity", cfg["identity"],
                             "--passphrase-file", cfg["passphrase"]], "core_invitation_unverified")
    if (checked_grant.get("operation") != "mailbox_verify_invitation" or checked_grant.get("network_used") is not False
            or checked_grant.get("owner_key") != owner_key
            or checked_grant.get("invitation_sha256") != hashlib.sha256(grant).hexdigest()):
        raise ContactError("core_invitation_unverified")
    if args.node is None:
        raise ContactError("node_required")
    receipt = checked([args.node, "--no-warnings", SIGNAL / "cli.mjs", "pair", "--profile", profile,
                       "--peer-bundle", bundle, "--trusted-bundle-sha256", fingerprint,
                       "--core-owner-key", owner_key], "signal_pair_refused")
    if receipt != {"operation":"pair", "bundle_sha256":fingerprint}:
        raise ContactError("signal_pair_refused")
    return ("already_paired", None) if previous else ("paired", encoded)


def list_contacts(owner):
    path = owner.absolute()
    if os.path.lexists(path):
        private(path, directory=True)
        if os.path.lexists(path / "address-book.json"):
            book, _, contacts = address_book(path)
            return {"address":book["address"], "contacts":[
                {k:entry[k] for k in ("address","peer_bundle_sha256","core_owner_key")}
                for entry in contacts.values()
            ]}
    return {"address":None, "contacts":[]}


def update(args):
    owner = args.owner.absolute()
    address, target = mailbox(args.address)
    if not HEX.fullmatch(args.trusted_bundle_sha256):
        raise ContactError("invalid_identity_fingerprint")
    sender = mailbox(args.from_address) if args.action == "pair" else None
    if sender:
        owner.mkdir(mode=0o700, parents=True, exist_ok=True)
    elif not os.path.lexists(owner):
        return {"status":"not_paired", "address":address}
    private(owner, directory=True)
    fd = os.open(owner, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if os.path.lexists(owner / "address-book.json"):
            book, local, contacts = address_book(owner)
        elif sender:
            own_address, local = sender
            book, contacts = {"version":1, "address":own_address, "contacts":[]}, {}
        else:
            return {"status":"not_paired", "address":address}
        previous = contacts.get(target)
        if sender:
            if sender[1] != local or target == local:
                raise ContactError("owner_address_mismatch")
            status, data = pair(args, address, previous, book)
        elif previous:
            if previous["peer_bundle_sha256"] != args.trusted_bundle_sha256:
                raise ContactError("signal_fingerprint_mismatch")
            book["contacts"].remove(previous)
            status, data = "removed", (json.dumps(book, ensure_ascii=True, indent=2) + "\n").encode()
        else:
            status, data = "not_paired", None
        if data is not None:
            publish(owner, data)
        result = {"status":status, "address":address, "peer_bundle_sha256":args.trusted_bundle_sha256}
        if sender:
            result["core_owner_key"] = args.core_owner_key
        return result
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    registration = actions.add_parser("pair", help="authorize a verified contact for private mail")
    removal = actions.add_parser("remove", help="remove an address authorization and retain its Signal profile")
    listing = actions.add_parser("list", help="show authorized addresses and their public identity fingerprints")
    default = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "volparossa/mail"
    for action in (registration, removal, listing):
        action.add_argument("--owner", type=Path, default=default)
    for action in (registration, removal):
        action.add_argument("--address", required=True, help="recipient address confirmed through the trusted channel")
        action.add_argument("--trusted-bundle-sha256", required=True)
    registration.add_argument("--from", dest="from_address", required=True)
    registration.add_argument("--core-owner-key", required=True)
    registration.add_argument("--node", type=Path, default=shutil.which("node"))
    for name in ("profile", "peer-bundle", "invitation", "core-config"):
        registration.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(list_contacts(args.owner) if args.action == "list" else update(args)))
    except (OSError, ValueError, TypeError, KeyError, MessageError, subprocess.SubprocessError) as err:
        print(json.dumps({"status":str(err) if isinstance(err, ContactError) else "invalid_contact_or_state"}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
