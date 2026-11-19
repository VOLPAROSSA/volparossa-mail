#!/usr/bin/env python3
"""Export the tested Linux mailbox runtime for the Core QA guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
NODE_REV = "cdc1b38d40cb567b7ad0b39c86addf830a0af0ae"
SIGNAL_REV = "857c4dca03537dc5e395a5e1eda6bf18f59c3601"
NODE_SHA = "bc17c508ffeed0ec622934f9b7fa72f8e78da65350e63c3eceb56fa688aa5e12"
NATIVE = "prebuilds/linux-x64/@signalapp+libsignal-client.node"
# Hashes of the selected installed files independently checked against the exact SRI-locked npm archives.
PACKAGE_FILES = {
    "@signalapp/libsignal-client": "de4de1b8e96e2f526eb4853c0424e43aed52bea984fdfef359803382083eac97",
    "node-gyp-build": "98b8b1aad144158c151d5bb95671cc3404dcf873f075de02fff86ed7fadfd19f",
}
NOTICES = {
    "Node-LICENSE": (f"nodejs/node/{NODE_REV}/LICENSE", "148eacf7863ef4329224a29398623077200a27194aa075569faf4a0a85566ca5"),
    "libsignal-LICENSE": (f"signalapp/libsignal/{SIGNAL_REV}/LICENSE", "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"),
    "libsignal-desktop-acknowledgments.md": (f"signalapp/libsignal/{SIGNAL_REV}/acknowledgments/acknowledgments-desktop.md",
        "6d345aec8925a4e965f138cb1b3d0f8fb92522448448ed54abfb494cb9dde25d"),
}


def require(value, stage):
    if not value:
        raise ValueError(stage)


def encoded(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def digest(source):
    require(source.is_file() and not source.is_symlink(), "expected a regular runtime file")
    with source.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export(args):
    report = json.loads(args.compiled.read_bytes())
    peer = json.loads(args.peer.read_bytes())
    require(report["community_compiled"] is True and report["source_unchanged"] is True
            and peer["provider_acks"] == 2 and peer["exact_raw_bytes_verified"] is True
            and peer["messages_after_ack"] == 0, "a proven Mail owner trial is required")
    stalwart = Path(report["executable"])
    source = json.loads((ROOT / "third_party/stalwart-source.json").read_bytes())
    owner_file = stalwart.parents[2] / "BUILD_OWNER.json"
    owner = json.loads(owner_file.read_bytes())
    toolchain = json.loads((ROOT / "third_party/stalwart-rust-toolchain.json").read_bytes())
    require(owner["lock_sha256"] == digest(args.stalwart_lock)
            and owner["toolchain_manifest_sha256"] == toolchain["manifest_sha256"], "Stalwart build inputs differ")
    require(owner["source_revision"] == source["revision"] and owner["source_archive_sha256"] == source["archive"]["sha256"]
            and owner["features"] == ["sqlite"] and owner["default_features"] is False
            and stalwart.name == "stalwart" and digest(stalwart) == report["sha256"]
            and stalwart.stat().st_size == report["bytes"], "Stalwart source/build mismatch")
    node = args.node.resolve(strict=True)
    require(digest(node) == NODE_SHA and subprocess.check_output([node, "--version"]).strip() == b"v24.19.0",
            "Node release mismatch")
    files = {"bin/node": node, "bin/stalwart": stalwart, "provenance/stalwart-build.json": owner_file,
             "provenance/Cargo.lock": args.stalwart_lock}
    mail = ROOT / "integration/signal-mail"
    files["provenance/package-lock.json"] = mail / "package-lock.json"
    for name in ("stalwart-source.json", "stalwart-rust-toolchain.json"):
        files["provenance/" + name] = ROOT / "third_party" / name
    for path, notice in source["preserved_notices"].items():
        original = ROOT / path
        require(digest(original) == notice["sha256"], "Stalwart notice mismatch")
        files["notices/stalwart/" + original.name] = original
    files["notices/stalwart/NOTICE.md"] = ROOT / "third_party/stalwart/NOTICE.md"
    files["notices/MAIL-THIRD-PARTY.md"] = ROOT / "THIRD_PARTY_LICENSES.md"
    for package, fingerprint in PACKAGE_FILES.items():
        folder = args.modules / package
        if package.startswith("@"):
            selected = [p.relative_to(folder).as_posix() for p in (folder / "dist").rglob("*.js")] + ["package.json", NATIVE]
        else:
            selected = ["package.json", "index.js", "node-gyp-build.js", "LICENSE"]
        hashes = {name: digest(folder / name) for name in selected}
        require(hashlib.sha256(encoded(hashes)).hexdigest() == fingerprint, "npm runtime differs from its reviewed archive")
        files.update({f"node_modules/{package}/{name}": folder / name for name in selected})

    # Carry the license and attribution files shipped in the build's isolated registry and Git checkouts.
    cargo = owner_file.parent / "cargo"
    notices = {}
    for kind, pattern in (("registry", "registry/src/*/*"), ("git", "git/checkouts/*/*")):
        for package in sorted(cargo.glob(pattern)):
            if not package.is_dir():
                continue
            for path in package.rglob("*"):
                if not re.fullmatch(r"(?:LICEN[CS]E|COPYING|COPYRIGHT|NOTICE)(?:[._-][\w.-]+)?", path.name, re.I):
                    continue
                if ".git" in path.parts or not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
                    continue
                data = path.read_bytes()
                if b"\0" not in data:
                    sha = hashlib.sha256(data).hexdigest()
                    notices[f"{kind}/{package.parent.name}/{package.name}/{path.relative_to(package)}"] = sha
                    files.setdefault("notices/cargo/" + sha, path)
    require(notices, "missing Cargo dependency notices")
    revision = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "invalid Mail revision")
    sources = {"mail_revision": revision, "compiled_receipt_sha256": digest(args.compiled),
               "peer_receipt_sha256": digest(args.peer), "node_revision": NODE_REV, "node_archive_sha256":
               "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647",
               "libsignal_revision": SIGNAL_REV, "source_urls": {
                   "node": f"https://github.com/nodejs/node/tree/{NODE_REV}",
                   "libsignal": f"https://github.com/signalapp/libsignal/tree/{SIGNAL_REV}",
                   "stalwart": "https://github.com/stalwartlabs/stalwart/tree/" + owner["source_revision"]}}
    with tempfile.TemporaryDirectory() as folder:
        scratch = Path(folder)
        for name, (remote, expected) in NOTICES.items():
            path = scratch / name
            with urllib.request.urlopen("https://raw.githubusercontent.com/" + remote, timeout=25) as response:
                path.write_bytes(response.read(1_000_001))
            require(digest(path) == expected, "upstream notice mismatch")
            files["notices/" + name] = path
        path = scratch / "cargo-notices.json"
        path.write_bytes(encoded(notices))
        files["notices/cargo-index.json"] = path
        manifest = {name: {"sha256": digest(path), "bytes": path.stat().st_size} for name, path in files.items()}
        manifest_file = scratch / "runtime.json"
        manifest_file.write_bytes(encoded(dict(schema=1, files=manifest, **sources)) + b"\n")
        files["runtime.json"] = manifest_file
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=args.output.parent) as temporary:
            with tarfile.open(fileobj=temporary, mode="w:gz", compresslevel=2) as archive:
                for name, path in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.size = path.stat().st_size
                    info.mode = 0o555 if name.startswith("bin/") else 0o444
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
            temporary.flush()
            # Publish only complete bytes. The NamedTemporaryFile keeps its own cleanup path.
            os.link(temporary.name, args.output)
    print(json.dumps({"mail_revision": revision, "files": len(files), "sha256": digest(args.output)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiled", type=Path, default=ROOT / "build/compiled.json")
    parser.add_argument("--stalwart-lock", type=Path, default=ROOT / "build/stalwart-0.16.24/source/Cargo.lock")
    parser.add_argument("--peer", type=Path, default=ROOT / "build/owner-inbox/peer-mail.json")
    parser.add_argument("--modules", type=Path, default=ROOT / "integration/signal-mail/node_modules")
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "node"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/mail-custody-runtime.tar.gz")
    export(parser.parse_args())
