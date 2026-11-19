# SPDX-License-Identifier: GPL-3.0-only
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.util.spec_from_file_location(
    "peer_draft_host", Path(__file__).resolve().parents[1] / "integration/peer-mail/thunderbird-host.py"
)
host = importlib.util.module_from_spec(loader)
loader.loader.exec_module(host)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import peer_draft_install as installer


class PeerDraft(unittest.TestCase):
    def test_registration_reuses_the_selected_runtime_and_preserves_other_files(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, HOME=temp):
            first, second = (Path(temp) / name for name in ("node-a", "node-b"))
            for node in (first, second):
                node.write_text("#!/bin/sh\nexit 0\n")
                node.chmod(0o700)
            manifest = installer.register(first)
            runtime = manifest.with_suffix(".node")
            self.assertEqual(runtime.readlink(), first)
            self.assertEqual(installer.register(second), manifest)
            self.assertEqual(runtime.readlink(), second)
            manifest.write_text("existing owner registration")
            with self.assertRaises(ValueError):
                installer.register(first)
            self.assertEqual(runtime.readlink(), second)
            self.assertEqual(manifest.read_text(), "existing owner registration")

    def test_only_embedded_attachments_can_leave_the_owner(self):
        prefix = b'Content-Type: multipart/mixed; boundary="draft"\r\n\r\n--draft\r\n'
        part = b"Content-Type: application/octet-stream\r\n"
        tail = b"\r\nplaceholder\r\n--draft--\r\n"
        for field in (b"X-Mozilla-Cloud-Part: cloudFile;\r\n provider=local", b"x-mozilla-external-attachment-url: file:///example",
                      b"X-Mozilla-Altered: AttachmentDeleted"):
            with self.subTest(field=field):
                with self.assertRaises(host.IncompleteAttachment):
                    host.correspondence(prefix + part + field + b"\r\n" + tail)
                quoted = prefix + b"Content-Type: text/plain\r\n\r\n" + field + tail
                self.assertEqual(host.correspondence(quoted), quoted)

    def test_local_headers_and_their_continuations_stay_on_the_sender(self):
        for nl in (b"\r\n", b"\n"):
            lines = [b"From: Film <sender@example.test>", b"Subject: A", b" B"]
            body = nl.join([b"", b"X-Mozilla-Keys: body text", b"\x00\xff", b""])
            message = nl.join(lines + [body])
            for pos in (0, 1, 3):
                draft = lines[:pos] + [b"x-MOZILLA-Keys: internal", b" more labels"] + lines[pos:]
                self.assertEqual(host.correspondence(nl.join(draft + [body])), message)
            self.assertEqual(host.correspondence(message), message)

    def test_malformed_or_incomplete_native_frames_do_not_start_a_send(self):
        for frame in (b"", struct.pack("=I", host.MAX_FRAME + 1), struct.pack("=I", 100) + b"x"):
            with self.assertRaises(ValueError):
                host.receive(io.BytesIO(frame))
        for source in (b"\tstandalone\r\n\r\nhello", b"x" * 66000 + b"\r\n\r\n"):
            raw = json.dumps(
                {
                    "version": 1,
                    "operation": "send_selected_draft",
                    "message": base64.b64encode(source).decode(),
                }
            ).encode()
            with self.assertRaises(ValueError):
                host.receive(io.BytesIO(struct.pack("=I", len(raw)) + raw))
