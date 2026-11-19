#!/usr/bin/python3 -I
# SPDX-License-Identifier: GPL-3.0-only
"""Thunderbird native messaging bridge to the owner's address-bound peer sender."""

import base64
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

OWNER = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "volparossa/mail"
SENDER = Path(__file__).resolve().with_name("send_selected.py")
MAX_MIME = 4 * 1024 * 1024
MAX_STORED = MAX_MIME + 65536
MAX_FRAME = 4 * ((MAX_STORED + 2) // 3) + 512
# Thunderbird records these headers in its local draft store. They can contain
# owner-side folder names, labels and identity keys, outside the correspondence.
LOCAL_HEADERS = {
    b"x-mozilla-status",
    b"x-mozilla-status2",
    b"x-mozilla-keys",
    b"x-mozilla-draft-info",
    b"x-account-key",
    b"x-identity-key",
    b"fcc",
    b"x-mozilla-fcc",
    b"x-mozilla-identity-key",
    b"x-mozilla-account-key",
    b"x-mozilla-news-host",
}
ATTACHMENT_MARKERS = {"x-mozilla-cloud-part", "x-mozilla-external-attachment-url", "x-mozilla-altered"}
MAY_HAVE_PLACEHOLDER = re.compile(
    b"^(?:" + b"|".join(name.encode("ascii") for name in sorted(ATTACHMENT_MARKERS)) + b"):", re.I | re.M
)


class IncompleteAttachment(ValueError):
    pass


def receive(stream):
    header = stream.read(4)
    if len(header) != 4:
        raise ValueError("incomplete frame")
    (length,) = struct.unpack("=I", header)
    if not 0 < length <= MAX_FRAME:
        raise ValueError("frame size")
    payload = stream.read(length)
    if len(payload) != length:
        raise ValueError("incomplete request")
    request = json.loads(payload)
    if (
        not isinstance(request, dict)
        or set(request) != {"version", "operation", "message"}
        or type(request["version"]) is not int
        or request["version"] != 1
        or request["operation"] != "send_selected_draft"
        or not isinstance(request["message"], str)
    ):
        raise ValueError("unsupported request")
    mime = base64.b64decode(request["message"], validate=True)
    if not 0 < len(mime) <= MAX_STORED:
        raise ValueError("draft size")
    message = correspondence(mime)
    if len(message) > MAX_MIME:
        raise ValueError("message size")
    return message


def correspondence(draft):
    # Retain each original header octet and the entire MIME body, including its
    # line endings and transfer encoding. Do not reserialize the message.
    boundary = re.search(rb"\r?\n\r?\n", draft[:65540])
    if boundary is None or boundary.start() > 65536:
        raise ValueError("missing bounded mail headers")
    # Filelink drafts and detached attachments can retain a local filename,
    # provider account or provider-specific data instead of the file bytes.
    # A literal header name quoted in ordinary body text is safe to preserve.
    if MAY_HAVE_PLACEHOLDER.search(draft):
        parsed = BytesParser(policy=policy.compat32).parsebytes(draft)
        for part in parsed.walk():
            if part.defects or ATTACHMENT_MARKERS.intersection(name.lower() for name in part.keys()):
                raise IncompleteAttachment()
    fields = []
    keep = None
    for line in draft[: boundary.start()].splitlines(keepends=True):
        if not line.startswith((b" ", b"\t")):
            name, separator, _ = line.partition(b":")
            if not separator or not re.fullmatch(rb"[!-9;-~]+", name):
                raise ValueError("malformed mail header")
            keep = name.lower() not in LOCAL_HEADERS
        elif keep is None:
            raise ValueError("invalid continuation")
        if keep:
            fields.append(line)
    if not fields:
        raise ValueError("draft has no correspondence headers")
    # The separator begins at the line ending of the final stored field, which
    # may itself have been local metadata.
    headers = b"".join(fields).rstrip(b"\r\n")
    return headers + draft[boundary.start() :]


def deliver(mime):
    # Each saved original has a stable operation ID across popup and process restarts.
    operation = hashlib.sha256(b"VOLPAROSSA Thunderbird saved draft v1\0" + mime).hexdigest()
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [sys.executable, "-I", "-B", SENDER, "--owner", OWNER, "--id", operation, "--stdin"],
            input=mime,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=1300,
        )
        if not 0 < output.tell() <= 8192:
            return {"status": "unconfirmed"}
        output.seek(0)
        reply = json.load(output)
    if result.returncode == 3 and reply.get("status") == "ordinary_mail_required":
        return {"status": "ordinary_mail_required"}
    if (
        result.returncode == 0
        and reply.get("status") == "provider_custody"
        and reply.get("confirmed_providers") == 2
        and re.fullmatch("[0-9a-f]{64}", reply.get("message_id", ""))
    ):
        return {"status": "in_custody", "confirmed_providers": 2, "message_id": reply["message_id"]}
    return {"status": "unconfirmed"}


def main():
    os.umask(0o077)
    try:
        reply = deliver(receive(sys.stdin.buffer))
    except IncompleteAttachment:
        reply = {"status": "attachment_unavailable"}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError, subprocess.SubprocessError):
        reply = {"status": "unconfirmed"}
    encoded = json.dumps(reply).encode("ascii")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)) + encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
