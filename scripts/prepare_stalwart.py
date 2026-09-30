#!/usr/bin/env python3
"""Stage exact Stalwart sources under build/. Never compile, install or start them."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "af37a234981722493b74623a983581691d2b70b6"
ARCHIVE_SHA256 = "507ee7e79a799311f0e9a9be8971b03ec83d641b5046c4ca675756e4a0ca8f93"
ARCHIVE_SIZE = 8731436
PREFIX = "stalwart-" + REVISION
URL = "https://codeload.github.com/stalwartlabs/stalwart/tar.gz/" + REVISION
MAX_ENTRIES = 8192
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
BUILD_COMMAND = ["cargo", "build", "--locked", "--release", "-p", "stalwart",
                 "--no-default-features", "--features", "sqlite"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def manifest():
    value = json.loads((ROOT / "third_party/stalwart-source.json").read_text())
    require(value["revision"] == REVISION and value["archive"] == {
        "url": URL, "sha256": ARCHIVE_SHA256, "size": ARCHIVE_SIZE,
        "entries": 2010, "regular_files": 1714, "unpacked_bytes": 23855896,
    }, "source manifest pin mismatch")
    require(value["community_build"]["argv"] == BUILD_COMMAND, "build recipe mismatch")
    for relative, expected in value["preserved_notices"].items():
        path = ROOT / relative
        require(path.is_file() and not path.is_symlink(), "missing original notice")
        require(path.stat().st_size == expected["size"] and
                digest(path) == expected["sha256"], "original notice mismatch")
    return value


def workspace_path(path, *, fresh):
    """No symlinked ancestors and no broad workspace targets, including build itself."""
    path = Path(os.path.abspath(path))
    build = ROOT / "build"
    require(path != build and path.is_relative_to(build), "target must be a build child")
    current = ROOT
    for component in path.relative_to(ROOT).parts:
        current = current / component
        require(not current.is_symlink(), "symlinked target or ancestor")
        if current != path:
            require(current.is_dir(), "target parent must already exist")
    require(not path.exists() if fresh else path.is_dir(),
            "target must be fresh" if fresh else "stage does not exist")
    return path


def verify_archive(path):
    require(path.is_file() and not path.is_symlink(), "archive must be a regular file")
    require(path.stat().st_size == ARCHIVE_SIZE, "archive size mismatch")
    require(digest(path) == ARCHIVE_SHA256, "archive checksum mismatch")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("source redirects are not allowed")


def fetch_archive(path):
    request = urllib.request.Request(URL, headers={"User-Agent": "VOLPAROSSA-source-preparer/1"})
    opener = urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic() + 180
    total = 0
    with opener.open(request, timeout=30) as response, path.open("xb") as output:
        require(response.geturl() == URL, "unexpected source origin")
        while True:
            require(time.monotonic() < deadline, "source download deadline exceeded")
            # read1 returns available bytes without waiting for a whole chunk;
            # the deadline remains observable even on a slow trickling response.
            chunk = response.read1(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            require(total <= ARCHIVE_SIZE, "source download exceeds pinned size")
            output.write(chunk)
    verify_archive(path)


def archive_members(archive):
    """Preflight the entire archive before creating any source files; reject all links."""
    entries = {}
    total = 0
    count = 0
    for member in archive:
        count += 1
        require(count <= MAX_ENTRIES, "too many archive entries")
        name = member.name.removesuffix("/")
        parts = name.split("/")
        require(parts[0] == PREFIX and all(part not in ("", ".", "..") for part in parts),
                "unsafe archive path")
        require(not name.startswith("/") and "\\" not in name and
                all(ord(c) >= 32 and ord(c) != 127 for c in name), "unsafe archive path")
        require(member.isdir() or member.isfile(), "archive links and special files forbidden")
        require(not member.issparse() and not member.pax_headers.get("linkpath"),
                "sparse files and link metadata forbidden")
        require(0 <= member.size <= MAX_FILE_BYTES, "archive file size limit")
        require(not member.isdir() or member.size == 0, "directory with payload")
        total += member.size
        require(total <= MAX_TOTAL_BYTES, "archive unpacked size limit")
        relative = PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else ""
        require(relative not in entries, "duplicate archive entry")
        require(relative or member.isdir(), "archive root must be a directory")
        entries[relative] = member
    require("" in entries, "missing archive root")
    for relative in entries:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            require(parent.as_posix() in entries and entries[parent.as_posix()].isdir(),
                    "missing or non-directory archive parent")
            parent = parent.parent
    return entries, total


def extracted_hashes(archive, entries):
    files = {}
    for relative, member in entries.items():
        if member.isfile():
            with archive.extractfile(member) as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            require(len(data) == member.size, "truncated archive file")
            files[relative] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "mode": 0o755 if member.mode & 0o111 else 0o644}
    return files


def report_for(value, entries, unpacked, files):
    require(len(entries) == value["archive"]["entries"] and
            len(files) == value["archive"]["regular_files"] and
            unpacked == value["archive"]["unpacked_bytes"], "archive inventory mismatch")
    for relative, expected in value["source_files"].items():
        require({k: files[relative][k] for k in ("size", "sha256")} == expected,
                "critical source hash mismatch")
    return {"version": 1, "revision": REVISION, "release": value["release"],
            "archive_sha256": ARCHIVE_SHA256, "files": files, "entries": len(entries),
            "unpacked_bytes": unpacked, "source_only": True,
            "community_build_argv": BUILD_COMMAND, "compiled": False,
            "server_executed": False, "dependencies_downloaded": False,
            "host_services_or_network_modified": False}


def validate(stage):
    stage = workspace_path(stage, fresh=False)
    value = manifest()
    verify_archive(stage / "source.tar.gz")
    source = stage / "source"
    require(source.is_dir() and not source.is_symlink(), "invalid source directory")
    with tarfile.open(stage / "source.tar.gz", "r:gz") as archive:
        entries, unpacked = archive_members(archive)
        files = extracted_hashes(archive, entries)
    expected = report_for(value, entries, unpacked, files)
    report = stage / "SOURCE_REPORT.json"
    require(report.is_file() and not report.is_symlink() and report.stat().st_size <= 1024 * 1024,
            "invalid source report")
    require(json.loads(report.read_text()) == expected, "source report mismatch")
    observed = set()
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            relative = path.relative_to(source).as_posix()
            require(not path.is_symlink() and relative in entries, "unexpected source path")
            observed.add(relative)
            mode = path.stat().st_mode
            if relative in files:
                item = files[relative]
                require(stat.S_ISREG(mode) and path.stat().st_nlink == 1 and
                        stat.S_IMODE(mode) == item["mode"] and path.stat().st_size == item["size"] and
                        digest(path) == item["sha256"], "source file changed")
            else:
                require(stat.S_ISDIR(mode), "source directory changed")
    require(observed == set(entries) - {""}, "source inventory changed")
    return expected


def prepare(output, archive_path=None, fetch=False):
    value = manifest()
    require(bool(archive_path) != bool(fetch), "select exactly --archive or --fetch")
    output = workspace_path(output, fresh=True)
    if archive_path is not None:
        archive_path = Path(archive_path)
        verify_archive(archive_path)
    output.mkdir(mode=0o700)
    destination = output / "source.tar.gz"
    if fetch:
        fetch_archive(destination)
    else:
        copied = 0
        with archive_path.open("rb") as source, destination.open("xb") as target:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                copied += len(chunk)
                require(copied <= ARCHIVE_SIZE, "archive changed while copying")
                target.write(chunk)
        verify_archive(destination)
    with tarfile.open(destination, "r:gz") as archive:
        entries, unpacked = archive_members(archive)
        files = extracted_hashes(archive, entries)
        report = report_for(value, entries, unpacked, files)
        source = output / "source"
        source.mkdir(mode=0o700)
        for relative, member in entries.items():
            if relative and member.isdir():
                (source / relative).mkdir(parents=True, exist_ok=True, mode=0o755)
        for relative, item in files.items():
            path = source / relative
            with archive.extractfile(entries[relative]) as incoming, path.open("xb") as target:
                for chunk in iter(lambda: incoming.read(64 * 1024), b""):
                    target.write(chunk)
            path.chmod(item["mode"])
    with (output / "SOURCE_REPORT.json").open("x") as target:
        json.dump(report, target, indent=2, sort_keys=True)
        target.write("\n")
    return validate(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--fetch", action="store_true", help="explicitly fetch the pinned source archive")
    parser.add_argument("--validate", type=Path, help="offline verification of an existing stage")
    args = parser.parse_args()
    if args.validate:
        require(not (args.output or args.archive or args.fetch), "validate is a separate offline mode")
        result = validate(args.validate)
    else:
        require(args.output is not None, "--output is required")
        result = prepare(args.output, args.archive, args.fetch)
    print(json.dumps({"source_prepared_and_validated": True, "revision": REVISION,
                      "files": len(result["files"]), "compiled": False, "server_executed": False}))


if __name__ == "__main__":
    main()
