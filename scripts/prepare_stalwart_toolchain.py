#!/usr/bin/env python3
"""Explicitly stage exact official Rust components in build/, without an installer."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import time
import tomllib
import urllib.request

from prepare_stalwart import NoRedirect, ROOT, digest, require, workspace_path

MAX_ENTRIES = 20_000
MAX_FILE = 512 * 1024 * 1024
MAX_UNPACKED = 2 * 1024 * 1024 * 1024


def pin():
    value = json.loads((ROOT / "third_party/stalwart-rust-toolchain.json").read_text())
    require(value["rust_version"] == "1.98.1" and
            value["host"] == "x86_64-unknown-linux-gnu" and
            value["date"] == "2026-09-03", "unexpected toolchain selection")
    require(set(value["components"]) == {"rustc", "cargo", "rust-std"},
            "unexpected toolchain components")
    return value


def fetch(url, path, size, sha256):
    require(url.startswith("https://static.rust-lang.org/dist/"), "unexpected origin")
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "VOLPAROSSA-source-preparer/1"})
    deadline = time.monotonic() + 600
    total = 0
    with opener.open(request, timeout=30) as response, path.open("xb") as output:
        require(response.geturl() == url, "unexpected redirected source")
        while True:
            require(time.monotonic() < deadline, "download deadline exceeded")
            chunk = response.read1(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= size, "download exceeds exact pinned size")
            output.write(chunk)
    require(total == size and digest(path) == sha256, "download checksum or size mismatch")


def preflight(archive, prefix):
    entries = {}
    total = 0
    for member in archive:
        name = member.name.removesuffix("/")
        parts = name.split("/")
        require(parts[0] == prefix and all(part not in ("", ".", "..") for part in parts),
                "unsafe archive path")
        require("\\" not in name and all(ord(c) >= 32 and ord(c) != 127 for c in name),
                "unsafe archive path")
        require(member.isdir() or member.isfile(), "archive links or special files forbidden")
        require(not member.issparse() and not member.pax_headers.get("linkpath"),
                "sparse or link metadata forbidden")
        require(0 <= member.size <= MAX_FILE and (not member.isdir() or member.size == 0),
                "archive entry size limit")
        total += member.size
        require(total <= MAX_UNPACKED and len(entries) < MAX_ENTRIES, "archive resource bound")
        relative = "/".join(parts[1:])
        require(relative not in entries, "duplicate archive member")
        entries[relative] = member
    require("" in entries and entries[""].isdir(), "missing archive root")
    for relative in entries:
        for parent in Path(relative).parents:
            if str(parent) == ".":
                break
            require(str(parent) in entries and entries[str(parent)].isdir(),
                    "missing or non-directory archive parent")
    return entries


def stage_component(archive_path, name, value, output):
    prefix = f"{name}-{value['rust_version']}-{value['host']}"
    component = name if name != "rust-std" else f"rust-std-{value['host']}"
    with tarfile.open(archive_path, "r:xz") as archive:
        entries = preflight(archive, prefix)
        require(component in entries and entries[component].isdir(), "component payload missing")
        for relative, member in entries.items():
            if not member.isfile():
                continue
            parts = Path(relative).parts
            if parts[0] == component and len(parts) > 1 and parts[1] != "manifest.in":
                target = output / "toolchain" / Path(*parts[1:])
            elif len(parts) == 1 and (parts[0].startswith("LICENSE") or parts[0] == "COPYRIGHT"):
                target = output / "notices" / name / parts[0]
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            mode = 0o755 if member.mode & 0o111 else 0o644
            with archive.extractfile(member) as source:
                if target.exists():
                    # Components may carry the same original notices. Never replace a file.
                    expected = hashlib.file_digest(source, "sha256").hexdigest()
                    require(target.is_file() and not target.is_symlink() and
                            target.stat().st_size == member.size and digest(target) == expected and
                            stat.S_IMODE(target.stat().st_mode) == mode,
                            "conflicting component payload")
                else:
                    with target.open("xb") as destination:
                        shutil.copyfileobj(source, destination, 64 * 1024)
                    require(target.stat().st_size == member.size, "truncated component")
                    target.chmod(mode)


def inventory(output):
    items = {}
    for root in (output / "toolchain", output / "notices"):
        require(root.is_dir() and not root.is_symlink(), "missing staged payload")
        for directory, dirs, names in os.walk(root, followlinks=False):
            for name in dirs + names:
                path = Path(directory) / name
                require(not path.is_symlink(), "staged payload contains symlink")
                if path.is_file():
                    info = path.stat()
                    require(info.st_nlink == 1 and stat.S_ISREG(info.st_mode), "invalid staged file")
                    items[path.relative_to(output).as_posix()] = {
                        "size": info.st_size, "mode": stat.S_IMODE(info.st_mode), "sha256": digest(path)}
                else:
                    require(path.is_dir(), "special staged payload")
    return items


def validate_manifest(path, value):
    require(path.stat().st_size == value["manifest_size"] and
            digest(path) == value["manifest_sha256"], "official manifest mismatch")
    official = tomllib.loads(path.read_text())
    require(official["date"] == value["date"], "manifest date mismatch")
    for name, item in value["components"].items():
        target = official["pkg"][name]["target"][value["host"]]
        url = (f"https://static.rust-lang.org/dist/{value['date']}/"
               f"{name}-{value['rust_version']}-{value['host']}.tar.xz")
        require(target["available"] and target["xz_url"] == url and
                target["xz_hash"] == item["sha256"], "official component pin mismatch")
    return official


def validate(output):
    output = workspace_path(output, fresh=False)
    value = pin()
    validate_manifest(output / "channel.toml", value)
    for name, item in value["components"].items():
        path = output / f"{name}.tar.xz"
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == item["size"] and
                digest(path) == item["sha256"], "component archive mismatch")
    report_path = output / "TOOLCHAIN_REPORT.json"
    require(report_path.is_file() and not report_path.is_symlink() and
            report_path.stat().st_size <= 8 * 1024 * 1024, "invalid toolchain report")
    report = json.loads(report_path.read_text())
    require(report["pin"] == value and report["files"] == inventory(output), "toolchain changed")
    return report


def prepare(output):
    output = workspace_path(output, fresh=True)
    value = pin()
    output.mkdir(mode=0o700)
    fetch(value["manifest_url"], output / "channel.toml", value["manifest_size"],
          value["manifest_sha256"])
    official = validate_manifest(output / "channel.toml", value)
    for name, item in value["components"].items():
        target = official["pkg"][name]["target"][value["host"]]
        archive = output / f"{name}.tar.xz"
        print(f"Fetching pinned {name} ({item['size']} bytes)", flush=True)
        fetch(target["xz_url"], archive, item["size"], item["sha256"])
        stage_component(archive, name, value, output)
    report = {"version": 1, "pin": value, "files": inventory(output),
              "upstream_installer_executed": False, "system_installation": False,
              "stalwart_compiled": False, "server_executed": False}
    with (output / "TOOLCHAIN_REPORT.json").open("x") as target:
        json.dump(report, target, indent=2, sort_keys=True)
        target.write("\n")
    return validate(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate:
        require(not args.fetch and args.output is None, "offline validation is a separate mode")
        report = validate(args.validate)
    else:
        require(args.fetch and args.output is not None, "explicit --fetch and fresh --output required")
        report = prepare(args.output)
    print(json.dumps({"toolchain_staged_and_validated": True, "rust_version": "1.98.1",
                      "files": len(report["files"]), "server_executed": False}))


if __name__ == "__main__":
    main()
