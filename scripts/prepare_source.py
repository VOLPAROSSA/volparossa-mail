#!/usr/bin/env python3
"""Produce an exact Thunderbird source overlay; never install or execute Thunderbird."""
# SPDX-License-Identifier: GPL-3.0-only

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "c747bb0160f873ab78692b2450612b232d38c30e"
BASE = f"https://raw.githubusercontent.com/thunderbird/thunderbird-desktop/{REVISION}/"
MAX_SOURCE_BYTES = 1024 * 1024
MENU_ANCHOR = '    <menuitem id="addonsManager"\n'
MENU = '''    <menuitem id="volparossaCooperativeAI"
              label="VOLPAROSSA AI (public cooperative tasks)"
              oncommand="window.openDialog('chrome://messenger/content/volparossa/mail-ai.html', '_blank', 'chrome,resizable,centerscreen,width=760,height=820');"/>
'''
EDITS = {
    "mail/base/content/messenger-menubar.inc.xhtml": [(MENU_ANCHOR, MENU + MENU_ANCHOR)],
    "mail/modules/moz.build": [('    "XULStoreUtils.sys.mjs",\n', '''    "VolparossaCooperativeCompute.sys.mjs",
    "VolparossaCooperativePanel.sys.mjs",
    "VolparossaMailAI.sys.mjs",
    "XULStoreUtils.sys.mjs",
''')],
    "mail/base/jar.mn": [('messenger.jar:\n', '''messenger.jar:
    content/messenger/volparossa/mail-ai.html       (content/volparossa/mail-ai.html)
    content/messenger/volparossa/mail-ai.mjs        (content/volparossa/mail-ai.mjs)
    content/messenger/volparossa/mail-ai.css        (content/volparossa/mail-ai.css)
''')],
}
ADDITIONS = {
    "mail/modules/VolparossaCooperativeCompute.sys.mjs": "integration/VolparossaCooperativeCompute.sys.mjs",
    "mail/modules/VolparossaCooperativePanel.sys.mjs": "integration/VolparossaCooperativePanel.sys.mjs",
    "mail/modules/VolparossaMailAI.sys.mjs": "integration/VolparossaMailAI.sys.mjs",
    "mail/base/content/volparossa/mail-ai.html": "integration/mail-ai.html",
    "mail/base/content/volparossa/mail-ai.mjs": "integration/mail-ai.mjs",
    "mail/base/content/volparossa/mail-ai.css": "integration/mail-ai.css",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def source_manifest():
    manifest = json.loads((ROOT / "patches/thunderbird-source.json").read_text())
    require(manifest["revision"] == REVISION, "unexpected Thunderbird revision")
    require(set(manifest["files"]) == set(EDITS) | {".gecko_rev.yml"}, "unexpected source files")
    return manifest


def verified_source(path, expected, directory=None, download=False):
    if directory is not None:
        candidate = directory / path
        require(not candidate.is_symlink() and candidate.is_file(), "source must be a regular file")
        require(candidate.resolve().is_relative_to(directory.resolve()), "source escapes directory")
        require(candidate.stat().st_size <= MAX_SOURCE_BYTES, "source too large")
        data = candidate.read_bytes()
    else:
        require(download, "use --source-directory or explicitly --download")
        request = urllib.request.Request(BASE + path, headers={"User-Agent": "VOLPAROSSA-source-preparer/1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            require(response.geturl() == BASE + path, "unexpected source redirect")
            data = response.read(MAX_SOURCE_BYTES + 1)
    require(len(data) == expected["size"] <= MAX_SOURCE_BYTES, "source size mismatch")
    require(hashlib.sha256(data).hexdigest() == expected["sha256"], "source checksum mismatch")
    return data.decode("utf-8")


def transform(path, source):
    result = source
    for old, new in EDITS.get(path, []):
        require(result.count(old) == 1, f"source anchor mismatch: {path}")
        result = result.replace(old, new, 1)
    return result


def make_patch(originals):
    return "".join("".join(difflib.unified_diff(
        originals[path].splitlines(keepends=True), transform(path, originals[path]).splitlines(keepends=True),
        fromfile="a/" + path, tofile="b/" + path,
    )) for path in EDITS)


def prepare(output, directory=None, download=False):
    manifest = source_manifest()
    output = Path(output).absolute()
    build = ROOT / "build"
    require(output != build and output.is_relative_to(build), "output must be a build child")
    require(not output.exists() and not output.is_symlink(), "output must be fresh")
    require(not build.is_symlink(), "build must not be a symlink")
    require(output.parent.resolve().is_relative_to(build.resolve()), "output parent escapes build")
    source = {path: verified_source(path, expected, directory, download)
              for path, expected in manifest["files"].items()}
    patch = make_patch(source)
    require(patch == (ROOT / "patches/0001-cooperative-ai-tools.patch").read_text(), "committed patch mismatch")
    output.mkdir(parents=True, mode=0o700)
    files = {}
    for path, text in source.items():
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(transform(path, text))
        files[path] = hashlib.sha256(target.read_bytes()).hexdigest()
    for path, origin in ADDITIONS.items():
        data = (ROOT / origin).read_bytes()
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[path] = hashlib.sha256(data).hexdigest()
    (output / "SOURCE_REPORT.json").write_text(json.dumps({
        "version": 1, "thunderbird_revision": REVISION, "platform": manifest["platform"],
        "files": files, "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "full_thunderbird_build": False, "runtime_peer_execution_proven": False,
        "native_accounts_modified": False, "source_only": True,
    }, indent=2) + "\n")
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-directory", type=Path)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    require(not (args.source_directory and args.download), "choose one source mode")
    count = prepare(args.output, args.source_directory, args.download)
    print(json.dumps({"source_overlay_prepared": True, "files": count, "runtime_executed": False}))


if __name__ == "__main__":
    main()
