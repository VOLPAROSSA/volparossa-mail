#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the Thunderbird add-on or register the matching Linux native mail host."""

import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/volparossa-peer-draft"
HOST = ROOT / "integration/peer-mail/thunderbird-host.py"
ADDON_ID = "volparossa-peer-draft@volparossa.local"
NAME = "org.volparossa.peer_mail"


def publish_once(source, destination):
    """Publish atomically, allowing another installer to finish the same file."""
    try:
        os.link(source, destination)
    except FileExistsError:
        if (destination.is_symlink() or not destination.is_file()
                or destination.stat().st_size != source.stat().st_size
                or destination.read_bytes() != source.read_bytes()):
            raise


def package(path):
    # Fixed ZIP metadata keeps the same extension sources reproducible.
    with tempfile.NamedTemporaryFile(dir=path.parent) as tmp:
        with zipfile.ZipFile(tmp, "w") as archive:
            files = [
                EXTENSION / name
                for name in ("manifest.json", "background.js", "draft.html", "draft.css", "draft.mjs")
            ]
            for source in [*files, ROOT / "LICENSE"]:
                entry = zipfile.ZipInfo(source.name)
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        tmp.flush()
        publish_once(Path(tmp.name), path)


def register(node):
    directory = Path.home() / ".mozilla/native-messaging-hosts"
    directory.mkdir(parents=True, exist_ok=True)
    resolved = Path(node).resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("select an executable Node runtime with --node")
    if not os.access(HOST, os.X_OK):
        raise ValueError("the native mail host must be executable")
    target = directory / (NAME + ".json")
    manifest = {
        "name": NAME,
        "description": "Deliver a local Thunderbird draft to a paired contact",
        "path": str(HOST),
        "type": "stdio",
        "allowed_extensions": [ADDON_ID],
    }
    data = (json.dumps(manifest, indent=2) + "\n").encode()
    exists = os.path.lexists(target)
    if exists and (target.is_symlink() or not target.is_file() or target.read_bytes() != data):
        raise ValueError("a different native host is already registered at " + str(target))
    # The desktop launcher and the onboarding shell can have different PATHs.
    # Retarget only our runtime link when the owner registers a new Node build.
    runtime = directory / (NAME + ".node")
    if os.path.lexists(runtime) and not runtime.is_symlink():
        raise ValueError("a different file already occupies " + str(runtime))
    if not runtime.is_symlink() or runtime.readlink() != resolved:
        with tempfile.TemporaryDirectory(dir=directory) as staging:
            link = Path(staging) / "node"
            link.symlink_to(resolved)
            os.replace(link, runtime)
    if not exists:
        with tempfile.NamedTemporaryFile(dir=directory) as tmp:
            tmp.write(data)
            tmp.flush()
            publish_once(Path(tmp.name), target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("package").add_argument("xpi", type=Path)
    registration = actions.add_parser("register")
    registration.add_argument("--node", type=Path, default=shutil.which("node"), help="Node executable used for pairing")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "package":
        package(args.xpi)
        print(args.xpi)
    else:
        if args.node is None:
            registration.error("provide --node or put the pairing runtime on PATH")
        print(register(args.node))
