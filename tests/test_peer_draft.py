# SPDX-License-Identifier: GPL-3.0-only
import base64
import importlib.util
import io
import json
from pathlib import Path
import struct
import unittest

loader = importlib.util.spec_from_file_location(
    "peer_draft_host", Path(__file__).resolve().parents[1] / "integration/peer-mail/thunderbird-host.py"
)
host = importlib.util.module_from_spec(loader)
loader.loader.exec_module(host)


class PeerDraft(unittest.TestCase):
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
