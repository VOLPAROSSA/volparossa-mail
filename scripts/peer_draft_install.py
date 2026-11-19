#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the Thunderbird add-on or register the matching Linux native mail host."""

import argparse
import json
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions/volparossa-peer-draft"
HOST = ROOT / "integration/peer-mail/thunderbird-host.py"
ADDON_ID = "volparossa-peer-draft@volparossa.local"
NAME = "org.volparossa.peer_mail"


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
        if (
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size == tmp.tell()
            and path.read_bytes() == Path(tmp.name).read_bytes()
        ):
            return
        os.link(tmp.name, path)


def register():
    directory = Path.home() / ".mozilla/native-messaging-hosts"
    directory.mkdir(parents=True, exist_ok=True)
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
    if target.is_file() and not target.is_symlink() and target.read_bytes() == data:
        return target
    with tempfile.NamedTemporaryFile(dir=directory) as tmp:
        tmp.write(data)
        tmp.flush()
        os.link(tmp.name, target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("package").add_argument("xpi", type=Path)
    actions.add_parser("register")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "package":
        package(args.xpi)
        print(args.xpi)
    else:
        print(register())
